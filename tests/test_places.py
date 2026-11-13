import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from web.auth import require_auth
from web.places import catalogue, opening_state
from web.routes_places import router


def test_opening_hours_respect_overnight_and_unknown():
    hours = {"Fri": {"working_hours": [{"from": "18:00", "to": "03:00"}]}}
    assert opening_state(hours, 4, 22) == "open"
    assert opening_state(hours, 5, 2) == "open"
    assert opening_state(hours, 5, 3) == "unknown"
    hours["Sat"] = {"working_hours": []}
    assert opening_state(hours, 5, 3) == "closed"
    assert opening_state(None, 5, 12) == "unknown"
    assert opening_state({"is_24x7": True}, 0, 4) == "open"


@pytest.fixture
def client(tmp_path, monkeypatch):
    days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    cards = [
        ("01_2gis", {"profile_type": "2gis", "card": {"id": "real", "name": "Кафе Реальное", "rubrics": [{"name": "Кафе"}], "point": {"lat": 43.1, "lon": 131.9}, "schedule": {"is_24x7": True}}, "congestion": {"3": {"Mon": [12]}}}),
        ("02_generated", {"profile_type": "synthetic", "card": {"id": "demo", "name": "Бар Демо", "rubrics": [{"name": "Бары"}], "point": {"lat": 43.1, "lon": 131.9}}, "synthetic_congestion": {"weekly_by_hour": {d: [42] * 24 for d in days}, "schedule_basis": "assumed_09_21_daily"}}),
    ]
    for folder, card in cards:
        directory = tmp_path / folder
        directory.mkdir()
        (directory / "profiles.jsonl").write_text(json.dumps(card, ensure_ascii=False) + "\n")
    monkeypatch.setenv("FARPOSTON_PLACES_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FARPOSTON_HISTORY_DB", str(tmp_path / "history.sqlite3"))
    catalogue.cache_clear()
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(router)
    app.dependency_overrides[require_auth] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    catalogue.cache_clear()


def test_api_keeps_source_and_demo_values_separate(client):
    result = client.get('/api/places', params={"category": "all", "day": 0, "hour": 12}).json()
    items = {x["id"]: x for x in result["items"]}
    assert result["total"] == 2
    assert items["real"]["profile_type"] == "2gis" and items["real"]["demo_index"] is None
    assert items["demo"]["profile_type"] == "synthetic" and items["demo"]["demo_index"] == 42
    assert items["demo"]["opening_state"] == "unknown"
    assert items["demo"]["schedule_assumed"] is True
    profile = client.get('/api/places/real').json()
    assert profile["congestion_2gis"] == {"3": {"Mon": [12]}}
    assert profile["has_dynamic_congestion"] is False


def test_search_source_filters_and_validation(client):
    assert client.get('/api/places', params={"category": "cafe", "q": "реальное"}).json()["total"] == 1
    assert client.get('/api/places', params={"category": "all", "source": "2gis"}).json()["total"] == 1
    assert client.get('/api/places', params={"category": "all", "open_only": "true", "day": 0, "hour": 12}).json()["total"] == 1
    assert client.get('/api/places', params={"day": 7}).status_code == 422
    assert client.get('/api/places/missing').status_code == 404


def test_demo_sort_never_mixes_provider_profiles(client, monkeypatch):
    from web import routes_places

    places = dict(catalogue())
    low = dict(places['demo'])
    low['id'], low['name'] = 'other', 'Другая точка'
    low['synthetic'] = {**low['synthetic'], 'weekly_by_hour': {day: [17] * 24 for day in
                        ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')}}
    places['other'] = low
    monkeypatch.setattr(routes_places, '_catalogue', lambda: places)
    assert client.get('/api/places', params={'category': 'all', 'sort': 'demo_asc'}).status_code == 400
    result = client.get('/api/places', params={'category': 'all', 'source': 'synthetic', 'sort': 'demo_asc', 'day': 0, 'hour': 12})
    assert [item['id'] for item in result.json()['items']] == ['other', 'demo']
    result = client.get('/api/places', params={'category': 'all', 'source': 'synthetic', 'sort': 'demo_desc', 'day': 0, 'hour': 12})
    assert [item['id'] for item in result.json()['items']] == ['demo', 'other']


def test_api_is_not_public_without_session(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "client")
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "0")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test")
    app.include_router(router)
    assert TestClient(app).get('/api/places').status_code == 401
    assert TestClient(app).get('/api/places/real/history').status_code == 401


def test_missing_history_does_not_break_catalogue(client, tmp_path, monkeypatch):
    monkeypatch.setenv('FARPOSTON_HISTORY_DB', str(tmp_path / 'not-created.sqlite3'))
    result = client.get('/api/places', params={'category': 'all'})
    assert result.status_code == 200 and result.json()['history'] is None
    assert client.get('/api/places/real/history').status_code == 503


def test_history_calendar_and_synthetic_provenance(client, tmp_path, monkeypatch):
    from datetime import date
    from web.places_history import calendar_month, create_history

    assert calendar_month(date(2026, 11, 13)) == (date(2026, 10, 13), date(2026, 11, 12))
    assert calendar_month(date(2024, 3, 31)) == (date(2024, 2, 29), date(2024, 3, 30))
    path = tmp_path / 'history.sqlite3'
    places = catalogue()
    info = create_history(path, places, date(2026, 10, 13), date(2026, 11, 12))
    assert info['venues'] == 2 and info['days'] == 31 and info['synthetic'] is True
    with pytest.raises(FileExistsError):
        create_history(path, places, date(2026, 10, 13), date(2026, 11, 12))
    monkeypatch.setenv('FARPOSTON_HISTORY_DB', str(path))
    result = client.get('/api/places/real/history')
    assert result.status_code == 200
    history = result.json()
    assert history['synthetic'] is True and history['timezone'] == 'Asia/Vladivostok'
    assert history['start_date'] == '2026-10-13' and history['end_date'] == '2026-11-12'
    assert len(history['days']) == 31
    assert all(len(day['hourly']) == 24 for day in history['days'])
    assert len({day['average'] for day in history['days']}) > 1
    demo = client.get('/api/places/demo/history').json()
    assert demo['schedule_basis'] == 'assumed_09_21_daily'
    mondays = [day['hourly'][12] for day in demo['days']
               if date.fromisoformat(day['date']).weekday() == 0 and day['hourly'][12] is not None]
    expected_index = round(sum(mondays) / len(mondays))
    listed = client.get('/api/places', params={'category': 'all', 'day': 0, 'hour': 12}).json()
    by_id = {item['id']: item for item in listed['items']}
    assert by_id['demo']['demo_index'] == expected_index
    assert by_id['real']['demo_index'] is None
    detail = client.get('/api/places/demo').json()
    assert detail['synthetic']['weekly_by_hour']['Mon'][12] == expected_index
    assert detail['synthetic']['generated_with'] == 'synthetic-history-weekly'
    assert client.get('/api/places/no-such-place/history').status_code == 404


def test_history_does_not_use_unknown_2gis_scale():
    from datetime import date
    from web.places_history import daily_values

    place = {'id': 'example', 'categories': ['restaurant'], 'rubrics': ['Рестораны'],
             'schedule': {'is_24x7': True}, 'congestion_2gis': {'10': {'Mon': [14]}}}
    first, basis = daily_values(place, date(2026, 10, 13))
    place['congestion_2gis'] = {'2': {'Fri': [20]}}
    second, _ = daily_values(place, date(2026, 10, 13))
    other, _ = daily_values(place, date(2026, 10, 14))
    assert first == second and first != other
    assert basis == 'reported_regular_schedule'
    assert all(n is not None and 1 <= n <= 100 for n in first)


def test_history_falls_back_when_source_has_no_usable_hours():
    from datetime import date
    from web.places_history import daily_values

    days = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
    place = {'id': 'short-schedule', 'categories': [], 'rubrics': [],
             'schedule': {day: {'working_hours': [{'from': '00:00', 'to': '00:01'}]} for day in days},
             'synthetic': {'weekly_by_hour': {day: [0] * 24 for day in days}}}
    values, basis = daily_values(place, date(2026, 10, 13))
    assert basis == 'assumed_09_21_no_usable_hours'
    assert values[8] is None and values[9] is not None and values[20] is not None and values[21] is None


def test_unknown_history_provenance_is_not_mislabelled(client, tmp_path, monkeypatch):
    import sqlite3
    from datetime import date
    from web.places_history import create_history

    path = tmp_path / 'history.sqlite3'
    create_history(path, catalogue(), date(2026, 10, 13), date(2026, 10, 14))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE metadata SET value='false' WHERE key='synthetic'")
    monkeypatch.setenv('FARPOSTON_HISTORY_DB', str(path))
    assert client.get('/api/places/real/history').status_code == 503
    assert client.get('/api/places', params={'category': 'all'}).json()['history'] is None


def test_presentation_scenario_has_weekend_peaks_and_distinct_venues():
    from datetime import date, timedelta
    from statistics import mean
    from web.places_history import daily_values

    places = [{'id': str(index), 'categories': ['restaurant'], 'rubrics': ['Рестораны'],
               'schedule': {'is_24x7': True}} for index in range(20)]
    monday, friday = [], []
    for place in places:
        for week in range(4):
            monday.append(daily_values(place, date(2026, 10, 19) + timedelta(days=week * 7))[0][19])
            friday.append(daily_values(place, date(2026, 10, 23) + timedelta(days=week * 7))[0][19])
    assert mean(friday) > mean(monday)
    assert len(set(friday)) > 10
