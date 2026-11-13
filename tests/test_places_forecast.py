from datetime import date, datetime
import sqlite3

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tools.train_venue_ensemble import features_for_day
from web import places_forecast, routes_places
from web.auth import require_auth
from web.places_history import create_history


class CapturePredictor:
    def __init__(self, value):
        self.value = value
        self.features = []

    def predict(self, features):
        self.features.append(features.copy())
        return np.full(len(features), self.value)


def test_forecast_uses_same_past_features_as_training(tmp_path, monkeypatch):
    source = tmp_path / 'history.sqlite3'
    places = {'a': {'id': 'a', 'schedule': {'is_24x7': True}}}
    create_history(source, places, date(2026, 11, 6), date(2026, 11, 12))
    with sqlite3.connect(source) as conn:
        raw = conn.execute('SELECT hourly FROM daily ORDER BY day').fetchall()
    matrix = np.array([[list(row[0]) for row in raw] + [[50] * 24]], dtype=np.float32)
    matrix[matrix == 255] = np.nan
    expected, _, _, _ = features_for_day(matrix, date(2026, 11, 6), 7)
    hgb, extra = CapturePredictor(40), CapturePredictor(60)
    model = {'hgb': hgb, 'extra_trees': extra, 'hgb_weight': .75,
             'metadata': {'split': {'test': {'end': '2026-11-12'}}}}
    marker = tmp_path / 'model'
    marker.touch()
    monkeypatch.setattr(places_forecast, 'MODEL_PATH', marker)
    monkeypatch.setattr(places_forecast, 'HISTORY_PATH', source)
    monkeypatch.setattr(places_forecast, '_load_verified', lambda *_: model)
    result = places_forecast.forecast(places['a'], today=date(2026, 11, 13))
    np.testing.assert_array_equal(hgb.features[0], expected)
    np.testing.assert_array_equal(extra.features[0], expected)
    assert result['date'] == '2026-11-13' and result['history_through'] == '2026-11-12'
    assert result['synthetic'] is True and result['hourly'] == [45] * 24
    assert [day['date'] for day in result['days']] == [f'2026-11-{d}' for d in range(13, 20)]
    assert all(day['hourly'] == [45] * 24 for day in result['days'])
    assert np.all(hgb.features[1][:, 2] == 45)  # Day 2 uses day 1's prediction as lag 1.
    assert result['method'] == 'recursive_next_day'


def test_forecast_rolls_over_and_never_generates_future_inputs(tmp_path, monkeypatch):
    source, marker = tmp_path / 'history.sqlite3', tmp_path / 'model'
    place = {'id': 'a', 'schedule': {'is_24x7': True}}
    create_history(source, {'a': place}, date(2026, 11, 6), date(2026, 11, 12))
    marker.touch()
    model = {'hgb': CapturePredictor(40), 'extra_trees': CapturePredictor(60), 'hgb_weight': .5,
             'metadata': {'split': {'test': {'end': '2026-11-12'}}}}
    monkeypatch.setattr(places_forecast, 'MODEL_PATH', marker)
    monkeypatch.setattr(places_forecast, 'HISTORY_PATH', source)
    monkeypatch.setattr(places_forecast, '_load_verified', lambda *_: model)
    generated_dates = []
    generator = places_forecast.daily_values
    def record_generation(place, day):
        generated_dates.append(day)
        return generator(place, day)
    monkeypatch.setattr(places_forecast, 'daily_values', record_generation)
    class FakeClock:
        current = date(2026, 11, 13)
        @classmethod
        def now(cls, zone):
            assert str(zone) == 'Asia/Vladivostok'
            return datetime.combine(cls.current, datetime.min.time(), tzinfo=zone)
    monkeypatch.setattr(places_forecast, 'datetime', FakeClock)
    first = places_forecast.forecast(place)
    FakeClock.current = date(2026, 11, 14)
    second = places_forecast.forecast(place)
    assert first['date'] == '2026-11-13' and second['date'] == '2026-11-14'
    assert max(generated_dates[:7]) == date(2026, 11, 12)
    assert max(generated_dates[7:]) == date(2026, 11, 13)
    assert second['days'][-1]['date'] == '2026-11-20'


def test_missing_lag_remains_missing_and_nan_history_is_imputed():
    history = np.full((7, 24), 30, dtype=np.float32)
    history[0, 3] = np.nan
    history[-1, 4] = np.nan
    hgb, extra = CapturePredictor(40), CapturePredictor(60)
    result = places_forecast._predict_day({'hgb': hgb, 'extra_trees': extra, 'hgb_weight': .5}, history, date(2026, 11, 13))
    assert result[3] is None and result[4] == 50
    row = hgb.features[0][hgb.features[0][:, 1] == 4][0]
    assert row[2] == -1 and np.isfinite(hgb.features[0]).all()


def test_inference_rejects_unmarked_model_and_mismatched_history(tmp_path, monkeypatch):
    model, history = tmp_path / 'model', tmp_path / 'history'
    model.write_bytes(b'model')
    history.write_bytes(b'history')
    signature = (places_forecast._file_signature(model), places_forecast._file_signature(history))
    monkeypatch.setattr(places_forecast.joblib, 'load', lambda _: {'synthetic_only': False})
    places_forecast._load_verified.cache_clear()
    with pytest.raises(ValueError, match='демонстрационная'):
        places_forecast._load_verified(*signature)
    monkeypatch.setattr(places_forecast.joblib, 'load', lambda _: {
        'synthetic_only': True, 'features': places_forecast.FEATURES, 'hgb_weight': .5,
        'metadata': {'synthetic_only': True, 'source_sha256': 'wrong'}})
    with pytest.raises(ValueError, match='не соответствует'):
        places_forecast._load_verified(*signature)
    places_forecast._load_verified.cache_clear()


def test_forecast_route_handles_missing_and_unavailable(monkeypatch):
    app = FastAPI()
    app.include_router(routes_places.router)
    app.dependency_overrides[require_auth] = lambda: None
    monkeypatch.setattr(routes_places, '_catalogue', lambda: {'a': {}})
    monkeypatch.setattr(routes_places, 'forecast', lambda _: {'synthetic': True, 'hourly': [42] * 24})
    client = TestClient(app)
    assert client.get('/api/places/a/forecast').json()['hourly'][0] == 42
    assert client.get('/api/places/missing/forecast').status_code == 404
    def unavailable(_):
        raise FileNotFoundError()
    monkeypatch.setattr(routes_places, 'forecast', unavailable)
    assert client.get('/api/places/a/forecast').status_code == 503
