from datetime import date
import json
import sqlite3

import numpy as np
import pytest

from tools.train_venue_ensemble import deterministic_sample, features_for_day, read_source, scores
from web.places_history import create_history


def test_features_use_only_past_and_skip_missing_target():
    matrix = np.empty((2, 12, 24), dtype=np.float32)
    for day in range(12):
        matrix[:, day, :] = day + 1
    matrix[1, 7, 5] = np.nan
    start = date(2026, 10, 13)
    x, y, groups, total = features_for_day(matrix, start, 7)
    assert total == len(y) == 47
    assert x[0].tolist() == [1, 0, 7, 6, 1, 4]
    assert y[0] == 8 and groups[0] == 0
    changed = matrix.copy()
    changed[:, 7:, :] = 99
    changed[1, 7, 5] = np.nan
    x_after, y_after, _, _ = features_for_day(changed, start, 7)
    np.testing.assert_array_equal(x, x_after)
    assert y_after[0] != y[0]
    with pytest.raises(ValueError):
        features_for_day(matrix, start, 6)


def test_only_explicit_synthetic_data_is_accepted(tmp_path):
    days = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
    places = {'x': {'id': 'x', 'categories': [], 'rubrics': [], 'schedule': {'is_24x7': True},
                    'synthetic': {'weekly_by_hour': {d: [30] * 24 for d in days}}}}
    path = tmp_path / 'history.sqlite3'
    create_history(path, places, date(2026, 10, 13), date(2026, 11, 12))
    meta, start, matrix = read_source(path)
    assert meta['synthetic'] is True and start == date(2026, 10, 13)
    assert matrix.shape == (1, 31, 24)
    with sqlite3.connect(path) as c:
        c.execute('update metadata set value=? where key=?', (json.dumps(False), 'synthetic'))
    with pytest.raises(ValueError, match='синтетическая'):
        read_source(path)


def test_metrics_and_sampling_are_reproducible():
    y = np.array([10., 20., 30.])
    result = scores(y, np.array([10., 15., 45.]))
    assert result['mae'] == pytest.approx(20 / 3, abs=0.0001)
    assert result['within_10_index_points_pct'] == pytest.approx(66.67)
    np.testing.assert_array_equal(deterministic_sample(100, 20), deterministic_sample(100, 20))
