# -*- coding: utf-8 -*-
"""ML-сторож (guard.model_sanity): доля пар без опорных признаков.

Регрессия 13–17.09: сторож считал долю по всей годовой вселенной пар (в т.ч.
cold_start-строкам без признаков) и браковал выпуск при 68%; порог 60% строгий.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from prognoz.predict.guard import NAN_SHARE_FAIL, NAN_SHARE_INFO, model_sanity

DAY = date(2026, 8, 20)


def _stamp(cutoff=DAY, n=80_000):
    return {"train_cutoff": cutoff.isoformat(), "n_train_rows": n}


def _preds(n_total: int, n_empty: int, cold_start: int = 0) -> pd.DataFrame:
    """n_total строк, из них n_empty без единого опорного признака; cold_start
    строк добавляются сверху с guard_mode=cold_start и тоже пустые."""
    full = n_total - n_empty
    frame = pd.DataFrame({
        "mu_ml": [3.0] * n_total + [0.0] * cold_start,
        "r28_mean": [2.5] * full + [np.nan] * (n_empty + cold_start),
        "wd_mean": [3.0] * full + [np.nan] * (n_empty + cold_start),
        "lag_recent": [3.0] * full + [np.nan] * (n_empty + cold_start),
        "guard_mode": ["stacked_consensus"] * n_total + ["cold_start"] * cold_start,
    })
    return frame


def test_boundary_61_percent_blocks_59_percent_passes():
    blocked = model_sanity(_stamp(date(2026, 8, 10)), _stamp(), _preds(100, 61), DAY)
    assert any("пустые опорные признаки" in p for p in blocked)
    assert model_sanity(_stamp(date(2026, 8, 10)), _stamp(), _preds(100, 59), DAY) == []


def test_cold_start_rows_do_not_count_towards_nan_share():
    # 100 живых пар, 50 пустых (50%) + 100 cold_start: по всей вселенной было бы 75%
    preds = _preds(100, 50, cold_start=100)
    assert model_sanity(_stamp(date(2026, 8, 10)), _stamp(), preds, DAY) == []


def test_info_zone_is_reported_without_blocking():
    infos: list[str] = []
    problems = model_sanity(_stamp(date(2026, 8, 10)), _stamp(), _preds(100, 45), DAY,
                            infos=infos)
    assert problems == []
    assert infos and "инфо" in infos[0] and "45%" in infos[0]
    assert NAN_SHARE_INFO < 0.45 <= NAN_SHARE_FAIL


def test_effective_lag_moves_information_boundary():
    # модель обучена по 05.08; цель 20.08, номинальный лаг 3 → граница 17.08 (12 дней — ок),
    # фактический лаг 7 → граница 13.08 (8 дней — тоже ок), но заглядывание вперёд ловится
    stamp = _stamp(date(2026, 8, 18))
    assert any("позже допустимого" in p
               for p in model_sanity(stamp, _stamp(), _preds(10, 0), DAY, lag_days=7))
    assert model_sanity(stamp, _stamp(), _preds(10, 0), DAY, lag_days=2) == []
