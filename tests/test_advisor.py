import json
from dataclasses import replace

import pytest
import requests

from flats import advisor, db, repo_houses, repo_user, trips
from flats.house_identity import normalize_house_address
from flats.importer import run_import
from flats.web import create_app, pages


def offer(source_id, **changes):
    return {"source": "farpost", "source_id": source_id, "url": f"https://example.com/{source_id}",
            "address": f"Адрес {source_id}", "district": "Центр", "rooms": 1,
            "price": 30000, "deposit_raw": "Без залога", "commission_raw": "Без комиссии",
            "prepayment_raw": "за 1 месяц", "utilities_raw": "Включены", "pets_raw": "можно",
            "description": "Квартира у парка", "last_seen": "2026-11-13T11:00:00Z", **changes}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in ("FLATS_LLM_BASE_URL", "FLATS_LLM_MODEL", "FLATS_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "advisor.db"
    conn = db.connect(path)
    db.init_db(conn)
    run_import(conn, [offer("a", address="Тестовая 1", lat=0, lon=0),
                      offer("b", price=45000, rooms=2, deposit_raw=None, prepayment_raw=None, pets_raw=None),
                      offer("expensive", price=90000), offer("skip", price=25000),
                      offer("g-a", group_id="g", price=35000, commission_raw="35 000 ₽"),
                      offer("g-b", group_id="g", price=37000)])
    repo_user.decide(conn, "farpost", "skip", "skip")
    app = create_app(path)
    app.testing = True
    yield conn, app.test_client()
    conn.close()


def ask(conn, **changes):
    return advisor.answer(conn, {"question": "Подбери квартиру по бюджету", **changes})


def test_retrieval_filters_and_decisions_are_preserved(env):
    conn, _ = env
    before = repo_user.get_profile(conn)
    decisions = repo_user.current_decisions(conn)
    result = ask(conn, filters={"monthly_max": 50000, "rooms": [1], "pets_required": True},
                 decisions=[["farpost", "skip", "skip"]])
    assert result["mode"] == "retrieval" and result["model_used"] is False
    assert {r["offer_id"] for r in result["recommendations"]} == {"farpost:a", "farpost:g-b"}
    assert result["total_candidates"] == 2
    assert repo_user.get_profile(conn) == before
    assert repo_user.current_decisions(conn) == decisions
    assert "farpost:expensive" not in json.dumps(result)
    assert "farpost:skip" not in json.dumps(result)


def test_saved_context_keeps_exact_source_costs_and_dates(env):
    conn, _ = env
    result = ask(conn, scope="saved", offer_ids=["farpost:g-a"],
                 decisions=[["farpost", "g-a", "save"]])
    [item] = result["recommendations"]
    assert item["offer_id"] == "farpost:g-a" and item["key"] == "g"
    assert item["monthly_known"] == 35000 and item["move_in_known"] == 70000
    assert item["source_url"] == "https://example.com/g-a"
    assert item["last_seen"] == "2026-11-13T11:00:00Z"
    imported_at = conn.execute("SELECT finished_at FROM import_runs").fetchone()[0]
    listing = [fact for fact in result["evidence"] if fact["kind"] == "listing"]
    assert all(fact["url"] == item["source_url"] and fact["observed_at"] == item["last_seen"]
               and fact["imported_at"] == imported_at for fact in listing)
    assert "37 000" not in json.dumps(result, ensure_ascii=False)


def test_missing_browser_decisions_never_fall_back_to_database(env):
    conn, _ = env
    repo_user.decide(conn, "farpost", "a", "save")
    result = ask(conn)
    assert "farpost:skip" in {r["offer_id"] for r in result["recommendations"]}
    assert ask(conn, scope="saved")["recommendations"] == []
    with pytest.raises(ValueError, match="не находится в избранном"):
        ask(conn, scope="saved", offer_ids=["farpost:a"])


def test_browser_group_decisions_are_applied_before_candidate_selection(env):
    conn, _ = env
    choices = [["farpost", "g-a", "skip"]]
    result = ask(conn, filters={"monthly_max": 50000}, decisions=choices)
    assert result["total_candidates"] == 3
    assert not any(r["key"] == "g" for r in result["recommendations"])
    choices.append(["farpost", "g-b", "save"])
    result = ask(conn, filters={"monthly_max": 50000}, decisions=choices)
    assert result["total_candidates"] == 4
    assert "farpost:g-b" in {r["offer_id"] for r in result["recommendations"]}


def test_saved_scope_ranks_all_browser_favorites_before_limiting_recommendations(env):
    conn, _ = env
    run_import(conn, [offer("last-cheapest", price=10000)])
    ids = ["a", "b", "expensive", "skip", "g-a", "g-b", "last-cheapest"]
    result = ask(conn, scope="saved", decisions=[["farpost", key, "save"] for key in ids])
    assert result["total_candidates"] == len(ids)
    assert len(result["recommendations"]) == 3
    assert result["recommendations"][0]["offer_id"] == "farpost:last-cheapest"


def test_public_advisor_does_not_read_or_store_visitor_decisions(env, monkeypatch):
    conn, original_client = env
    monkeypatch.setenv("FLATS_PUBLIC_DEMO", "1")
    monkeypatch.setenv("FLATS_SECRET_KEY", "test-advisor-browser-decisions")
    app = create_app(original_client.application.config["DB_PATH"])
    app.testing = True
    client = app.test_client()
    client.get("/advisor")
    with client.session_transaction() as session:
        headers = {"X-CSRF-Token": session["csrf_token"]}
        visitor_id = session["visitor_id"]
    conn.execute("INSERT INTO visitor_decisions(visitor_id,source,source_id,action,created_at)"
                 " VALUES (?, 'farpost', 'a', 'save', '2026-11-14')", (visitor_id,))
    conn.commit()
    before = [tuple(row) for row in conn.execute("SELECT * FROM visitor_decisions")]
    payload = {"question": "Подбери квартиру по бюджету", "scope": "saved"}
    response = client.post("/api/advisor", json=payload, headers=headers)
    assert response.status_code == 200 and response.get_json()["recommendations"] == []
    payload["decisions"] = [["farpost", "g-a", "save"]]
    response = client.post("/api/advisor", json=payload, headers=headers)
    assert response.status_code == 200
    assert [r["offer_id"] for r in response.get_json()["recommendations"]] == ["farpost:g-a"]
    assert [tuple(row) for row in conn.execute("SELECT * FROM visitor_decisions")] == before
    assert repo_user.current_decisions(conn) == {("farpost", "skip"): "skip"}


def test_unknown_and_zero_values_stay_distinct(env):
    conn, _ = env
    result = ask(conn, scope="offers", offer_ids=["farpost:b"], priority="move_in")
    [item] = result["recommendations"]
    assert item["move_in_known"] == 45000
    assert "Предоплата не указана" in item["unknowns"]
    assert "Залог не указан" in item["unknowns"]
    assert "Разрешение на животных не указано" in item["unknowns"]
    assert any("неполная сумма" in reason["text"] for reason in item["reasons"])
    assert any(f["text"] == "Комиссия: 0 ₽" for f in result["evidence"])
    assert any(f["text"] == "Залог: не указано" for f in result["evidence"])


def test_house_guides_are_not_address_facts_and_routes_are_unknown(env):
    conn, _ = env
    result = ask(conn, priority="house")
    assert result["coverage"] == {"house": False, "route": False}
    assert any(f["kind"] == "guide" for f in result["evidence"])
    assert not any(f["kind"] == "house_fact" for f in result["evidence"])
    assert all("Сведения о доме по этому адресу не получены" in r["unknowns"] for r in result["recommendations"])
    result = ask(conn, priority="commute", question="Проверь дорогу до работы")
    assert result["coverage"]["route"] is False
    assert "не рассчитаны" in result["notice"]
    assert all("Длина и время маршрута не рассчитаны" in r["unknowns"] for r in result["recommendations"])


def attach_house(conn):
    hid = repo_houses.ensure_house(conn, normalize_house_address("Владивосток", "улица Тестовая 1"),
                                  address_display="улица Тестовая 1", created_at="2026-11-13T00:00:00Z")
    repo_houses.link_offer_house(conn, "farpost", "a", hid, match_status="exact", offer_address_raw="Тестовая 1",
                                match_basis="fixture", evidence_url="https://official.example/house/1", checked_at="2026-11-13T00:00:00Z")
    source = {"source_key": "registry:facts", "provider": "Реестр домов", "section": "facts",
              "source_url": "https://official.example/house/1", "source_type": "fixture",
              "match_status": "exact", "matched_address": "улица Тестовая 1", "checked_at": "2026-11-13T00:00:00Z",
              "fetched_at": "2026-11-13T00:00:00Z", "fetch_status": "ok"}
    sid = repo_houses.store_house_observation(conn, hid, {"source": source, "facts": {"construction_year": 1980}, "contacts": [], "events": []})
    conn.commit()
    return hid, sid, source


def test_exact_house_evidence_is_scoped_and_ranked(env):
    conn, _ = env
    hid, sid, _ = attach_house(conn)
    result = ask(conn, priority="house", question="Проверь год постройки дома")
    item = result["recommendations"][0]
    assert item["offer_id"] == "farpost:a" and result["coverage"]["house"] is True
    assert item["house_url"] == f"/house/{hid}" and item["house_status"] == "exact"
    assert item["reasons"][0] == {"id": f"offer:farpost:a:house:{sid}:construction_year", "text": "Год постройки по источнику: 1980"}
    assert not any(f["kind"] == "house_fact" and not f["id"].startswith("offer:farpost:a:") for f in result["evidence"])


def test_duplicate_source_text_is_one_reason_but_both_sources_remain(env):
    conn, _ = env
    hid, sid, source = attach_house(conn)
    source.update(source_key="other-registry:facts", source_url="https://other.example/house/1", provider="Другой реестр")
    second_id = repo_houses.store_house_observation(conn, hid, {
        "source": source, "facts": {"construction_year": 1980}, "contacts": [], "events": []})
    result = ask(conn, scope="offers", offer_ids=["farpost:a"], priority="house", question="Год постройки дома")
    reasons = result["recommendations"][0]["reasons"]
    assert sum(reason["text"] == "Год постройки по источнику: 1980" for reason in reasons) == 1
    assert len({reason["text"] for reason in reasons}) == len(reasons)
    evidence = [fact for fact in result["evidence"] if fact["kind"] == "house_fact"]
    assert {fact["id"] for fact in evidence} == {
        f"offer:farpost:a:house:{sid}:construction_year", f"offer:farpost:a:house:{second_id}:construction_year"}
    assert {fact["url"] for fact in evidence} == {"https://official.example/house/1", "https://other.example/house/1"}


def test_stale_house_link_never_reuses_prior_address_evidence(env):
    conn, _ = env
    attach_house(conn)
    conn.execute("UPDATE offers SET address='улица Тестовая 1А' WHERE source_id='a'")
    result = ask(conn, scope="offers", offer_ids=["farpost:a"], priority="house")
    [item] = result["recommendations"]
    assert item["house_url"] is None and item["house_status"] == "stale"
    assert result["coverage"]["house"] is False
    assert not any(f["kind"] == "house_fact" for f in result["evidence"])
    assert any("устарела" in text for text in item["unknowns"])


def test_failed_source_keeps_dated_evidence_and_reports_failure(env):
    conn, _ = env
    hid, _, source = attach_house(conn)
    source.update(checked_at="2026-11-14T00:00:00Z", fetched_at=None, fetch_status="error", error="unavailable")
    repo_houses.store_house_observation(conn, hid, {"source": source, "facts": None, "contacts": [], "events": []})
    result = ask(conn, scope="offers", offer_ids=["farpost:a"], priority="house")
    assert "Характеристики дома: Не удалось получить сведения из источника; актуальность не проверена." in result["recommendations"][0]["unknowns"]
    [fact] = [f for f in result["evidence"] if f["kind"] == "house_fact"]
    assert fact["url"] == source["source_url"] and fact["observed_at"].startswith("2026-11-13")


def test_district_filter_is_request_scoped_and_can_clear_stored_selection(env):
    conn, _ = env
    run_import(conn, [offer("other-district", district="Эгершельд")])
    before = replace(repo_user.get_profile(conn), districts=("Центр",))
    repo_user.save_profile(conn, before)
    result = ask(conn, filters={"districts": ["Эгершельд"]})
    assert [item["offer_id"] for item in result["recommendations"]] == ["farpost:other-district"]
    assert ask(conn, filters={"districts": []})["total_candidates"] == 6
    assert repo_user.get_profile(conn) == before


def test_advisor_reuses_completed_visitor_journeys_without_spending_api_calls(env, monkeypatch):
    from flask import g
    from flats import routing
    from flats.search import search

    conn, client = env
    monkeypatch.setattr(trips, "_JOBS", {})
    monkeypatch.setattr(trips, "_CURRENT", {})
    monkeypatch.setattr(trips, "_USAGE", {})
    conn.execute("UPDATE offers SET lat=43,lon=131 WHERE source_id='a'")
    conn.execute("UPDATE offers SET lat=43.1,lon=131 WHERE source_id='b'")
    conn.commit()
    schedule = {"places": [{"id": "work", "name": "Работа", "lat": 43.2, "lon": 131.2, "confirmed": True}],
                "legs": [{"id": "morning", "from": "home", "to": "work", "days": [0],
                          "departure": "08:00", "max_minutes": 60}]}
    trips.save_schedule(conn, schedule)
    job = trips.start_job(conn, search(conn, repo_user.get_profile(conn)))

    def provider(source, target, start_time):
        return {"status": "ok", "alternatives": [{"duration_seconds": 1800 if source["lat"] == 43 else 7200,
                "transfers": 0, "pedestrian": False, "walkway": ""}]}

    job = trips.step_job(conn, job["id"], provider=provider)
    assert job["status"] == "complete"
    monkeypatch.setattr(routing, "route", lambda *args, **kwargs: pytest.fail("Advisor must not call Routing API"))
    result = ask(conn, priority="commute", question="Проверь путь до работы")
    first = result["recommendations"][0]
    assert first["offer_id"] == "farpost:a" and result["coverage"]["route"] is True
    assert first["route"]["weekly_seconds"] == 1800 and first["route"]["week_start"] == job["week_start"]
    assert first["route"]["calculated_at"] and first["reasons"][0]["id"].endswith(":route:weekly")
    assert "farpost:b" not in json.dumps(result)
    assert "оценка" in result["notice"]
    assert ask(conn, scope="offers", offer_ids=["farpost:b"], priority="commute")["recommendations"] == []
    with client.application.test_request_context():
        g.visitor_id = "another-browser"
        other = ask(conn, priority="commute", question="Проверь путь до работы")
        assert other["coverage"]["route"] is False
        assert not any(f["kind"] == "route" for f in other["evidence"])
    schedule["legs"][0]["departure"] = "09:00"
    trips.save_schedule(conn, schedule)
    assert ask(conn, priority="commute")["coverage"]["route"] is False


def test_off_topic_and_empty_results_do_not_invent_recommendations(env):
    conn, _ = env
    result = ask(conn, question="Напиши рецепт борща")
    assert result["recommendations"] == [] and result["model_used"] is False
    result = ask(conn, filters={"monthly_max": 1})
    assert result["total_candidates"] == 0 and result["recommendations"] == []


def test_default_viewing_question_and_retrieval_bounds(env):
    conn, _ = env
    run_import(conn, [offer(f"extra-{n}") for n in range(5)])
    result = ask(conn, question="С каких квартир начать просмотры и что нужно уточнить?")
    assert result["total_candidates"] == 10
    assert len(result["recommendations"]) == 3
    listing_sources = {f["url"] for f in result["evidence"] if f["kind"] == "listing"}
    assert len(listing_sources) == 5


class FakeHTTP:
    def __init__(self, transform=lambda value, context: value):
        self.calls = []
        self.transform = transform

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        context = json.loads(kwargs["json"]["messages"][1]["content"])
        candidate = context["candidates"][0]
        value = {"recommendations": [{"offer_id": candidate["offer_id"],
                                      "reason_fact_ids": [candidate["facts"][0]["id"]],
                                      "question_ids": [candidate["questions"][0]["id"]]}]}
        content = self.transform(value, context)

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": json.dumps(content)}}]}

        return Response()


def configure(monkeypatch):
    monkeypatch.setenv("FLATS_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("FLATS_LLM_MODEL", "configured-model")


def test_optional_provider_selects_only_retrieved_facts(env, monkeypatch):
    conn, _ = env
    configure(monkeypatch)
    http = FakeHTTP()
    result = advisor.answer(conn, {"question": "Подбери квартиру по бюджету"}, http=http)
    assert result["mode"] == "llm" and result["model_used"] is True
    url, request = http.calls[0]
    assert url == "http://127.0.0.1:8000/v1/chat/completions"
    assert request["timeout"] == (3, 15) and "Authorization" not in request["headers"]
    facts = {fact["id"]: fact for fact in result["evidence"]}
    for item in result["recommendations"]:
        assert all(reason["text"] == facts[reason["id"]]["text"] for reason in item["reasons"])
    assert "configured-model" not in json.dumps(result)


@pytest.mark.parametrize("bad", ["unknown", "cross_offer", "irrelevant", "guide", "duplicate", "prose", "shape"])
def test_unverifiable_provider_output_falls_back(env, monkeypatch, bad):
    conn, _ = env
    configure(monkeypatch)

    def transform(value, context):
        selection = value["recommendations"][0]
        if bad == "unknown":
            selection["offer_id"] = "invented:offer"
        elif bad == "cross_offer":
            selection["reason_fact_ids"] = [context["candidates"][1]["facts"][0]["id"]]
        elif bad == "irrelevant":
            selection["reason_fact_ids"] = [f"offer:{selection['offer_id']}:rooms"]
        elif bad == "guide":
            selection["reason_fact_ids"] = [context["guides"][0]["id"]]
        elif bad == "duplicate":
            value["recommendations"].append(selection)
        elif bad == "prose":
            selection["answer"] = "В этом доме никогда не отключают воду"
        else:
            return {"recommendations": "bad"}
        return value

    result = advisor.answer(conn, {"question": "Подбери квартиру по бюджету", "priority": "house" if bad == "guide" else "budget"}, http=FakeHTTP(transform))
    assert result["mode"] == "retrieval" and result["model_used"] is False
    assert "непроверяемый" in result["notice"]
    assert "никогда" not in json.dumps(result, ensure_ascii=False)


def test_provider_failure_never_exposes_key(env, monkeypatch, capsys):
    conn, _ = env
    configure(monkeypatch)
    monkeypatch.setenv("FLATS_LLM_API_KEY", "secret-never-output")

    class BrokenHTTP:
        def post(self, *args, **kwargs):
            raise requests.Timeout("secret-never-output")

    result = advisor.answer(conn, {"question": "Подбери квартиру по бюджету"}, http=BrokenHTTP())
    assert result["mode"] == "retrieval" and result["recommendations"]
    assert "secret-never-output" not in json.dumps(result) + str(capsys.readouterr())


def test_no_implicit_remote_provider(env, monkeypatch):
    conn, _ = env
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    http = FakeHTTP()
    result = advisor.answer(conn, {"question": "Подбери квартиру по бюджету"}, http=http)
    assert result["model_used"] is False and http.calls == []


@pytest.mark.parametrize("changes", [
    {"question": ""}, {"question": "x" * 1201}, {"question": 1}, {"priority": "anything"},
    {"priority": []}, {"priority": {}}, {"scope": []}, {"scope": {}},
    {"scope": "anything"}, {"scope": "offers"}, {"offer_ids": "farpost:a"},
    {"offer_ids": ["farpost:a"] * 2}, {"offer_ids": ["farpost:missing"]},
    {"offer_ids": [None]}, {"scope": "saved", "offer_ids": ["farpost:a"]},
    {"filters": {"monthly_max": True}}, {"filters": {"monthly_max": 0}},
    {"filters": {"move_in_max": -1}}, {"filters": {"move_in_max": 2.5}},
    {"filters": {"rooms": [True]}}, {"filters": {"rooms": [11]}}, {"filters": {"rooms": "1"}},
    {"filters": {"monthly_max": float("nan")}}, {"filters": {"move_in_max": float("inf")}},
    {"filters": {"pets_required": 1}}, {"filters": {"districts": ["Нет такого района"]}},
    {"filters": {"districts": [True]}}, {"filters": {"districts": [[]]}}, {"filters": {"districts": "Центр"}},
    {"base_url": "https://attacker.example"}, {"filters": None},
    {"decisions": None}, {"decisions": {}}, {"decisions": [None]},
    {"decisions": [["farpost", "a"]]}, {"decisions": [["farpost", "a", "save", True]]},
    {"decisions": [["", "a", "save"]]}, {"decisions": [["farpost", 1, "save"]]},
    {"decisions": [["farpost", "a", "like"]]}, {"decisions": [["farpost", "a", {}]]},
    {"decisions": [["farpost", "x" * 301, "save"]]},
    {"decisions": [["farpost", "a", "save"], ["farpost", "a", "skip"]]},
    {"scope": "saved", "offer_ids": ["farpost:a"], "decisions": [["farpost", "a", "skip"]]},
])
def test_api_rejects_invalid_inputs(env, changes):
    _, client = env
    response = client.post("/api/advisor", json={"question": "Подбери квартиру", **changes})
    assert response.status_code == 400 and response.get_json()["error"]


def test_api_bounds_json_and_request_filters_are_not_saved(env):
    conn, client = env
    assert client.post("/api/advisor", data="not json").status_code == 400
    assert client.post("/api/advisor", data="{", content_type="application/json").status_code == 400
    assert client.post("/api/advisor", json={"question": "x" * 17000}).status_code == 413
    before = replace(repo_user.get_profile(conn), monthly_max=50000)
    repo_user.save_profile(conn, before)
    result = client.post("/api/advisor", json={"question": "Подбери квартиру", "filters": {"monthly_max": None, "rooms": [0, 1, 1]}})
    assert result.status_code == 200 and result.get_json()["recommendations"]
    assert repo_user.get_profile(conn) == before


def test_advisor_page_context_and_exact_flat_validation(env, monkeypatch):
    _, client = env
    contexts = []
    monkeypatch.setattr(pages, "render_template", lambda name, **context: contexts.append((name, context)) or "ok")
    assert client.get("/advisor?key=g&o=farpost:g-a&q=Въезд&priority=move_in").status_code == 200
    name, context = contexts[-1]
    assert name == "advisor.html" and context["offer_ids"] == ["farpost:g-a"]
    assert context["scope"] == "offers" and context["query"] == "Въезд"
    assert context["flat_key"] == "g" and context["priority"] == "move_in"
    assert context["all_offer_count"] == 6 and context["llm_available"] is False
    assert client.get("/advisor?key=g&o=farpost:a").status_code == 400
    assert client.get("/advisor?key=missing").status_code == 404
    assert client.get("/advisor?o=farpost:missing").status_code == 400
    assert client.get("/advisor?scope=saved&o=farpost:a").status_code == 200
    assert contexts[-1][1]["scope"] == "saved" and contexts[-1][1]["offer_ids"] == ["farpost:a"]
    assert client.get("/flat/g").status_code == 200
    assert contexts[-1][1]["house_facts"] == [] and contexts[-1][1]["house_sources"] == []
