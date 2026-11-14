import json

import pytest

from flats import db, repo_user
from flats.importer import run_import
from flats.requirements import parse_requirements
from flats.web import create_app


def test_complete_brief_is_only_a_proposal_with_evidence():
    text = "Хочу студию или однокомнатную в центре до 50 тыс в месяц, на въезд 120 тысяч, с котом. Работаю из дома."
    result = parse_requirements(text, ["Центр", "Чуркин"])
    assert result["mode"] == "rules"
    assert result["filters"] == {"monthly_max": 50000, "move_in_max": 120000,
                                  "rooms": [0, 1], "pets_required": True, "districts": ["Центр"]}
    assert result["priority"] == "house"
    assert all(item["label"] and item["text"] and item["value"] is not None for item in result["recognized"])
    assert "Проверьте" in result["notice"]
    assert "не подтверждаются" in result["notes"][-1]


@pytest.mark.parametrize("text, expected", [
    ("Аренда не больше 50 тысяч, на въезд не более 120 000 рублей", {"monthly_max": 50000, "move_in_max": 120000}),
    ("50,5 тыс в месяц, на въезд 100.5 тыс", {"monthly_max": 50500, "move_in_max": 100500}),
    ("50\u202f000 ₽ в месяц, на въезд 120\u00a0000 руб.", {"monthly_max": 50000, "move_in_max": 120000}),
    ("Снять квартиру до50к", {"monthly_max": 50000}),
    ("В месяц до 50000, на въезд 120000", {"monthly_max": 50000, "move_in_max": 120000}),
    ("В месяц до 50 рублей", {"monthly_max": 50}),
    ("Бюджет 1.5k", {"monthly_max": 1500}),
    ("На въезд 120000 и аренда до 45000", {"monthly_max": 45000, "move_in_max": 120000}),
])
def test_explicit_money_units_and_upper_bounds(text, expected):
    assert parse_requirements(text)["filters"] == expected


@pytest.mark.parametrize("text", [
    "Бюджет 50", "У меня 50 тыс", "От 30 до 50 тыс в месяц", "40-50 тыс в месяц",
    "От 40 тыс до 50 тыс в месяц", "До -50 тыс в месяц", "До −50000 в месяц",
    "В месяц не 50000", "В месяц от 50000", "Минимум 50000 в месяц", "Не менее 50000 в месяц",
    "Больше 50000 в месяц", "Бюджет 50000, в месяц 60000", "До 0 руб в месяц",
    "До 1.01 руб в месяц", "До 1000000001 рублей в месяц", "Зарплата 50000 в месяц",
    "Зарплата до 50000 в месяц", "Комиссия до 50000 в месяц", "От 30 тыс. до 50 тыс в месяц",
    "До $1000 в месяц", "До 1000 долларов в месяц", "1000€ в месяц", "1000% в месяц",
])
def test_money_is_not_guessed_or_silently_weakened(text):
    assert "monthly_max" not in parse_requirements(text)["filters"]


@pytest.mark.parametrize("text, expected", [
    ("Нужна 1-3-комнатная", [1, 2, 3]), ("Нужна 1 или 3 комнаты", [1, 3]),
    ("Студия или двушка", [0, 2]), ("Однокомнатную", [1]),
    ("Не студию, а двухкомнатную", [2]), ("Нужна 10-комнатная", [10]),
    ("Без животных хочу 2 комнаты", [2]),
])
def test_room_alternatives_ranges_and_negation(text, expected):
    assert parse_requirements(text)["filters"]["rooms"] == expected


@pytest.mark.parametrize("text", ["Не студию", "Не нужна двушка", "От 2 комнат", "Не меньше 2 комнат", "Кроме студии"])
def test_room_exclusions_do_not_become_positive_constraints(text):
    assert "rooms" not in parse_requirements(text)["filters"]


def test_contradictory_rooms_and_districts_are_not_applied():
    result = parse_requirements("Хочу студию. Не студию. Район Центр, но не в центре", ["Центр"])
    assert "rooms" not in result["filters"]
    assert "districts" not in result["filters"]
    assert any("противоречат" in note for note in result["notes"])


@pytest.mark.parametrize("text", [
    "Студию не хочу", "Не рассматриваю ни студию, ни однушку", "Студию и однушку не хочу",
    "До 2 комнат, бюджет 50 тыс в месяц", "Не более 2 комнат",
    "Нужны 1, 2 или 3 комнаты", "Нужны 1, 2 или 3 комнаты или студия",
])
def test_scoped_negation_bounds_and_complex_room_lists_are_not_partial_filters(text):
    result = parse_requirements(text)
    assert "rooms" not in result["filters"]
    assert any("комнат" in note for note in result["notes"])


def test_negation_carries_across_conjoined_choices_but_not_a_new_requirement():
    result = parse_requirements("Не хочу студию и однушку, нужна двушка")
    assert result["filters"]["rooms"] == [2]
    result = parse_requirements("Кроме Центра и Чуркина", ["Чуркин", "Центр"])
    assert "districts" not in result["filters"]
    assert any("«Чуркин»" in note for note in result["notes"])
    assert "districts" not in parse_requirements("Центр не нужен", ["Центр"])["filters"]
    assert "districts" not in parse_requirements("Центр, Чуркин не нужны", ["Центр", "Чуркин"])["filters"]
    assert "districts" not in parse_requirements("Кроме Центра, Чуркина", ["Чуркин", "Центр"])["filters"]
    assert "pets_required" not in parse_requirements("Нельзя с животными")["filters"]


def test_trip_preset_proposes_commute_priority():
    result = parse_requirements("Ищу однокомнатную до 50 тыс в месяц. Езжу в ДВФУ по будням, хочу проверить дорогу.")
    assert result["priority"] == "commute"
    assert result["filters"] == {"monthly_max": 50000, "rooms": [1]}


@pytest.mark.parametrize("text, expected", [
    ("Живу с кошкой", True), ("У меня есть собака", True),
    ("Без животных", False), ("Животных нет", False),
    ("Нужна квартира с животными", True),
])
def test_pets_requirement_preserves_explicit_negation(text, expected):
    assert parse_requirements(text)["filters"]["pets_required"] is expected


@pytest.mark.parametrize("text", ["Не с котом", "Не без животных", "С котом, но без животных", "Животные не важны"])
def test_ambiguous_pets_does_not_toggle_the_form(text):
    assert "pets_required" not in parse_requirements(text)["filters"]


def test_districts_are_only_existing_catalog_names_and_workplace_is_not_housing():
    result = parse_requirements("Работаю в центре, квартира на Первой речке или на Чуркине, не на Седанке", ["Центр", "Первая речка", "Чуркин", "Седанка"])
    assert result["filters"]["districts"] == ["Первая речка", "Чуркин"]
    assert any("Исключение района «Седанка»" in note for note in result["notes"])
    assert any("не район поиска квартиры" in note for note in result["notes"])
    assert "districts" not in parse_requirements("Ищу на Патрокле", ["Центр"])["filters"]
    assert parse_requirements("Без животных в центре", ["Центр"])["filters"]["districts"] == ["Центр"]


def test_unknown_constraints_and_multiple_priorities_are_explicit():
    result = parse_requirements("Нужна тихая квартира у школы. Работаю из дома, важна дорога до работы")
    assert result["filters"] == {} and result["priority"] is None
    assert any("несколько приоритетов" in note for note in result["notes"])
    assert "школа рядом" in result["notes"][-1]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("FLATS_PUBLIC_DEMO", raising=False)
    path = tmp_path / "requirements.db"
    conn = db.connect(path)
    db.init_db(conn)
    run_import(conn, [{"source": "demo", "source_id": "1", "district": "Центр", "price": 45000}])
    conn.close()
    app = create_app(path)
    app.testing = True
    return app.test_client()


def test_api_preview_does_not_persist_profile_or_decisions(client):
    conn = db.connect(client.application.config["DB_PATH"])
    before = repo_user.get_profile(conn)
    response = client.post("/api/requirements", json={"text": "В центре, в месяц до 50000 с котом"})
    assert response.status_code == 200
    assert response.get_json()["filters"] == {"monthly_max": 50000, "pets_required": True, "districts": ["Центр"]}
    assert repo_user.get_profile(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("payload", [None, [], "text", {}, {"text": 50}, {"text": True},
                                      {"text": ""}, {"text": " "}, {"text": "x" * 1201},
                                      {"text": "до 50000", "filters": {}}, {"text": ["до 50000"]}])
def test_api_rejects_invalid_payloads(client, payload):
    response = client.post("/api/requirements", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 400 and response.get_json()["error"]


def test_api_rejects_non_json_malformed_and_oversized_requests(client):
    assert client.post("/api/requirements", data="text").status_code == 400
    assert client.post("/api/requirements", data="{", content_type="application/json").status_code == 400
    assert client.post("/api/requirements", data=b"{\xff}", content_type="application/json").status_code == 400
    assert client.post("/api/requirements", json={"text": "x" * 8200}).status_code == 413


def test_api_enforces_public_csrf_and_keeps_visitors_independent(tmp_path, monkeypatch):
    monkeypatch.setenv("FLATS_PUBLIC_DEMO", "1")
    monkeypatch.setenv("FLATS_SECRET_KEY", "requirements-test-only")
    app = create_app(tmp_path / "public.db")
    a, b = app.test_client(), app.test_client()
    a.get("/advisor")
    with a.session_transaction() as session:
        headers = {"X-CSRF-Token": session["csrf_token"]}
    payload = {"text": "Аренда до 50000"}
    assert a.post("/api/requirements", json=payload).status_code == 403
    assert b.post("/api/requirements", json=payload, headers=headers).status_code == 403
    assert a.post("/api/requirements", json=payload, headers=headers).status_code == 200
    assert 'value="50000"' not in a.get("/advisor").get_data(as_text=True)
    assert 'value="50000"' not in b.get("/advisor").get_data(as_text=True)
