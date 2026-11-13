# -*- coding: utf-8 -*-
"""ML-ядро (predict.ml_core): честность признаков, cutoff, категории, floor.

Образец адверсарной проверки — analysis/research/_audit_gbm_feats.py: признаки
пересчитываются независимо из сырья, утечка проверяется подменой будущего.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))

from prognoz.predict import ml_core                          # noqa: E402
from prognoz.predict.ml_core import (MlCfg, add_features,    # noqa: E402
                                     apply_categories, build_panel, fit,
                                     novelty_floor, snapshot_categories)

END = date(2026, 7, 31)


def _shelf(pt, sk, n_days, sold_fn, grp="Шаурма", gap_days=(), end=END):
    """n_days жизни пары до end включительно; sold_fn(i) — продажи дня i."""
    rows = []
    for i in range(n_days):
        d = end - timedelta(days=n_days - 1 - i)
        rows.append(dict(point_key=pt, sku_key=sk, trade_day=d,
                         sold_units=float(sold_fn(i)), lost_units=0.0,
                         u40_units=0.0, wo_units=0.0, avg_price=250.0,
                         supply_gap=(i in gap_days), wo_settled=True, grp=grp))
    return rows


def _big_panel(end=END, n_days=150):
    """2 точки × 5 SKU, детерминированный «спрос» — хватает на fit (≥1000 строк)."""
    rng = np.random.RandomState(7)
    rows = []
    for pi, pt in enumerate(("P1", "P2")):
        for si in range(5):
            base = 3 + 2 * si + 5 * pi
            rows += _shelf(pt, f"s{si}", n_days,
                           lambda i, b=base: b + (i % 7) + rng.randint(0, 3))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ панель

def test_panel_gap_and_excluded_days_are_nan():
    df = pd.DataFrame(_shelf("P", "s", 40, lambda i: 5.0, gap_days=(10,)))
    excl_day = END - timedelta(days=5)
    pan = build_panel(df, END, excluded={excl_day})
    row_gap = pan[pan["day"] == pd.Timestamp(END - timedelta(days=29))]
    assert row_gap["dem"].isna().all()          # supply_gap → NaN
    row_ex = pan[pan["day"] == pd.Timestamp(excl_day)]
    assert row_ex["dem"].isna().all()           # нетипичный день → NaN
    assert pan["day"].max() == pd.Timestamp(END)


def test_panel_grid_extends_to_future_target():
    """Сетка тянется до end даже когда полка кончилась раньше: строки будущего
    существуют с NaN-целью, признаки на них считаются лагами."""
    df = pd.DataFrame(_shelf("P", "s", 40, lambda i: 5.0))
    target = END + timedelta(days=2)
    pan = build_panel(df, target, None)
    fut = pan[pan["day"] > pd.Timestamp(END)]
    assert len(fut) == 2 and fut["dem"].isna().all()


# ------------------------------------------------------------------ признаки

def test_features_match_independent_recalc():
    """lag/r28/wd на арифметической паре — против ручного пересчёта."""
    df = pd.DataFrame(_shelf("P", "s", 60, lambda i: float(i)))  # dem дня i = i
    lag = 3
    pan, _ = add_features(build_panel(df, END, None), lag)
    d = pan[pan["day"] == pd.Timestamp(END)].iloc[0]
    # день END имеет i=59: lag3 → i=56, lag7 → i=52
    assert d["lag3"] == 56.0 and d["lag7"] == 52.0
    # r28_mean: 28 значений, кончая i=56 → mean(29..56) = 42.5
    assert d["r28_mean"] == pytest.approx(np.mean(np.arange(29, 57)))
    # wd_mean: тот же день недели, shift(1) по weekday-группе → i−7, i−14, i−21, i−28
    assert d["wd_mean"] == pytest.approx(np.mean([52, 45, 38, 31]))


def test_features_blind_beyond_lag():
    """Признаки дня D не видят dem позже D−lag: подмена хвоста ничего не меняет."""
    df = pd.DataFrame(_shelf("P", "s", 60, lambda i: float(i % 9)))
    lag = 3
    pan1, feats = add_features(build_panel(df, END, None), lag)
    df2 = df.copy()
    tail = pd.to_datetime(df2["trade_day"]) > pd.Timestamp(END - timedelta(days=lag))
    df2.loc[tail, "sold_units"] = 999.0        # dem = sold + lost
    pan2, _ = add_features(build_panel(df2, END, None), lag)
    num = [f for f in feats if f not in ("point_cat", "grp_cat")]
    r1 = pan1[pan1["day"] == pd.Timestamp(END)][num].reset_index(drop=True)
    r2 = pan2[pan2["day"] == pd.Timestamp(END)][num].reset_index(drop=True)
    pd.testing.assert_frame_equal(r1, r2)


# ------------------------------------------------------------------ cutoff

def test_fit_ignores_rows_after_cutoff():
    """Подмена dem ПОСЛЕ cutoff не меняет модель: та же выборка, те же ответы."""
    shelf = _big_panel()
    cutoff = END - timedelta(days=10)
    cfg = MlCfg()
    pan1, feats = add_features(build_panel(shelf, END, None), cfg.lag)
    cats = snapshot_categories(pan1)
    pan1 = apply_categories(pan1, cats)

    shelf2 = shelf.copy()
    late = pd.to_datetime(shelf2["trade_day"]) > pd.Timestamp(cutoff)
    shelf2.loc[late, "sold_units"] *= 10
    pan2, _ = add_features(build_panel(shelf2, END, None), cfg.lag)
    pan2 = apply_categories(pan2, cats)

    m1, tr1 = fit(pan1, feats, cutoff, cfg)
    m2, tr2 = fit(pan2, feats, cutoff, cfg)
    assert len(tr1) == len(tr2)
    assert tr1["day"].max() <= pd.Timestamp(cutoff)
    te = pan1[pan1["day"] == pd.Timestamp(END)]
    np.testing.assert_allclose(m1.predict(te[feats]), m2.predict(te[feats]))


# ------------------------------------------------------------------ категории

def test_unknown_point_becomes_nan_category_not_crash():
    shelf = _big_panel()
    cfg = MlCfg()
    pan, feats = add_features(build_panel(shelf, END, None), cfg.lag)
    cats = snapshot_categories(pan)
    pan = apply_categories(pan, cats)
    model, _ = fit(pan, feats, END - timedelta(days=5), cfg)

    new = pd.DataFrame(_shelf("P_НОВАЯ", "s0", 40, lambda i: 4.0, grp="Супы?"))
    pan_new, _ = add_features(build_panel(new, END, None), cfg.lag)
    pan_new = apply_categories(pan_new, cats)     # словарь СТАРОЙ модели
    assert pan_new["point_cat"].isna().all()      # незнакомая точка → missing
    te = pan_new[pan_new["day"] == pd.Timestamp(END)]
    p = model.predict(te[feats])                  # не падает
    assert np.isfinite(p).all()


# ------------------------------------------------------------------ floor

def _floor_setup(n_days_new):
    """SKU 'нов' живёт на P1 давно (уровень ~6/д), на P2 — n_days_new дней."""
    rows = _shelf("P1", "нов", 60, lambda i: 6.0)
    rows += _shelf("P1", "фон", 60, lambda i: 10.0)   # объём точки для scale
    rows += _shelf("P2", "фон", 60, lambda i: 10.0)
    if n_days_new:
        rows += _shelf("P2", "нов", n_days_new, lambda i: 2.0)
    return pd.DataFrame(rows)


def test_floor_cold_pair_gets_network_level():
    pan = build_panel(_floor_setup(0), END, None)
    pan, _ = add_features(pan, 3)
    preds = pd.DataFrame([{"point_key": "P2", "sku_key": "нов",
                           "mu_ml": 0.0, "n_obs_days": 0}])
    out = novelty_floor(preds, pan, END, 3).iloc[0]
    assert out["is_novelty"]
    # уровень сети ≈ 6 × point_scale(P2)≈0.63 (10 против медианы 16 с клипом 0.4)
    assert 1 <= out["floor_units"] <= 8


def test_floor_unknown_sku_gets_client_rule_8():
    pan = build_panel(_floor_setup(0), END, None)
    pan, _ = add_features(pan, 3)
    preds = pd.DataFrame([{"point_key": "P2", "sku_key": "НИГДЕ_НЕТ",
                           "mu_ml": 0.0, "n_obs_days": 0}])
    out = novelty_floor(preds, pan, END, 3).iloc[0]
    assert out["floor_units"] == ml_core.NOVELTY_COLD_START


def test_floor_fades_with_history_and_never_lowers():
    pan = build_panel(_floor_setup(0), END, None)
    pan, _ = add_features(pan, 3)
    mature = pd.DataFrame([{"point_key": "P2", "sku_key": "нов",
                            "mu_ml": 12.0,
                            "n_obs_days": ml_core.NOVELTY_FADE_DAYS}])
    out = novelty_floor(mature, pan, END, 3).iloc[0]
    assert out["floor_units"] == 0.0            # история набрана — пол погашен
    # молодая пара: пол есть, но правило — max(модель, пол): модель не режется
    young = pd.DataFrame([{"point_key": "P2", "sku_key": "нов",
                           "mu_ml": 12.0, "n_obs_days": 2}])
    o2 = novelty_floor(young, pan, END, 3).iloc[0]
    assert max(o2["mu_ml"], o2["floor_units"]) == 12.0


# ------------------------------------------------------------------ штамп

def test_needs_refit_rules():
    stamp = dict(feats_version=ml_core.FEATS_VERSION,
                 gbm_params_hash=ml_core._gbm_params_hash(),
                 lag=3, quantile=ml_core.Q,
                 train_cutoff=(END - timedelta(days=3)).isoformat(),
                 excluded_hash=ml_core._excluded_hash(),
                 assortment_policy_sha256=ml_core.assortment_provenance()[
                     "assortment_policy_sha256"])
    import sklearn
    stamp["sklearn_version"] = sklearn.__version__
    cfg = MlCfg()
    assert ml_core._needs_refit(stamp, END - timedelta(days=2), cfg) == ""
    assert "дн" in ml_core._needs_refit(stamp, END + timedelta(days=9), cfg)
    old = dict(stamp, feats_version="ml-0000")
    assert "версия фич" in ml_core._needs_refit(old, END, cfg)
    old_policy = dict(stamp)
    old_policy.pop("assortment_policy_sha256")
    assert "ассортиментная политика" in ml_core._needs_refit(
        old_policy, END - timedelta(days=2), cfg)
    alien = dict(stamp, sklearn_version="0.0")
    assert "sklearn" in ml_core._needs_refit(alien, END - timedelta(days=2), cfg)
