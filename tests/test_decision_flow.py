from urllib.parse import urlencode
from pathlib import Path

import pytest

from flats import db, place_context, repo_offers
from flats.cli import DEMO_FILES
from flats.importer import load_json, run_import
from flats.house_import import import_house_passports, load_house_bundle
from flats.web import create_app


@pytest.fixture
def real_client(tmp_path):
    path = tmp_path / "real.db"
    conn = db.connect(path)
    db.init_db(conn)
    run_import(conn, load_json(DEMO_FILES["real"]))
    conn.close()
    app = create_app(path)
    app.testing = True
    return app.test_client()


@pytest.fixture
def houses_client(real_client):
    conn = db.connect(real_client.application.config["DB_PATH"])
    archive = Path(__file__).resolve().parents[1] / "research/house-passports/2026-11-14/farposton-house-data-2026-11-14.zip"
    import_house_passports(conn, load_house_bundle(archive))
    conn.close()
    return real_client


def test_house_context_never_attaches_to_nearby_house():
    facts = place_context.house_facts("улица Гамарника 21")
    assert any(f["field"] == "commissioned_year" and f["value"] == 1974 for f in facts)
    assert not place_context.house_facts("улица Гамарника 21А")
    assert not place_context.house_facts("улица Гамарника 24")
    assert not place_context.house_facts(None)
    assert all(f["url"].startswith("https://") and f["observed_at"] for f in facts)


def test_real_house_page_separates_reported_data_from_current_status(real_client):
    html = real_client.get("/flat/3ca9c63dceaa8ae384349546").get_data(as_text=True)
    assert "1974" in html and "Атлант" in html
    assert "Актуальность не подтверждена" in html
    assert "18.11.2025" in html and "не действующий график" in html
    assert "Это не означает, что отключений нет" in html
    assert "Дом требует проверки" in html


def test_advisor_page_and_context_preserve_the_selected_offer(houses_client):
    real_client = houses_client
    selected = "farpost:139158750"
    html = real_client.get("/advisor?" + urlencode({"o": selected, "priority": "house"})).get_data(as_text=True)
    assert 'id="advisor-form"' in html and 'id="viewing-brief"' in html
    assert selected in html and 'data-scope="offers"' in html
    response = real_client.post("/api/advisor", json={"question": "Что известно о доме?",
        "scope": "offers", "offer_ids": [selected], "priority": "house"})
    assert response.status_code == 200
    data = response.get_json()
    assert [r["offer_id"] for r in data["recommendations"]] == [selected]
    assert data["coverage"] == {"house": True, "route": False}
    assert any(f["kind"] == "house_fact" and "1974" in f["text"] for f in data["evidence"])
    assert any(f["kind"] == "guide" for f in data["evidence"])
    assert data["recommendations"][0]["monthly_known"] == 30000
    assert "управляет" in data["recommendations"][0]["questions"][0]["text"]


def test_advisor_retrieves_full_house_archive_and_keeps_conflicting_sources(houses_client):
    selected = "farpost:133892276"
    response = houses_client.post("/api/advisor", json={
        "question": "Работаю из дома. Какая УК и что известно об отключениях и ремонте?",
        "scope": "offers", "offer_ids": [selected], "priority": "house"})
    assert response.status_code == 200
    data = response.get_json()
    [item] = data["recommendations"]
    assert item["house_url"].startswith("/house/") and item["house_status"] == "exact"
    assert any("разные управляющие организации" in text for text in item["unknowns"])
    facts = [fact for fact in data["evidence"] if fact["kind"] == "house_fact"]
    assert any("ТЕХНОЛОГИЯ КОМФОРТА" in fact["text"] for fact in facts)
    assert any("Технология управления" in fact["text"] for fact in facts)
    assert any("Публикаций об отключениях" in fact["text"] and "не число фактических" in fact["text"] for fact in facts)
    assert any("плана капремонта" in fact["text"] and "Выполнение работ планом не подтверждено" in fact["text"] for fact in facts)
    assert all(fact["id"].startswith(f"offer:{selected}:house:") and fact["url"].startswith("https://") and fact["observed_at"] for fact in facts)
    assert houses_client.get(item["house_url"]).status_code == 200


def test_advisor_without_imported_passports_does_not_claim_house_coverage(real_client):
    data = real_client.post("/api/advisor", json={"question": "Что известно о доме?",
        "scope": "offers", "offer_ids": ["farpost:139158750"], "priority": "house"}).get_json()
    assert data["coverage"]["house"] is False
    assert not any(fact["kind"] == "house_fact" for fact in data["evidence"])
    assert data["recommendations"][0]["house_url"] is None


def test_work_from_home_reasons_prioritize_management_outages_and_repairs(houses_client):
    data = houses_client.post("/api/advisor", json={"question": "Работаю из дома",
        "scope": "offers", "offer_ids": ["farpost:139158750"], "priority": "house"}).get_json()
    reasons = data["recommendations"][0]["reasons"]
    assert len(reasons) == 3
    assert any(":management:" in reason["id"] for reason in reasons)
    assert any(reason["id"].endswith(":outages") for reason in reasons)
    assert any(reason["id"].endswith(":repair_plans") for reason in reasons)
    assert any(fact["id"].endswith(":floors_text") for fact in data["evidence"])
    assert any("не число фактических" in reason["text"] for reason in reasons)


def test_address_trailing_interface_label_is_not_part_of_house_identity(tmp_path):
    conn = db.connect(tmp_path / "address.db")
    db.init_db(conn)
    addresses = ["улица Гамарника 21 Подробности о доме", "улица Гамарника 21А",
                 "Подробности о доме: улица Гамарника 21"]
    run_import(conn, [{"source": "farpost", "source_id": str(i), "address": address}
                      for i, address in enumerate(addresses)])
    offers = repo_offers.all_offers(conn)
    assert [o.address for o in offers] == ["улица Гамарника 21", *addresses[1:]]
    assert conn.execute("SELECT address FROM offers WHERE source_id='0'").fetchone()[0] == addresses[0]
    assert place_context.house_facts(offers[0].address)
    assert not place_context.house_facts(offers[1].address)
    conn.close()


def test_comparison_keeps_house_sources_and_undated_outages_separate(real_client):
    selected = ["farpost:139158750", "farpost:138877224"]
    html = real_client.get("/compare?" + urlencode([("o", key) for key in selected])).get_data(as_text=True)
    assert "Атлант" in html and "Эгершельд ДВ" in html
    assert "18.11.2025" in html and "выполнение работ этой записью не подтверждены" in html
    assert "Текущие отключения" in html and "Актуальные сведения не получены" in html
