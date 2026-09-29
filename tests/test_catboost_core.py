from datetime import date

import numpy as np
import pandas as pd

from prognoz.predict.catboost_core import CatCfg, add_features, build_panel


def _shelf() -> pd.DataFrame:
    days = pd.date_range("2026-01-01", periods=14, freq="D")
    return pd.DataFrame({
        "point_key": ["point-1"] * len(days),
        "sku_key": ["sku-1"] * len(days),
        "trade_day": days,
        # A later category must never be copied backwards into a test date.
        "grp": ["old"] * 9 + ["future"] * 5,
        "sold_units": np.arange(1, len(days) + 1, dtype=float),
        "lost_units": np.zeros(len(days)),
        "u40_units": np.zeros(len(days)),
        "wo_units": np.zeros(len(days)),
        "avg_price": np.full(len(days), 100.0),
        "supply_gap": [False] * len(days),
        "wo_settled": [True] * len(days),
    })


def test_panel_does_not_backfill_future_group_or_unsettled_label():
    panel = build_panel(_shelf(), date(2026, 1, 12), as_of=date(2026, 1, 8))
    early = panel[panel["day"] == pd.Timestamp("2026-01-08")].iloc[0]
    later = panel[panel["day"] == pd.Timestamp("2026-01-11")].iloc[0]
    assert early["grp"] == "old"
    assert pd.isna(later["dem"])


def test_unsettled_tail_is_not_a_label_but_keeps_observed_sales_signal():
    shelf = _shelf()
    shelf.loc[shelf["trade_day"] == pd.Timestamp("2026-01-08"), "wo_settled"] = False
    shelf.loc[shelf["trade_day"] == pd.Timestamp("2026-01-08"), "lost_units"] = 999.0
    panel = build_panel(shelf, date(2026, 1, 11), as_of=date(2026, 1, 8))
    source = panel[panel["day"] == pd.Timestamp("2026-01-08")].iloc[0]
    assert pd.isna(source["dem"])
    assert source["feature_dem"] == source["sold_units"]

    featured = add_features(panel, CatCfg(lag=3))
    target = featured[featured["day"] == pd.Timestamp("2026-01-11")].iloc[0]
    assert target["lag3"] == source["sold_units"]


def test_features_for_target_do_not_use_target_or_future_demand():
    cfg = CatCfg(lag=3)
    base = add_features(build_panel(_shelf(), date(2026, 1, 14), as_of=date(2026, 1, 14)), cfg)
    mutated = build_panel(_shelf(), date(2026, 1, 14), as_of=date(2026, 1, 14))
    mutated.loc[mutated["day"] >= pd.Timestamp("2026-01-10"), "dem"] = 1_000_000.0
    changed = add_features(mutated, cfg)
    cols = ["lag3", "r28_mean", "wd_mean", "price_r28"]
    left = base[base["day"] == pd.Timestamp("2026-01-12")][cols].reset_index(drop=True)
    right = changed[changed["day"] == pd.Timestamp("2026-01-12")][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)
