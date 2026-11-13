"""Build an immutable production bundle for stacked_guard.

The script consumes a causal shelf parquet for the final HGB/q67/q70 refit and
chronological out-of-fold expert predictions for the residual meta-model.  It
writes model weights plus a hash manifest, never forecasts or client rows.
Run only with PROGNOZ_1C_WRITE=0 and an output located below
PROGNOZ_ML_MODEL_DIR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import catboost
import sklearn
from catboost import CatBoostRegressor, Pool
from sklearn.ensemble import HistGradientBoostingRegressor

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
sys.path.insert(0, str(PROJECT / "analysis" / "etl"))

from prognoz.predict import catboost_core, ml_core, stacked_guard_core as core  # noqa: E402

KEYS = ["point_key", "sku_key", "day"]
TRAINING_START_DEFAULT = date(2024, 11, 1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def full_panel(shelf: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    panel = catboost_core.build_panel(
        shelf, cutoff, as_of=cutoff, excluded=ml_core.load_excluded(),
        target_mode="recovered",
    )
    panel = catboost_core.add_features(panel, catboost_core.CatCfg(lag=3))
    pair = panel.groupby(["point_key", "sku_key"], sort=False)
    panel["surplus_l"] = pair["u40_units"].shift(3) + pair["wo_units"].shift(3)
    return panel.replace([np.inf, -np.inf], np.nan)


def cat_frame(frame: pd.DataFrame, numeric: list[str],
              categorical: list[str]) -> pd.DataFrame:
    out = frame[[*numeric, *categorical]].copy()
    for col in numeric:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in categorical:
        out[col] = out[col].fillna("__MISSING__").astype(str)
    return out.replace([np.inf, -np.inf], np.nan)


def hgb_frame(frame: pd.DataFrame, categories: dict) -> pd.DataFrame:
    out = frame.copy()
    point = out["point_key"].where(out["point_key"].isin(categories["points"]))
    group = out["grp"].where(out["grp"].isin(categories["grps"]))
    out["point_cat"] = pd.Categorical(point, categories=categories["points"])
    out["grp_cat"] = pd.Categorical(group, categories=categories["grps"])
    return out[[*core.EXPERT_NUMERIC, "point_cat", "grp_cat"]]


def cat_model(alpha: float, iterations: int) -> CatBoostRegressor:
    return CatBoostRegressor(
        loss_function=f"Quantile:alpha={alpha}",
        eval_metric=f"Quantile:alpha={alpha}",
        iterations=iterations, depth=7, learning_rate=0.04,
        l2_leaf_reg=10.0, random_seed=7, random_strength=0.5,
        bootstrap_type="Bayesian", bagging_temperature=0.5,
        one_hot_max_size=5000, allow_writing_files=False,
        thread_count=4, verbose=False,
    )


def meta_model() -> CatBoostRegressor:
    return CatBoostRegressor(
        loss_function="Quantile:alpha=0.67",
        eval_metric="Quantile:alpha=0.67",
        iterations=350, depth=6, learning_rate=0.04,
        l2_leaf_reg=10.0, random_seed=7, random_strength=0.5,
        bootstrap_type="Bayesian", bagging_temperature=0.5,
        one_hot_max_size=5000, allow_writing_files=False,
        thread_count=4, verbose=False,
    )


def price_weights(frame: pd.DataFrame) -> tuple[np.ndarray, dict]:
    price = pd.to_numeric(frame["price"], errors="coerce").to_numpy(float)
    if not np.isfinite(price).all() or (price <= 0).any():
        raise RuntimeError("OOF price must be finite and positive")
    low, high = np.quantile(price, [0.01, 0.99])
    low, high = max(float(low), 1.0), max(float(high), float(low) + 1.0)
    clipped = np.clip(price, low, high)
    median = float(np.median(clipped))
    return clipped / median, {"p01": low, "p99": high, "median": median}


def load_oof(path: Path, cutoff: date) -> tuple[pd.DataFrame, str]:
    frame = pd.read_parquet(path)
    required = set(KEYS + core.META_NUMERIC + core.META_CATEGORICAL + [
        "demand", "price", "hgb_qty", "q67_qty", "q70_qty",
        "hgb_train_cutoff", "hgb_feature_as_of", "hgb_source_sha256",
        "q67_block_id", "q70_block_id",
    ])
    missing = sorted(required - set(frame.columns))
    if missing:
        raise RuntimeError(f"OOF parquet misses columns: {missing}")
    frame = frame.copy()
    frame["day"] = pd.to_datetime(frame["day"]).dt.normalize()
    frame = frame[frame["day"] <= pd.Timestamp(cutoff)].copy()
    if frame.empty or frame.duplicated(KEYS).any():
        raise RuntimeError("OOF key contract failed")
    for name in ("hgb_train_cutoff", "q67_train_cutoff", "q70_train_cutoff",
                 "hgb_feature_as_of",
                 "q67_feature_as_of", "q70_feature_as_of"):
        if name not in frame:
            raise RuntimeError(f"OOF provenance column is missing: {name}")
        frame[name] = pd.to_datetime(frame[name], errors="coerce")
    allowed = frame["day"] - pd.Timedelta(days=3)
    if (frame[["hgb_train_cutoff", "q67_train_cutoff", "q70_train_cutoff",
                "hgb_feature_as_of", "q67_feature_as_of",
                "q70_feature_as_of"]].isna().any().any()
            or (frame["hgb_train_cutoff"] > allowed).any()
            or (frame["q67_train_cutoff"] > allowed).any()
            or (frame["q70_train_cutoff"] > allowed).any()
            or not frame["hgb_feature_as_of"].eq(allowed).all()
            or not frame["q67_feature_as_of"].eq(allowed).all()
            or not frame["q70_feature_as_of"].eq(allowed).all()
            or not frame["hgb_train_cutoff"].eq(
                frame["q70_train_cutoff"]).all()
            or not frame["q67_block_id"].astype(str).eq(
                frame["q70_block_id"].astype(str)).all()):
        raise RuntimeError("OOF temporal contract failed")
    sources = frame["hgb_source_sha256"].dropna().astype(str).str.lower().unique()
    if (len(sources) != 1 or len(sources[0]) != 64
            or any(c not in "0123456789abcdef" for c in sources[0])):
        raise RuntimeError("OOF HGB source hash contract failed")
    frame["residual_label"] = frame["demand"] - frame["hgb_qty"]
    return frame.sort_values(KEYS).reset_index(drop=True), sources[0]


def build_bundle(shelf_path: Path, oof_path: Path, output: Path,
                 expert_cutoff: date, meta_cutoff: date,
                 training_start: date = TRAINING_START_DEFAULT) -> dict:
    if os.environ.get("PROGNOZ_1C_WRITE") != "0":
        raise RuntimeError("PROGNOZ_1C_WRITE must be exactly 0")
    model_root_raw = os.environ.get("PROGNOZ_ML_MODEL_DIR", "").strip()
    if not model_root_raw:
        raise RuntimeError("PROGNOZ_ML_MODEL_DIR is required")
    model_root = Path(model_root_raw).resolve()
    output = output.resolve()
    if output == model_root or model_root not in output.parents:
        raise RuntimeError("Output must be below PROGNOZ_ML_MODEL_DIR")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite bundle: {output}")
    if meta_cutoff > expert_cutoff:
        raise RuntimeError("Meta cutoff cannot be later than expert cutoff")

    shelf = pd.read_parquet(shelf_path)
    panel = full_panel(shelf, expert_cutoff)
    train = panel[
        panel["dem"].notna()
        & (panel["day"] >= pd.Timestamp(training_start))
        & (panel["day"] <= pd.Timestamp(expert_cutoff))
    ].copy()
    if len(train) < 1000:
        raise RuntimeError(f"Too few mature expert rows: {len(train)}")
    categories = {
        "points": sorted(train["point_key"].dropna().astype(str).unique().tolist()),
        "grps": sorted(train["grp"].dropna().astype(str).unique().tolist()),
    }

    hgb = HistGradientBoostingRegressor(
        loss="quantile", quantile=0.70,
        categorical_features=["point_cat", "grp_cat"], **ml_core.GBM_PARAMS,
    )
    hgb.fit(hgb_frame(train, categories), train["dem"])
    expert_x = cat_frame(train, core.EXPERT_NUMERIC, core.EXPERT_CATEGORICAL)
    q67, q70 = cat_model(0.67, 500), cat_model(0.70, 508)
    q67.fit(expert_x, train["dem"], cat_features=core.EXPERT_CATEGORICAL)
    q70.fit(expert_x, train["dem"], cat_features=core.EXPERT_CATEGORICAL)

    oof, hgb_source_sha256 = load_oof(oof_path, meta_cutoff)
    actual_meta_cutoff = oof["day"].max().date()
    weights, weight_spec = price_weights(oof)
    meta = meta_model()
    meta.fit(Pool(
        cat_frame(oof, core.META_NUMERIC, core.META_CATEGORICAL),
        oof["residual_label"].to_numpy(float),
        cat_features=core.META_CATEGORICAL, weight=weights,
    ))

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".stacked-guard-", dir=output.parent))
    try:
        joblib.dump(hgb, temp / "hgb.joblib")
        q67.save_model(temp / "q67.cbm")
        q70.save_model(temp / "q70.cbm")
        meta.save_model(temp / "meta.cbm")
        file_manifest = {}
        for filename in core.REQUIRED_MODEL_FILES.values():
            path = temp / filename
            file_manifest[filename] = {
                "bytes": path.stat().st_size, "sha256": sha256(path),
            }
        manifest = {
            "engine": core.ENGINE,
            "bundle_version": core.BUNDLE_VERSION,
            "guard_rule": core.GUARD_RULE,
            "lag": 3,
            "supported_horizon": (
                "D+2 trained; D+3/D+4 reuse weights via causal latest-day adapter"),
            "runtime_feature_lags": list(core.SUPPORTED_LAGS),
            "status": "FROZEN_PROSPECTIVE_CHALLENGER_WITH_HGB_FAIL_SAFE",
            "feature_schema": {
                "expert_numeric": core.EXPERT_NUMERIC,
                "expert_categorical": core.EXPERT_CATEGORICAL,
                "meta_numeric": core.META_NUMERIC,
                "meta_categorical": core.META_CATEGORICAL,
            },
            "hgb_features": [*core.EXPERT_NUMERIC, "point_cat", "grp_cat"],
            "hgb_categories": categories,
            "cutoffs": {
                "training_start_requested": training_start.isoformat(),
                "actual_expert_train_start": train["day"].min().date().isoformat(),
                "expert_train_cutoff": expert_cutoff.isoformat(),
                "max_expert_label_date": train["day"].max().date().isoformat(),
                "meta_cutoff_requested": meta_cutoff.isoformat(),
                "meta_label_cutoff": actual_meta_cutoff.isoformat(),
                "max_meta_label_date": actual_meta_cutoff.isoformat(),
            },
            "training": {
                "expert_rows": int(len(train)),
                "meta_oof_rows": int(len(oof)),
                "points": int(train["point_key"].nunique()),
                "skus": int(train["sku_key"].nunique()),
                "meta_price_weight": weight_spec,
                "params": {
                    "hgb": {"quantile": 0.70, **ml_core.GBM_PARAMS},
                    "q67": {"quantile": 0.67, "iterations": 500, "depth": 7},
                    "q70": {"quantile": 0.70, "iterations": 508, "depth": 7},
                    "meta": {"quantile": 0.67, "iterations": 350, "depth": 6},
                },
            },
            "inputs": {
                "shelf_sha256": sha256(shelf_path),
                "oof_sha256": sha256(oof_path),
                "hgb_oof_source_sha256": hgb_source_sha256,
            },
            "evaluation_status": (
                "Final refit bytes are a frozen prospective challenger; "
                "historical architecture evidence is documented separately."
            ),
            "invariants": {
                "final_formula": (
                    "HGB+1 iff meta_uplift>=1 and q67>=HGB+1 and q70>=HGB+1; "
                    "otherwise HGB"
                ),
                "rowwise_qty_never_below_hgb": (
                    "true at model output; daily preserves a calendar-adjusted "
                    "HGB floor through novelty and emergency day guard"
                ),
                "human_order_feature_used": False,
            },
            "runtime": {
                "python": sys.version.split()[0],
                "pandas": pd.__version__,
                "numpy": np.__version__,
                "catboost": catboost.__version__,
                "sklearn": sklearn.__version__,
                "joblib": joblib.__version__,
            },
            "files": file_manifest,
        }
        (temp / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        try:
            temp.replace(output)
        except PermissionError:
            # Windows can complete the directory move and still report
            # ERROR_ACCESS_DENIED while finalising the source handle.  Accept
            # that state only after the same strict manifest validation used
            # by production; every other partial state still fails closed.
            if temp.exists() or not output.is_dir():
                raise
            written = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8"))
            if written != manifest:
                raise RuntimeError("Promoted manifest differs from staging")
            core._validate_manifest(output, written, target=None)
        return manifest
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shelf", type=Path, required=True)
    parser.add_argument("--oof", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expert-cutoff", type=date.fromisoformat, required=True)
    parser.add_argument("--meta-cutoff", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--training-start", type=date.fromisoformat,
        default=TRAINING_START_DEFAULT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_bundle(
        args.shelf, args.oof, args.output,
        args.expert_cutoff, args.meta_cutoff, args.training_start,
    )
    print(json.dumps({
        "status": manifest["status"],
        "output": str(args.output.resolve()),
        "expert_rows": manifest["training"]["expert_rows"],
        "meta_oof_rows": manifest["training"]["meta_oof_rows"],
        "manifest_sha256": core._manifest_digest(manifest),
        "files": manifest["files"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
