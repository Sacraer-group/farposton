"""Production inference for the frozen stacked consensus guard.

The guard keeps the reproduced HGB forecast as a row-level floor.  It adds exactly
one unit only when the residual meta-model and both independent CatBoost
quantile experts agree that the HGB quantity is too low.  Consequently the
model output cannot increase under-order units relative to HGB on the same row;
the daily pipeline carries a calendar-adjusted HGB floor through downstream guards.

The frozen bundle was trained with the D+2 feature schema (``lag3``).  Nightly
D+2/D+3/D+4 releases reuse those exact verified weights.  For the longer
horizons the most recent issue-time observation (target-4/target-5) is adapted
into the bundle's ``lag3`` input slot; every rolling feature is still cut at the
same issue-time boundary.  This is a same-model operational forecast, not a
claim that separate D+3/D+4 accuracy has already been measured.

Model artefacts are loaded only from a hash-verified bundle.  A missing, stale
or tampered bundle falls back to the existing HGB engine; it never silently
uses an unverified CatBoost file.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from prognoz.assortment import filter_frame, filter_pairs
from prognoz.predict import catboost_core, ml_core

ENGINE = "stacked_guard"
BUNDLE_VERSION = "stacked-consensus-add1-v1"
GUARD_RULE = "meta_consensus_add1"
BUNDLE_LAG = 3
SUPPORTED_LAGS = (3, 4, 5)
# Фактический лаг признаков может быть больше номинального: если продажи за
# issue−1 ещё не загружены, панель строится от последнего ПОЛНОГО дня (17.09:
# заявки на день вперёд + продажи с отставанием в сутки давали lag_recent=NaN
# у 100% пар). Дальше недели данных — выпуск ML не делаем.
MAX_EFFECTIVE_LAG = 7
# Compatibility name used by the frozen trainer/tests and encoded in manifest.
SUPPORTED_LAG = BUNDLE_LAG
INFERENCE_LOOKBACK_DAYS = 120
PACKAGED_MANIFEST_SHA256 = "f225994ea43d9713cdc09f4058d1746223b1e9ee00c9f52d42d7b3ed9d5a3bce"

EXPERT_NUMERIC = [
    "lag3", "lag7", "lag14", "lag21", "lag28",
    "r28_mean", "r28_med", "r28_std", "r28_q75",
    "wd_mean", "wd_med", "wd_std", "wd_q75",
    "price_r28", "u40_share28", "wo_rate28", "surplus_l",
    "weekday", "month", "week",
]
EXPERT_CATEGORICAL = ["point_key", "grp"]
META_EXTRA_NUMERIC = [
    "hgb_qty", "q70_qty", "q67_qty", "q70_minus_hgb",
    "q67_minus_hgb", "q70_minus_q67", "expert_mean",
]
META_NUMERIC = [*EXPERT_NUMERIC, *META_EXTRA_NUMERIC]
META_CATEGORICAL = ["point_key", "sku_key", "grp"]

REQUIRED_MODEL_FILES = {
    "hgb": "hgb.joblib",
    "q67": "q67.cbm",
    "q70": "q70.cbm",
    "meta": "meta.cbm",
}


class BundleError(RuntimeError):
    """The production model bundle is absent, incompatible or corrupted."""


@dataclass(frozen=True)
class GuardCfg:
    # Runtime information lag: D+2/D+3/D+4 -> 3/4/5.  The binary bundle remains
    # the single lag_3 release; see the feature adapter in _full_feature_panel.
    lag: int = BUNDLE_LAG
    allow_hgb_fallback: bool = True


@dataclass(frozen=True)
class LoadedBundle:
    root: Path
    manifest: dict[str, Any]
    hgb: object
    q67: object
    q70: object
    meta: object


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bundle_dir(lag: int = BUNDLE_LAG) -> Path:
    """Return the exact bundle directory used by inference.

    ``PROGNOZ_STACKED_GUARD_BUNDLE`` is an explicit immutable bundle override.
    ``PROGNOZ_ML_MODEL_DIR`` keeps all runtime artefacts inside the configured
    model root.  A normal checkout uses the reviewed bundle shipped in
    ``models/``.
    """
    explicit = os.environ.get("PROGNOZ_STACKED_GUARD_BUNDLE", "").strip()
    if explicit:
        return Path(explicit)
    model_root = os.environ.get("PROGNOZ_ML_MODEL_DIR", "").strip()
    if model_root:
        return Path(model_root) / ENGINE / f"lag_{lag}"
    return _project_root() / "models" / ENGINE / f"lag_{lag}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_digest(manifest: dict[str, Any]) -> str:
    payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest_sha256(path: Path) -> str:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError("Cannot read stacked guard manifest") from exc
    return _manifest_digest(manifest)


def _half_up_nonnegative(values) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if not np.isfinite(arr).all():
        raise BundleError("Model returned NaN or infinite values")
    return np.floor(np.clip(arr, 0, None) + 0.5).astype(np.int64)


def consensus_add1(hgb_qty, q67_qty, q70_qty, meta_raw) -> tuple[np.ndarray, np.ndarray]:
    """Apply the frozen row-level rule and return ``(qty, applied)``.

    The meta prediction is rounded after clipping to [0, 2], exactly as in the
    frozen annual experiment.  The final action remains capped at +1 because
    the second unit failed the economic test.
    """
    hgb = _half_up_nonnegative(hgb_qty)
    q67 = _half_up_nonnegative(q67_qty)
    q70 = _half_up_nonnegative(q70_qty)
    uplift_signal = _half_up_nonnegative(np.clip(np.asarray(meta_raw, dtype=float), 0, 2))
    applied = (uplift_signal >= 1) & (q67 >= hgb + 1) & (q70 >= hgb + 1)
    result = hgb + applied.astype(np.int64)
    if (result < hgb).any():
        raise AssertionError("Stacked guard violated its HGB floor")
    return result, applied


def _validate_manifest(root: Path, manifest: dict[str, Any], *,
                       target: date | None,
                       information_lag: int = BUNDLE_LAG) -> None:
    if information_lag not in SUPPORTED_LAGS:
        raise BundleError(f"Unsupported information lag: {information_lag}")
    if manifest.get("engine") != ENGINE:
        raise BundleError("Unexpected bundle engine")
    if manifest.get("bundle_version") != BUNDLE_VERSION:
        raise BundleError("Unsupported bundle version")
    if manifest.get("guard_rule") != GUARD_RULE:
        raise BundleError("Unexpected guard rule")
    if manifest.get("lag") != BUNDLE_LAG:
        raise BundleError("Stacked guard bundle must use the frozen lag=3 schema")
    schema = manifest.get("feature_schema", {})
    expected_schema = {
        "expert_numeric": EXPERT_NUMERIC,
        "expert_categorical": EXPERT_CATEGORICAL,
        "meta_numeric": META_NUMERIC,
        "meta_categorical": META_CATEGORICAL,
    }
    if schema != expected_schema:
        raise BundleError("Bundle feature schema differs from production code")

    files = manifest.get("files")
    if not isinstance(files, dict):
        raise BundleError("Bundle file manifest is missing")
    for role, filename in REQUIRED_MODEL_FILES.items():
        item = files.get(filename)
        path = root / filename
        if not isinstance(item, dict) or not path.is_file():
            raise BundleError(f"Bundle misses {role} model: {filename}")
        if item.get("bytes") != path.stat().st_size or item.get("sha256") != _sha256(path):
            raise BundleError(f"Bundle hash mismatch: {filename}")

    cutoffs = manifest.get("cutoffs", {})
    try:
        expert_cutoff = date.fromisoformat(cutoffs["expert_train_cutoff"])
        max_expert_label_date = date.fromisoformat(
            cutoffs.get("max_expert_label_date", cutoffs["expert_train_cutoff"]))
        meta_cutoff = date.fromisoformat(cutoffs["meta_label_cutoff"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BundleError("Bundle cutoffs are missing or invalid") from exc
    if meta_cutoff > expert_cutoff:
        raise BundleError("Meta label cutoff is later than expert cutoff")
    if max_expert_label_date > expert_cutoff:
        raise BundleError("Maximum expert label date is later than expert cutoff")
    if target is not None:
        allowed = target - timedelta(days=information_lag)
        if expert_cutoff > allowed or meta_cutoff > allowed:
            raise BundleError("Bundle was trained after the target information boundary")
        # The requested cutoff may be newer than the last actually mature
        # label.  Freshness is therefore measured from the observed label,
        # never from a requested date that could make a stale refit look new.
        if (allowed - max_expert_label_date).days > 14:
            raise BundleError("Stacked expert bundle is older than 14 days")


def load_bundle(*, target: date | None = None, cfg: GuardCfg = GuardCfg()) -> LoadedBundle:
    if cfg.lag not in SUPPORTED_LAGS:
        raise BundleError("Stacked guard supports only D+2/D+3/D+4 (lag=3/4/5)")
    # D+3/D+4 intentionally reuse the exact same pinned binary bundle.
    root = bundle_dir(BUNDLE_LAG)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise BundleError(f"No stacked guard manifest at {manifest_path}")
    expected_manifest = os.environ.get(
        "PROGNOZ_STACKED_GUARD_MANIFEST_SHA256", "").strip().lower()
    packaged = _project_root() / "models" / ENGINE / f"lag_{BUNDLE_LAG}"
    if not expected_manifest and root.resolve() == packaged.resolve():
        expected_manifest = PACKAGED_MANIFEST_SHA256
    if not expected_manifest:
        raise BundleError(
            "Non-packaged stacked bundle requires "
            "PROGNOZ_STACKED_GUARD_MANIFEST_SHA256")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError("Cannot read stacked guard manifest") from exc
    if expected_manifest and _manifest_digest(manifest) != expected_manifest:
        raise BundleError("Stacked guard manifest hash differs from the pinned release")
    _validate_manifest(root, manifest, target=target,
                       information_lag=cfg.lag)

    import joblib
    from catboost import CatBoostRegressor

    try:
        hgb = joblib.load(root / REQUIRED_MODEL_FILES["hgb"])
        models = []
        for role in ("q67", "q70", "meta"):
            model = CatBoostRegressor()
            model.load_model(root / REQUIRED_MODEL_FILES[role])
            models.append(model)
    except Exception as exc:  # noqa: BLE001 - converted to fail-closed domain error
        raise BundleError(f"Cannot load stacked guard models: {exc}") from exc
    return LoadedBundle(root, manifest, hgb, models[0], models[1], models[2])


def _compact_inference_shelf(shelf_df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Keep enough history for every feature plus one metadata-only seed row."""
    raw = filter_frame(
        shelf_df, "metric_scope", date_col="trade_day")
    raw["trade_day"] = pd.to_datetime(raw["trade_day"]).dt.normalize()
    start = pd.Timestamp(as_of) - pd.Timedelta(days=INFERENCE_LOOKBACK_DAYS)
    recent = raw[raw["trade_day"] >= start].copy()
    before = raw[raw["trade_day"] < start].sort_values(
        ["point_key", "sku_key", "trade_day"])
    if before.empty:
        return recent
    seed = before.groupby(["point_key", "sku_key"], sort=False).tail(1).copy()
    seed["trade_day"] = start - pd.Timedelta(days=1)
    for col in ("sold_units", "lost_units", "u40_units", "wo_units"):
        seed[col] = 0.0
    seed["supply_gap"] = True
    seed["wo_settled"] = False
    return pd.concat([seed, recent], ignore_index=True)


def last_complete_day(shelf_df: pd.DataFrame) -> date | None:
    """Последний день полки с загруженными продажами (см. shelf.core.flag_data_missing).
    Без служебных колонок — None: полка считается полной до своего максимума."""
    if shelf_df is None or shelf_df.empty:
        return None
    days = pd.to_datetime(shelf_df["trade_day"])
    if "data_missing" in shelf_df.columns:
        known = days[~shelf_df["data_missing"].fillna(False).astype(bool)]
        return known.max().date() if not known.empty else None
    has_data = pd.Series(False, index=shelf_df.index)
    for col in ("n_lines", "sold_units"):
        if col in shelf_df.columns:
            has_data |= pd.to_numeric(shelf_df[col], errors="coerce").fillna(0) > 0
    return days[has_data].max().date() if has_data.any() else None


def effective_lag(shelf_df: pd.DataFrame, target: date, lag: int) -> tuple[date, int]:
    """(as_of, лаг): номинальный as_of = target − lag, но не позже последнего
    полного дня продаж. Лаг больше MAX_EFFECTIVE_LAG — BundleError (фолбэк)."""
    as_of = target - timedelta(days=lag)
    complete = last_complete_day(shelf_df)
    if complete is not None and complete < as_of:
        as_of = complete
    eff = (target - as_of).days
    if eff > MAX_EFFECTIVE_LAG:
        raise BundleError(
            f"Последний полный день продаж {as_of} слишком далёк от цели {target} "
            f"(лаг {eff} > {MAX_EFFECTIVE_LAG}) — данные не загружены")
    return as_of, eff


def _full_feature_panel(shelf_df: pd.DataFrame, target: date, lag: int,
                        *, compact: bool = True) -> pd.DataFrame:
    if lag not in SUPPORTED_LAGS:
        raise BundleError("Feature lag must be one of 3, 4 or 5")
    as_of, eff = effective_lag(shelf_df, target, lag)
    source = (_compact_inference_shelf(shelf_df, as_of)
              if compact else shelf_df)
    panel = catboost_core.build_panel(
        source, target, as_of=as_of, excluded=ml_core.load_excluded(),
        target_mode="recovered",
    )
    panel = catboost_core.add_features(panel, catboost_core.CatCfg(lag=eff))
    recent_col = f"lag{eff}"
    if recent_col not in panel.columns:
        raise BundleError(f"Feature panel misses the recent lag column {recent_col}")
    if eff != BUNDLE_LAG:
        # Frozen experts expect the literal name ``lag3``.  Its operational
        # meaning is "latest complete day available when the issue is made".
        # For D+3/D+4 that value is target-4/target-5; when the sales feed lags,
        # it is the last day the shelf actually knows.
        panel[f"lag{BUNDLE_LAG}"] = panel[recent_col]
    pair = panel.groupby(["point_key", "sku_key"], sort=False)
    panel["surplus_l"] = pair["u40_units"].shift(eff) + pair["wo_units"].shift(eff)
    panel = panel.replace([np.inf, -np.inf], np.nan)
    panel.attrs.update({"nominal_lag": lag, "effective_lag": eff,
                        "data_as_of": as_of.isoformat()})
    return panel


def _cat_frame(frame: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    out = frame[[*numeric, *categorical]].copy()
    for col in numeric:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in categorical:
        out[col] = out[col].fillna("__MISSING__").astype(str)
    return out.replace([np.inf, -np.inf], np.nan)


def _hgb_frame(frame: pd.DataFrame, manifest: dict[str, Any]) -> pd.DataFrame:
    categories = manifest.get("hgb_categories", {})
    points, groups = categories.get("points"), categories.get("grps")
    if not isinstance(points, list) or not isinstance(groups, list):
        raise BundleError("HGB categorical dictionary is missing")
    out = frame.copy()
    point = out["point_key"].where(out["point_key"].isin(points))
    group = out["grp"].where(out["grp"].isin(groups))
    out["point_cat"] = pd.Categorical(point, categories=points)
    out["grp_cat"] = pd.Categorical(group, categories=groups)
    features = manifest.get("hgb_features")
    expected = [*EXPERT_NUMERIC, "point_cat", "grp_cat"]
    if features != expected:
        raise BundleError("HGB feature order differs from production code")
    return out[features]


def predict_bundle(bundle: LoadedBundle, shelf_df: pd.DataFrame, target: date, *,
                   pairs: set[tuple[str, str]] | None = None,
                   cfg: GuardCfg = GuardCfg(),
                   panel: pd.DataFrame | None = None) -> pd.DataFrame:
    pairs = filter_pairs(pairs, "factory_order")
    panel = (panel if panel is not None
             else _full_feature_panel(shelf_df, target, cfg.lag))
    test = panel[panel["day"] == pd.Timestamp(target)].copy()
    if test.empty:
        raise BundleError(f"Feature panel has no rows for {target}")

    hgb_raw = bundle.hgb.predict(_hgb_frame(test, bundle.manifest))
    expert_frame = _cat_frame(test, EXPERT_NUMERIC, EXPERT_CATEGORICAL)
    q67_raw = bundle.q67.predict(expert_frame)
    q70_raw = bundle.q70.predict(expert_frame)
    hgb_qty = _half_up_nonnegative(hgb_raw)
    q67_qty = _half_up_nonnegative(q67_raw)
    q70_qty = _half_up_nonnegative(q70_raw)

    test["hgb_qty"] = hgb_qty
    test["q67_qty"] = q67_qty
    test["q70_qty"] = q70_qty
    test["q70_minus_hgb"] = q70_qty - hgb_qty
    test["q67_minus_hgb"] = q67_qty - hgb_qty
    test["q70_minus_q67"] = q70_qty - q67_qty
    test["expert_mean"] = 0.5 * (q70_qty + q67_qty)
    meta_raw = bundle.meta.predict(_cat_frame(test, META_NUMERIC, META_CATEGORICAL))
    qty, applied = consensus_add1(hgb_qty, q67_qty, q70_qty, meta_raw)
    test["mu_ml"] = qty.astype(float)
    test["guard_applied"] = applied
    test["guard_mode"] = "stacked_consensus"

    eff_lag = int(panel.attrs.get("effective_lag", cfg.lag))
    raw = shelf_df.copy()
    raw["trade_day"] = pd.to_datetime(raw["trade_day"]).dt.normalize()
    allowed = raw["trade_day"] <= pd.Timestamp(target - timedelta(days=eff_lag))
    valid = (allowed
             & ~raw["supply_gap"].fillna(False).astype(bool)
             & raw["wo_settled"].fillna(False).astype(bool))
    if "data_missing" in raw.columns:
        valid &= ~raw["data_missing"].fillna(False).astype(bool)
    excluded = ml_core.load_excluded()
    if excluded:
        valid &= ~raw["trade_day"].dt.date.isin(excluded)
    observed = raw[valid].groupby(["point_key", "sku_key"]).size()
    test["n_obs_days"] = [int(observed.get((r.point_key, r.sku_key), 0))
                          for r in test.itertuples()]
    recent_col = f"lag{eff_lag}"
    result = test[[
        "point_key", "sku_key", "grp", "mu_ml", "n_obs_days",
        "r28_mean", "wd_mean", recent_col, "lag7", "hgb_qty", "q67_qty",
        "q70_qty", "guard_applied", "guard_mode",
    ]].rename(columns={recent_col: "lag_recent"})

    if pairs:
        # Ответ — только матрица заявки: панель хранит годовую вселенную пар
        # (в т.ч. давно выбывшие), и сторож считал долю пустых признаков по ней
        # (803 пары вместо ~440 живых) — постоянный «пол» ~45 п.п. (13–17.09).
        keep = [(p, s) in pairs for p, s in zip(result["point_key"], result["sku_key"])]
        result = result[keep].reset_index(drop=True)
        existing = set(zip(result["point_key"], result["sku_key"]))
        missing = sorted(pairs - existing)
        if missing:
            extra = pd.DataFrame(missing, columns=["point_key", "sku_key"])
            extra = extra.assign(mu_ml=0.0, n_obs_days=0, hgb_qty=0,
                                 q67_qty=0, q70_qty=0, guard_applied=False,
                                 guard_mode="cold_start")
            result = pd.concat([result, extra], ignore_index=True)
    return result


def _fallback_hgb(con, target: date, shelf_df: pd.DataFrame | None,
                  pairs: set[tuple[str, str]] | None, refit: bool,
                  reason: str, cfg: GuardCfg) -> tuple[pd.DataFrame, dict[str, Any]]:
    preds, meta = ml_core.ml_forecast(
        con, target, cfg=ml_core.MlCfg(lag=cfg.lag),
        shelf_df=shelf_df, pairs=pairs, refit=refit,
    )
    preds = preds.copy()
    preds["hgb_qty"] = _half_up_nonnegative(preds["mu_ml"])
    preds["guard_applied"] = False
    preds["guard_mode"] = "hgb_fail_safe"
    stamp = dict(meta["stamp"])
    stamp.update({"engine": "ml", "requested_engine": ENGINE,
                  "bundle_version": BUNDLE_VERSION,
                  "guard_rule": GUARD_RULE, "guard_mode": "hgb_fail_safe",
                  "fallback_reason": reason[:500]})
    return preds, {**meta, "stamp": stamp, "engine": "ml",
                   "requested_engine": ENGINE,
                   "guard_mode": "hgb_fail_safe"}


def stacked_guard_forecast(con, target: date, *, cfg: GuardCfg = GuardCfg(),
                           shelf_df: pd.DataFrame | None = None,
                           pairs: set[tuple[str, str]] | None = None,
                           refit: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Facade used by :mod:`daily`: verified bundle or exact HGB fail-safe."""
    if cfg.lag not in SUPPORTED_LAGS:
        raise BundleError("Stacked guard supports only D+2/D+3/D+4 (lag=3/4/5)")
    if shelf_df is None:
        from prognoz.predict.demand import load_shelf
        cutoff = target - timedelta(days=cfg.lag)
        shelf_df = load_shelf(con, cutoff + timedelta(days=1))
    try:
        bundle = load_bundle(target=target, cfg=cfg)
        panel = _full_feature_panel(shelf_df, target, cfg.lag)
        preds = predict_bundle(
            bundle, shelf_df, target, pairs=pairs, cfg=cfg, panel=panel)
    except BundleError as exc:
        if not cfg.allow_hgb_fallback:
            raise
        return _fallback_hgb(con, target, shelf_df, pairs, refit, str(exc), cfg)

    preds = ml_core.novelty_floor(preds, panel, target, cfg.lag)
    manifest = bundle.manifest
    stamp = {
        "engine": ENGINE,
        "bundle_version": manifest["bundle_version"],
        "guard_rule": manifest["guard_rule"],
        "guard_mode": "stacked_consensus",
        "lag": cfg.lag,
        "feature_lag": cfg.lag,
        "effective_lag": int(panel.attrs.get("effective_lag", cfg.lag)),
        "data_as_of": str(panel.attrs.get(
            "data_as_of", (target - timedelta(days=cfg.lag)).isoformat())),
        "bundle_lag": BUNDLE_LAG,
        "horizon_days": cfg.lag - 1,
        "quantile": 0.70,
        "train_cutoff": manifest["cutoffs"]["expert_train_cutoff"],
        "max_label_date": manifest["cutoffs"].get(
            "max_expert_label_date", manifest["cutoffs"]["expert_train_cutoff"]),
        "n_train_rows": int(manifest.get("training", {}).get("expert_rows", 0)),
        "bundle_manifest_sha256": _manifest_digest(manifest),
    }
    return preds, {"stamp": stamp, "refitted": False, "engine": ENGINE,
                   "guard_mode": "stacked_consensus"}


def load_prev_stamp() -> dict[str, Any] | None:
    path = bundle_dir() / "prev_manifest.json"
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return {
            "train_cutoff": manifest["cutoffs"]["expert_train_cutoff"],
            "n_train_rows": int(manifest.get("training", {}).get("expert_rows", 0)),
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def explain_row(row) -> str:
    if getattr(row, "guard_mode", "") == "hgb_fail_safe":
        return "HGB champion: stacked bundle недоступен, включён безопасный резерв"
    base = getattr(row, "hgb_qty", None)
    if bool(getattr(row, "guard_applied", False)):
        return f"stacked guard: HGB {base:g} + 1; q0.67, q0.70 и meta согласны"
    if base is not None:
        return f"stacked guard: оставлен HGB {base:g}; согласия на повышение нет"
    return "stacked guard: HGB champion с защитным consensus-правилом"
