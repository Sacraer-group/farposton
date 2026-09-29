from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from prognoz.predict import stacked_guard_core as guard


def test_consensus_rule_is_exact_and_never_below_hgb():
    hgb = np.array([4.4, 4.5, 4.5, 4.5, 8.0])
    q67 = np.array([5.0, 6.0, 5.0, 4.0, 10.0])
    q70 = np.array([5.0, 6.0, 4.0, 5.0, 10.0])
    meta = np.array([0.49, 0.50, 1.20, 2.00, -99.0])

    qty, applied = guard.consensus_add1(hgb, q67, q70, meta)

    # Half-up HGB is [4,5,5,5,8]. Only row 2 has a positive meta signal and
    # both experts at least one unit above the HGB champion.
    np.testing.assert_array_equal(qty, [4, 6, 5, 5, 8])
    np.testing.assert_array_equal(applied, [False, True, False, False, False])
    assert (qty >= guard._half_up_nonnegative(hgb)).all()


def test_consensus_guard_cannot_increase_underorder():
    demand = np.array([0.0, 5.0, 8.5, 20.0])
    hgb = np.array([1.0, 4.0, 8.0, 12.0])
    qty, _ = guard.consensus_add1(
        hgb, hgb + 2, hgb + 1, np.ones(len(hgb)),
    )
    before = np.clip(demand - guard._half_up_nonnegative(hgb), 0, None)
    after = np.clip(demand - qty, 0, None)
    assert (after <= before).all()


def _manifest(root, *, expert_cutoff="2026-08-17", meta_cutoff="2026-07-30"):
    files = {}
    for filename in guard.REQUIRED_MODEL_FILES.values():
        path = root / filename
        path.write_bytes(("fake-" + filename).encode())
        files[filename] = {
            "bytes": path.stat().st_size,
            "sha256": guard._sha256(path),
        }
    return {
        "engine": guard.ENGINE,
        "bundle_version": guard.BUNDLE_VERSION,
        "guard_rule": guard.GUARD_RULE,
        "lag": guard.SUPPORTED_LAG,
        "feature_schema": {
            "expert_numeric": guard.EXPERT_NUMERIC,
            "expert_categorical": guard.EXPERT_CATEGORICAL,
            "meta_numeric": guard.META_NUMERIC,
            "meta_categorical": guard.META_CATEGORICAL,
        },
        "files": files,
        "cutoffs": {
            "expert_train_cutoff": expert_cutoff,
            "meta_label_cutoff": meta_cutoff,
        },
    }


def test_manifest_hash_and_target_cutoff_fail_closed(tmp_path):
    manifest = _manifest(tmp_path)
    guard._validate_manifest(tmp_path, manifest, target=date(2026, 8, 20))

    (tmp_path / "q70.cbm").write_bytes(b"tampered")
    with pytest.raises(guard.BundleError, match="hash mismatch"):
        guard._validate_manifest(tmp_path, manifest, target=date(2026, 8, 20))

    manifest = _manifest(tmp_path, expert_cutoff="2026-08-18")
    with pytest.raises(guard.BundleError, match="information boundary"):
        guard._validate_manifest(tmp_path, manifest, target=date(2026, 8, 20))

    manifest = _manifest(tmp_path, expert_cutoff="2026-08-17")
    with pytest.raises(guard.BundleError, match="information boundary"):
        guard._validate_manifest(
            tmp_path, manifest, target=date(2026, 8, 20), information_lag=4)


def test_bundle_directory_is_per_engine_and_lag(monkeypatch, tmp_path):
    monkeypatch.delenv("PROGNOZ_STACKED_GUARD_BUNDLE", raising=False)
    monkeypatch.setenv("PROGNOZ_ML_MODEL_DIR", str(tmp_path))
    assert guard.bundle_dir(3) == tmp_path / "stacked_guard" / "lag_3"


def test_manifest_release_pin_fails_before_model_load(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("PROGNOZ_STACKED_GUARD_BUNDLE", str(tmp_path))
    monkeypatch.setenv("PROGNOZ_STACKED_GUARD_MANIFEST_SHA256", "0" * 64)
    with pytest.raises(guard.BundleError, match="pinned release"):
        guard.load_bundle(target=date(2026, 8, 20))


def test_non_packaged_bundle_requires_explicit_pin(monkeypatch, tmp_path):
    manifest = _manifest(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("PROGNOZ_STACKED_GUARD_BUNDLE", str(tmp_path))
    monkeypatch.delenv("PROGNOZ_STACKED_GUARD_MANIFEST_SHA256", raising=False)
    with pytest.raises(guard.BundleError, match="requires"):
        guard.load_bundle(target=date(2026, 8, 20))


def test_optional_bundle_is_pinned_or_fails_closed(monkeypatch):
    monkeypatch.delenv("PROGNOZ_STACKED_GUARD_BUNDLE", raising=False)
    monkeypatch.delenv("PROGNOZ_ML_MODEL_DIR", raising=False)
    monkeypatch.delenv("PROGNOZ_STACKED_GUARD_MANIFEST_SHA256", raising=False)
    manifest = guard.bundle_dir() / "manifest.json"
    if not manifest.exists():
        # The neutral demo ships HGB, not the historical customer-trained bundle.
        with pytest.raises(guard.BundleError, match="No stacked guard manifest"):
            guard.load_bundle(target=date(2026, 8, 23))
        return
    assert guard._manifest_sha256(manifest) == guard.PACKAGED_MANIFEST_SHA256
    bundle = guard.load_bundle(target=date(2026, 8, 23))
    assert bundle.manifest["engine"] == guard.ENGINE
    longer = guard.load_bundle(
        target=date(2026, 8, 25), cfg=guard.GuardCfg(lag=5))
    assert longer.root == bundle.root
    assert longer.manifest["lag"] == guard.BUNDLE_LAG


def test_missing_bundle_uses_hgb_fail_safe(monkeypatch):
    def missing(*args, **kwargs):
        raise guard.BundleError("bundle absent")

    base = pd.DataFrame({
        "point_key": ["P"], "sku_key": ["S"], "mu_ml": [7.0],
        "n_obs_days": [100], "floor_units": [0.0], "is_novelty": [False],
    })
    monkeypatch.setattr(guard, "load_bundle", missing)
    fallback_cfg = []

    def hgb_fallback(*args, **kwargs):
        fallback_cfg.append(kwargs["cfg"])
        return (
            base.copy(),
            {"stamp": {"train_cutoff": "2026-08-17", "n_train_rows": 1234},
             "refitted": False},
        )

    monkeypatch.setattr(guard.ml_core, "ml_forecast", hgb_fallback)

    preds, meta = guard.stacked_guard_forecast(
        None, date(2026, 8, 21), cfg=guard.GuardCfg(lag=4),
        shelf_df=pd.DataFrame(),
    )
    assert preds.loc[0, "mu_ml"] == 7
    assert preds.loc[0, "hgb_qty"] == 7
    assert preds.loc[0, "guard_mode"] == "hgb_fail_safe"
    assert meta["stamp"]["engine"] == "ml"
    assert meta["stamp"]["requested_engine"] == "stacked_guard"
    assert meta["engine"] == "ml"
    assert "bundle absent" in meta["stamp"]["fallback_reason"]
    assert fallback_cfg[0].lag == 4


@pytest.mark.parametrize("lag", [1, 2, 6, 7])
def test_guard_rejects_unsupported_horizons_before_io(lag):
    with pytest.raises(guard.BundleError, match="lag=3/4/5"):
        guard.stacked_guard_forecast(
            None, date(2026, 8, 20), cfg=guard.GuardCfg(lag=lag),
            shelf_df=pd.DataFrame(),
        )


@pytest.mark.parametrize("lag", guard.SUPPORTED_LAGS)
def test_compact_inference_history_preserves_target_features(lag):
    days = pd.date_range("2026-01-01", "2026-08-20", freq="D")
    shelf = pd.DataFrame({
        "point_key": ["P"] * len(days),
        "sku_key": ["S"] * len(days),
        "trade_day": days,
        "grp": ["готовая еда"] * len(days),
        "sold_units": (np.arange(len(days)) % 11).astype(float),
        "lost_units": np.zeros(len(days)),
        "u40_units": (np.arange(len(days)) % 3 == 0).astype(float),
        "wo_units": (np.arange(len(days)) % 17 == 0).astype(float),
        "avg_price": np.full(len(days), 199.0),
        "supply_gap": [False] * len(days),
        "wo_settled": [True] * len(days),
    })
    target = date(2026, 8, 23)
    full = guard._full_feature_panel(shelf, target, lag, compact=False)
    compact = guard._full_feature_panel(shelf, target, lag, compact=True)
    cols = [*guard.EXPERT_NUMERIC, "point_key", "sku_key", "grp"]
    left = full[full["day"] == pd.Timestamp(target)][cols].reset_index(drop=True)
    right = compact[compact["day"] == pd.Timestamp(target)][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)


@pytest.mark.parametrize("lag", guard.SUPPORTED_LAGS)
def test_horizon_adapter_is_causal_and_maps_latest_complete_day(lag):
    target = date(2026, 8, 23)
    days = pd.date_range("2026-05-01", target, freq="D")
    shelf = pd.DataFrame({
        "point_key": ["P"] * len(days),
        "sku_key": ["S"] * len(days),
        "trade_day": days,
        "grp": ["готовая еда"] * len(days),
        "sold_units": (np.arange(len(days)) % 11).astype(float),
        "lost_units": np.zeros(len(days)),
        "u40_units": np.zeros(len(days)),
        "wo_units": np.zeros(len(days)),
        "avg_price": np.full(len(days), 199.0),
        "supply_gap": [False] * len(days),
        "wo_settled": [True] * len(days),
    })
    changed_future = shelf.copy()
    cutoff = pd.Timestamp(target) - pd.Timedelta(days=lag)
    changed_future.loc[changed_future["trade_day"] > cutoff, "sold_units"] = 99999.0

    base = guard._full_feature_panel(shelf, target, lag, compact=False)
    changed = guard._full_feature_panel(changed_future, target, lag, compact=False)
    cols = [*guard.EXPERT_NUMERIC, "point_key", "sku_key", "grp"]
    base_row = base[base["day"] == pd.Timestamp(target)][cols].reset_index(drop=True)
    changed_row = changed[changed["day"] == pd.Timestamp(target)][cols].reset_index(drop=True)
    pd.testing.assert_frame_equal(base_row, changed_row, check_dtype=False)

    target_row = base[base["day"] == pd.Timestamp(target)].iloc[0]
    assert target_row["lag3"] == target_row[f"lag{lag}"]
