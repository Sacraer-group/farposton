import json
import math
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from evening.realdata import haversine_m, schedule_open_state
from evening.routes import router


def make_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret", https_only=False)
    app.include_router(router)
    return app


def write_catalog(root: Path, places: list[dict], folder="01_2gis"):
    p = root / folder
    p.mkdir(parents=True, exist_ok=True)
    path = p / "profiles.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for place in places:
            row = {"profile_type": "2gis", "card": place}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # ensure other folder exists for loader
    other = root / ("02_generated" if folder == "01_2gis" else "01_2gis")
    other.mkdir(parents=True, exist_ok=True)
    op = other / "profiles.jsonl"
    if not op.exists():
        op.write_text("", encoding="utf-8")
    return path


def food_card(card_id, name, lat, lon, rubrics=None, attrs=None, schedule=None, rating=4.5, reviews=10, updated="2026-11-10T10:00:00Z", address="ул. Тестовая, 1"):
    rubrics = rubrics or [{"name": "Кафе"}]
    attribute_groups = []
    if attrs:
        attribute_groups.append({"name": "Особенности", "attributes": [{"name": a, "value": None} for a in attrs]})
    card = {
        "id": str(card_id),
        "name": name,
        "address_name": address,
        "point": {"lat": lat, "lon": lon},
        "rubrics": rubrics,
        "reviews": {"general_rating": rating, "general_review_count": reviews},
        "dates": {"updated_at": updated},
        "attribute_groups": attribute_groups,
    }
    if schedule is not None:
        card["schedule"] = schedule
    return card


def venue_card(card_id, name, lat, lon):
    return {
        "id": str(card_id),
        "name": name,
        "address_name": "пл. Событийная, 1",
        "point": {"lat": lat, "lon": lon},
        "rubrics": [{"name": "Театры"}],
        "reviews": {"general_rating": 4.0, "general_review_count": 5},
        "dates": {"updated_at": "2026-11-10T10:00:00Z"},
        "attribute_groups": [],
        "schedule": {"Mon": {"working_hours": [{"from": "10:00", "to": "22:00"}]}},
    }


def make_event(eid, title, starts_at, venue_id, status="scheduled", checked_at="2026-11-13T10:00:00+10:00", source_url="https://example.com/event"):
    dt = datetime.fromisoformat(starts_at)
    return {
        "id": eid,
        "title": title,
        "starts_at": starts_at,
        "date": dt.date().isoformat(),
        "status": status,
        "synthetic": False,
        "venue_id": str(venue_id),
        "venue_name": "Тестовая площадка",
        "source_url": source_url,
        "source_name": "test_source",
        "checked_at": checked_at,
    }


def write_events(path: Path, events: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture()
def real_env(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    events = tmp_path / "events.json"
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("EVENING_DATA_MODE", "real")
    monkeypatch.setenv("EVENING_CATALOG_DIR", str(catalog))
    monkeypatch.setenv("EVENING_EVENTS_PATH", str(events))
    monkeypatch.setenv("EVENING_DB_PATH", str(state / "plans.sqlite3"))
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")
    client = TestClient(make_app())
    return client, catalog, events, state


def base_payload(event_id, date, meal_start="17:00", meal_end="18:00", **kw):
    p = {
        "event_id": event_id,
        "date": date,
        "meal_start": meal_start,
        "meal_end": meal_end,
        "required_features": [],
        "preferred_features": [],
        "max_distance_m": 5000,
    }
    p.update(kw)
    return p


# --- 503 no fallback ---

def test_real_mode_503_when_events_missing(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    write_catalog(catalog, [food_card("p1", "Кафе 1", 43.115, 131.885)])
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setenv("EVENING_DATA_MODE", "real")
    monkeypatch.setenv("EVENING_CATALOG_DIR", str(catalog))
    monkeypatch.setenv("EVENING_EVENTS_PATH", str(tmp_path / "missing-events.json"))
    monkeypatch.setenv("EVENING_DB_PATH", str(state / "plans.sqlite3"))
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    client = TestClient(make_app())
    r = client.get("/api/evening/events")
    assert r.status_code == 503
    body = r.json()
    assert body["detail"]["error"] in {"events_unavailable", "real_data_unavailable"}
    # must not fallback to fictional
    assert "items" not in body or body.get("items") != []


def test_real_mode_503_when_catalog_missing(tmp_path, monkeypatch):
    events = tmp_path / "events.json"
    eid = str(uuid.uuid4())
    write_events(events, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", "v1")])
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setenv("EVENING_DATA_MODE", "real")
    monkeypatch.setenv("EVENING_CATALOG_DIR", str(tmp_path / "missing-catalog"))
    monkeypatch.setenv("EVENING_EVENTS_PATH", str(events))
    monkeypatch.setenv("EVENING_DB_PATH", str(state / "plans.sqlite3"))
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    client = TestClient(make_app())
    r = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "real_data_unavailable"


# --- adapter / schematic temp JSONL, source/synthetic markers ---

def test_real_adapter_reads_schematic_catalog_and_marks_sources(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-1"
    p1 = food_card("place-a", "Кафе Альфа", 43.116, 131.886, attrs=["Можно с собакой", "Детское меню"], schedule={
        "Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}
    })
    p2 = food_card("place-b", "Бар Бета", 43.117, 131.887, rubrics=[{"name": "Бары"}], attrs=["Заказ столиков"], schedule={
        "Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}
    })
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    # include non-food to prove full catalog available for venue lookup
    nonfood = {"id": "shop-1", "name": "Магазин", "point": {"lat": 43.1, "lon": 131.8}, "rubrics": [{"name": "Магазины"}], "attribute_groups": [], "dates": {"updated_at": "2026-11-01T00:00:00Z"}}
    write_catalog(catalog, [v, p1, p2, nonfood])
    eid = str(uuid.uuid4())
    write_events(events_path, [make_event(eid, "Концерт", "2099-01-10T19:00:00+10:00", vid)])  # 2099-01-10 is Sat
    r = client.get("/api/evening/events")
    assert r.status_code == 200
    data = r.json()
    assert data["synthetic"] is False
    assert data["data_mode"] == "real"
    assert data["items"][0]["synthetic"] is False
    assert data["items"][0]["venue_name"] == "Тестовая площадка"
    assert data["items"][0]["source_url"] == "https://example.com/event"

    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201, c.text
    plan = c.json()
    assert plan["synthetic"] is False
    assert all(s.get("name") != "fictional_seed" for s in plan["sources"])
    assert any(s.get("name") == "2gis_catalog_snapshot" and s.get("synthetic") is False for s in plan["sources"])
    assert any(s.get("synthetic") is False for s in plan["sources"] if "event" in str(s.get("note", "")).lower() or s.get("name") == "test_source")
    assert plan["primary"]["synthetic"] is False
    assert plan["primary"]["address"] == "ул. Тестовая, 1"


# --- haversine accuracy, distance from venue ---

def test_haversine_known_pair_accuracy():
    # Vladivostok approx: ~111m per 0.001 deg lat near 43N, lon scaled; use known points
    m = haversine_m(43.115, 131.885, 43.116, 131.885)
    assert 105 <= m <= 118
    m2 = haversine_m(0.0, 179.999, 0.0, -179.999)
    assert 150 <= m2 <= 300  # across dateline, very close (~222m)


def test_distance_computed_from_event_venue_coords(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    # venue at known point, places offset
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    # ~111m north
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2])
    eid = str(uuid.uuid4())
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201, c.text
    plan = c.json()
    assert 105 <= plan["primary"]["distance_m"] <= 118
    assert 210 <= plan["alternative"]["distance_m"] <= 240


def test_error_coords_exclude_place(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    p1 = food_card("p1", "Без координат", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p1["point"] = {"lat": None, "lon": None}
    p2 = food_card("p2", "Далеко", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p3 = food_card("p3", "Ещё", 43.118, 131.885, rubrics=[{"name": "Кофейни"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2, p3])
    eid = str(uuid.uuid4())
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201, c.text
    ids = {c.json()["primary"]["place_id"], c.json()["alternative"]["place_id"]}
    assert "p1" not in ids


# --- schedule ---

def test_schedule_weekday_and_full_interval_minute_precision():
    schedule = {"Mon": {"working_hours": [{"from": "10:00", "to": "18:30"}]}}
    d = datetime.fromisoformat("2099-01-05").date()  # Monday
    assert schedule_open_state(schedule, d, "10:00", "18:30") == "open"
    assert schedule_open_state(schedule, d, "10:01", "18:30") == "open"
    assert schedule_open_state(schedule, d, "10:00", "18:31") == "closed"
    assert schedule_open_state(schedule, d, "09:59", "18:00") == "closed"


def test_schedule_overnight_including_previous_day():
    # Fri 18:00-02:00 means Sat early morning covered by Fri overnight segment
    schedule = {"Fri": {"working_hours": [{"from": "18:00", "to": "02:00"}]}, "Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}}
    sat = datetime.fromisoformat("2099-01-10").date()
    assert schedule_open_state(schedule, sat, "00:30", "01:30") == "open"
    assert schedule_open_state(schedule, sat, "01:00", "02:00") == "open"
    assert schedule_open_state(schedule, sat, "01:00", "02:01") == "closed"


def test_schedule_holes_and_split_segments():
    schedule = {"Sat": {"working_hours": [{"from": "10:00", "to": "14:00"}, {"from": "17:00", "to": "22:00"}]}}
    d = datetime.fromisoformat("2099-01-10").date()
    assert schedule_open_state(schedule, d, "10:00", "14:00") == "open"
    assert schedule_open_state(schedule, d, "17:00", "18:00") == "open"
    assert schedule_open_state(schedule, d, "13:30", "17:30") == "closed"  # crosses hole
    assert schedule_open_state(schedule, d, "14:00", "17:00") == "closed"


def test_schedule_missing_unknown_special_not_promised_open():
    d = datetime.fromisoformat("2099-01-10").date()
    assert schedule_open_state(None, d, "12:00", "13:00") == "unknown"
    assert schedule_open_state({}, d, "12:00", "13:00") == "unknown"
    assert schedule_open_state({"is_24x7": True}, d, "12:00", "13:00") == "open"
    assert schedule_open_state({"Sat": {"working_hours": []}}, d, "12:00", "13:00") == "unknown"
    assert schedule_open_state({"Sat": {"comment": "по записи"}}, d, "12:00", "13:00") == "unknown"
    # special/break markers -> unverified
    assert schedule_open_state({"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}], "comment": "праздник"}}, d, "12:00", "13:00") == "unknown"


# --- feature mapping ---

def test_feature_mapping_true_only_on_verified_positive_marks(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    attrs = ["Можно с собакой", "Можно с ноутбуком", "Детское меню", "Летняя веранда", "Заказ столиков"]
    p1 = food_card("p1", "Все признаки", 43.116, 131.885, attrs=attrs, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p1b = food_card("p1b", "Все признаки 2", 43.1165, 131.885, rubrics=[{"name": "Рестораны"}], attrs=attrs, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Терраса улица", 43.117, 131.885, rubrics=[{"name": "Бары"}], attrs=["Столики на улице"], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p3 = food_card("p3", "Без признаков", 43.118, 131.885, rubrics=[{"name": "Кофейни"}], attrs=[], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p1b, p2, p3])
    eid = str(uuid.uuid4())
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10", required_features=["dog", "laptop", "children", "terrace", "reservations"], max_distance_m=5000))
    assert c.status_code == 201, c.text
    ids_all = {c.json()["primary"]["place_id"], c.json()["alternative"]["place_id"]}
    assert "p1" in ids_all or "p1b" in ids_all

    # terrace via "Столики на улице" must satisfy required terrace
    c2 = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10", required_features=["terrace"], max_distance_m=5000))
    assert c2.status_code == 201, c2.text
    ids = {c2.json()["primary"]["place_id"], c2.json()["alternative"]["place_id"]}
    assert ids & {"p1", "p1b", "p2"}
    assert "p3" not in ids
    # "Столики на улице" alone is eligible: pair it with another terrace place at similar distance
    c2b = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10", required_features=["terrace"], max_distance_m=180))
    assert c2b.status_code == 201, c2b.text
    ids2b = {c2b.json()["primary"]["place_id"], c2b.json()["alternative"]["place_id"]}
    assert "p1" in ids2b and "p1b" in ids2b
    # dedicated check: only street-terrace + veranda places eligible
    c2c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10", required_features=["terrace", "dog"], max_distance_m=5000))
    assert c2c.status_code == 201, c2c.text
    ids2c = {c2c.json()["primary"]["place_id"], c2c.json()["alternative"]["place_id"]}
    assert "p2" not in ids2c  # p2 has no dog mark => unknown
    assert ids2c == {"p1", "p1b"}




    # missing attrs must be unknown, never false-confirmed; requiring unknown should not pass
    c3 = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10", required_features=["dog"], max_distance_m=5000))
    assert c3.status_code == 201
    ids3 = {c3.json()["primary"]["place_id"], c3.json()["alternative"]["place_id"]}
    assert "p3" not in ids3



# --- stale/cancelled/removed/current and old fictional plans ---

def test_stale_warning_when_source_checked_at_older_than_24h(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2])
    eid = str(uuid.uuid4())
    old = "2026-11-01T10:00:00+10:00"
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid, checked_at=old)])
    r = client.get("/api/evening/events")
    assert r.status_code == 200
    assert r.json().get("warning")
    assert r.json()["items"][0].get("warning")

    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201
    assert c.json().get("warning") or any(s.get("stale") for s in c.json().get("sources", []))
    g = client.get(f"/api/evening/plans/{c.json()['id']}")
    assert g.status_code == 200
    assert g.json().get("warning") or any(s.get("stale") for s in g.json().get("sources", []))


def test_current_removed_cancelled_and_snapshot_preserved(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2])
    eid = str(uuid.uuid4())
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201
    pid = c.json()["id"]

    # cancelled
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid, status="cancelled")])
    g = client.get(f"/api/evening/plans/{pid}")
    assert g.status_code == 200
    assert g.json()["current"]["event_status"] == "cancelled"
    assert g.json()["event"]["status"] == "scheduled"  # snapshot

    # removed
    write_events(events_path, [])
    g2 = client.get(f"/api/evening/plans/{pid}")
    assert g2.status_code == 200
    assert g2.json()["current"]["event_status"] == "unavailable"


def test_current_time_change_same_status_becomes_rescheduled_and_snapshot_preserved(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2])
    eid = str(uuid.uuid4())
    orig = "2099-01-10T19:00:00+10:00"
    changed = "2099-01-10T20:00:00+10:00"
    write_events(events_path, [make_event(eid, "Событие", orig, vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201, c.text
    plan = c.json()
    pid = plan["id"]
    snap_event = plan["event"]
    snap_primary = plan["primary"]

    write_events(events_path, [make_event(eid, "Событие", changed, vid, status="scheduled")])
    g = client.get(f"/api/evening/plans/{pid}")
    assert g.status_code == 200
    body = g.json()
    assert body["current"]["event_status"] == "rescheduled"
    assert body["current"]["starts_at"] == changed
    assert body["event"]["starts_at"] == snap_event["starts_at"]
    assert body["event"]["venue_id"] == snap_event["venue_id"]
    assert body["event"]["status"] == "scheduled"
    assert body["primary"] == snap_primary


def test_current_venue_change_same_status_becomes_rescheduled_and_snapshot_preserved(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    vid2 = "venue-v2"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    v2 = venue_card(vid2, "Площадка 2", 43.120, 131.890)
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, v2, p1, p2])
    eid = str(uuid.uuid4())
    starts = "2099-01-10T19:00:00+10:00"
    write_events(events_path, [make_event(eid, "Событие", starts, vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201, c.text
    plan = c.json()
    pid = plan["id"]
    snap_event = plan["event"]
    snap_primary = plan["primary"]

    write_events(events_path, [make_event(eid, "Событие", starts, vid2, status="scheduled")])
    g = client.get(f"/api/evening/plans/{pid}")
    assert g.status_code == 200
    body = g.json()
    assert body["current"]["event_status"] == "rescheduled"
    assert body["current"]["starts_at"] == starts
    assert body["event"]["starts_at"] == snap_event["starts_at"]
    assert body["event"]["venue_id"] == snap_event["venue_id"]
    assert body["event"]["status"] == "scheduled"
    assert body["primary"] == snap_primary


def test_old_fictional_plan_preserved_and_unavailable_if_event_absent(tmp_path, monkeypatch):
    # create fictional plan in demo mode DB, then read when event absent from its demo source
    demo_dir = tmp_path / "demo"
    db = demo_dir / "plans.sqlite3"
    monkeypatch.setenv("EVENING_DATA_MODE", "demo")
    monkeypatch.setenv("EVENING_DEMO_DIR", str(demo_dir))
    monkeypatch.setenv("EVENING_DB_PATH", str(db))
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    from evening.seed import load_or_create_seed, seed_path
    seed = load_or_create_seed()
    ev = next(e for e in seed["events"] if e["status"] == "scheduled")
    client = TestClient(make_app())
    c = client.post("/api/evening/plans", json=base_payload(ev["id"], ev["date"]))
    assert c.status_code == 201
    pid = c.json()["id"]
    assert c.json()["synthetic"] is True

    # remove event from demo source, read in real mode context with unavailable real data
    data = json.loads(seed_path().read_text(encoding="utf-8"))
    data["events"] = [e for e in data["events"] if e["id"] != ev["id"]]
    seed_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("EVENING_DATA_MODE", "real")
    monkeypatch.setenv("EVENING_CATALOG_DIR", str(tmp_path / "missing-catalog"))
    monkeypatch.setenv("EVENING_EVENTS_PATH", str(tmp_path / "missing-events.json"))
    client2 = TestClient(make_app())
    g = client2.get(f"/api/evening/plans/{pid}")
    assert g.status_code == 200
    assert g.json()["synthetic"] is True
    assert g.json()["sources"][0]["name"] == "fictional_seed"
    assert g.json()["current"]["event_status"] == "unavailable"


# --- additional requirements: 503 on permission, catalog checked_at unknown, schedule merge/24:00, event validation ---


def test_real_catalog_permission_error_returns_503_not_500(tmp_path, monkeypatch):
    import evening.realdata as rd
    from pathlib import Path

    catalog = tmp_path / "catalog"
    catalog.mkdir()
    events = tmp_path / "events.json"
    eid = str(uuid.uuid4())
    write_events(events, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", "v1")])
    state = tmp_path / "state"; state.mkdir()
    monkeypatch.setenv("EVENING_DATA_MODE", "real")
    monkeypatch.setenv("EVENING_CATALOG_DIR", str(catalog))
    monkeypatch.setenv("EVENING_EVENTS_PATH", str(events))
    monkeypatch.setenv("EVENING_DB_PATH", str(state / "plans.sqlite3"))
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    rd._CATALOG_CACHE = None
    rd._CATALOG_KEY = None
    orig_exists = Path.exists

    def raise_perm(self):
        if str(self) == str(catalog):
            raise PermissionError("denied")
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", raise_perm)
    rd._CATALOG_CACHE = None
    rd._CATALOG_KEY = None
    client = TestClient(make_app())
    r = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "real_data_unavailable"


def test_catalog_checked_at_is_unknown_not_mtime(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2])
    eid = str(uuid.uuid4())
    write_events(events_path, [make_event(eid, "Событие", "2099-01-10T19:00:00+10:00", vid)])
    c = client.post("/api/evening/plans", json=base_payload(eid, "2099-01-10"))
    assert c.status_code == 201
    cat_src = next(s for s in c.json()["sources"] if s.get("name") == "2gis_catalog_snapshot")
    assert cat_src.get("checked_at") in (None, "unknown")


def test_schedule_merges_adjacent_and_overlapping_intervals_and_supports_2400():
    d = datetime.fromisoformat("2099-01-10").date()  # Saturday
    # adjacent segments merged -> full coverage
    schedule = {"Sat": {"working_hours": [{"from": "10:00", "to": "14:00"}, {"from": "14:00", "to": "18:00"}]}}
    assert schedule_open_state(schedule, d, "10:00", "18:00") == "open"
    assert schedule_open_state(schedule, d, "13:30", "14:30") == "open"
    # overlapping
    schedule2 = {"Sat": {"working_hours": [{"from": "10:00", "to": "15:00"}, {"from": "14:00", "to": "18:00"}]}}
    assert schedule_open_state(schedule2, d, "10:00", "18:00") == "open"
    # 24:00 end of day
    schedule3 = {"Sat": {"working_hours": [{"from": "18:00", "to": "24:00"}]}}
    assert schedule_open_state(schedule3, d, "18:00", "24:00") == "open"
    assert schedule_open_state(schedule3, d, "20:00", "24:00") == "open"
    # overnight to 00:00 represented as 24:00 previous-day end equivalent not needed, but 00:00 start covered by overnight span
    schedule4 = {"Fri": {"working_hours": [{"from": "20:00", "to": "02:00"}]}}
    assert schedule_open_state(schedule4, d, "00:00", "02:00") == "open"


def test_real_events_invalid_uuid_or_http_or_date_mismatch_or_synthetic_true_raise_503(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    p1 = food_card("p1", "Кафе 1", 43.116, 131.885, schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    p2 = food_card("p2", "Кафе 2", 43.117, 131.885, rubrics=[{"name": "Рестораны"}], schedule={"Sat": {"working_hours": [{"from": "10:00", "to": "22:00"}]}})
    write_catalog(catalog, [v, p1, p2])
    import evening.realdata as rd

    def reload_events(data_events):
        write_events(events_path, data_events)
        rd._EVENTS_CACHE = None
        rd._EVENTS_KEY = None
        return client.get("/api/evening/events")

    bad_uuid = [make_event("not-a-uuid", "Событие", "2099-01-10T19:00:00+10:00", vid)]
    assert reload_events(bad_uuid).status_code == 503

    bad_http = [make_event(str(uuid.uuid4()), "Событие", "2099-01-10T19:00:00+10:00", vid, source_url="http://example.com/x")]
    assert reload_events(bad_http).status_code == 503

    mismatch = make_event(str(uuid.uuid4()), "Событие", "2099-01-10T19:00:00+10:00", vid)
    mismatch["date"] = "2099-01-11"
    assert reload_events([mismatch]).status_code == 503

    bad_syn = make_event(str(uuid.uuid4()), "Событие", "2099-01-10T19:00:00+10:00", vid)
    bad_syn["synthetic"] = True
    assert reload_events([bad_syn]).status_code == 503


def test_real_events_all_corrupted_not_silent_success(real_env):
    client, catalog, events_path, _ = real_env
    vid = "venue-v"
    v = venue_card(vid, "Площадка", 43.115, 131.885)
    write_catalog(catalog, [v])
    import evening.realdata as rd

    # structurally valid file but every event invalid (bad source url) -> must not become silent empty success
    ev = make_event(str(uuid.uuid4()), "Событие", "2099-01-10T19:00:00+10:00", vid, source_url="http://insecure")
    write_events(events_path, [ev])
    rd._EVENTS_CACHE = None
    rd._EVENTS_KEY = None
    r = client.get("/api/evening/events")
    assert r.status_code == 503
    body = r.json()
    assert body.get("detail", {}).get("error") in {"events_unavailable", "real_data_unavailable"}
    assert body.get("items") in (None, [])  # no silent success list




def test_schedule_union_previous_overnight_and_today():
    d = datetime.fromisoformat("2099-01-10").date()
    schedule = {
        "Fri": {"working_hours": [{"from": "22:00", "to": "02:00"}]},
        "Sat": {"working_hours": [{"from": "02:00", "to": "06:00"}]},
    }
    assert schedule_open_state(schedule, d, "01:30", "02:30") == "open"
    schedule["Sat"]["comment"] = "по записи"
    assert schedule_open_state(schedule, d, "01:30", "02:30") == "unknown"


def test_schedule_missing_today_does_not_imply_closed():
    d = datetime.fromisoformat("2099-01-10").date()
    schedule = {"Fri": {"working_hours": [{"from": "10:00", "to": "22:00"}]}}
    assert schedule_open_state(schedule, d, "12:00", "13:00") == "unknown"


def test_schedule_overnight_union_keeps_real_gap_closed():
    d = datetime.fromisoformat("2099-01-10").date()
    schedule = {
        "Fri": {"working_hours": [{"from": "22:00", "to": "02:00"}]},
        "Sat": {"working_hours": [{"from": "03:00", "to": "06:00"}]},
    }
    assert schedule_open_state(schedule, d, "01:30", "03:30") == "closed"
