"""Causal CatBoost forecast core.

This module is deliberately independent from the legacy HGB artefacts.  Each
lead time has its own model directory, all labels are masked until write-offs
settle, and pair metadata is only carried forward.  It never writes a forecast
log: publication is handled by :mod:`prognoz.predict.release`.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from prognoz.assortment import (filter_frame, filter_pairs,
                                provenance as assortment_provenance)

LAG_DEFAULT = 3
QUANTILE_DEFAULT = 0.70
REFIT_DAYS = 7
FEATURES_VERSION = "catboost-2026-08-23-dual-demand-v2"
NUMERIC_FEATURES = [
    "lag3", "lag7", "lag14", "lag21", "lag28",
    "r28_mean", "r28_med", "r28_std", "r28_q75",
    "wd_mean", "wd_med", "wd_std", "wd_q75",
    "price_r28", "u40_share28", "wo_rate28", "weekday", "month", "week",
]
CAT_FEATURES = ["point_key", "grp"]


@dataclass(frozen=True)
class CatCfg:
    lag: int = LAG_DEFAULT
    quantile: float = QUANTILE_DEFAULT
    iterations: int = 700
    depth: int = 7
    learning_rate: float = 0.04
    l2_leaf_reg: float = 10.0
    seed: int = 7
    target_mode: str = "recovered"  # recovered demand or observed daily sales


def model_dir(lag: int) -> Path:
    # ⚠️ Не относительный путь: прод запускает daily.py из корня forecast,
    # и «analysis/output/ml_model» уехал бы мимо prognoz/. Якоримся на OUTPUT
    # ядра (тот же приём, что в ml_core.model_dir); env-переменная — песочница.
    env = os.environ.get("PROGNOZ_ML_MODEL_DIR")
    if env:
        root = Path(env)
    else:
        from prognoz.predict.demand import OUTPUT  # noqa: PLC0415
        root = OUTPUT / "ml_model"
    return root / "catboost" / f"lag_{lag}"


def load_prev_stamp(cfg: CatCfg = CatCfg()) -> dict | None:
    """Stamp of the model replaced at the previous refit, if any."""
    path = model_dir(cfg.lag) / "prev_stamp.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_current(cfg: CatCfg) -> tuple[object, dict] | None:
    """Load a reusable model only when both artefacts are present."""
    root = model_dir(cfg.lag)
    model_path, stamp_path = root / "model.cbm", root / "stamp.json"
    if not model_path.exists() or not stamp_path.exists():
        return None
    from catboost import CatBoostRegressor
    model = CatBoostRegressor()
    model.load_model(model_path)
    return model, json.loads(stamp_path.read_text(encoding="utf-8"))


def ensure_model(shelf_df: pd.DataFrame, cutoff: date, *, cfg: CatCfg = CatCfg(),
                 excluded: set[date] | None = None,
                 force: bool = False) -> tuple[object, dict, bool]:
    """Return a causal model; never reuse a model trained after *cutoff*."""
    current = _load_current(cfg)
    if current is not None and not force:
        model, stamp = current
        trained = date.fromisoformat(stamp.get("train_cutoff", "1900-01-01"))
        compatible = (
            stamp.get("features_version") == FEATURES_VERSION
            and stamp.get("lag") == cfg.lag
            and stamp.get("target_mode") == cfg.target_mode
            # смена квантиля/гиперпараметров обязана вызывать переобучение,
            # иначе конфиг говорит одно, а модель на диске считает другое
            and stamp.get("params") == dict(cfg.__dict__)
            and stamp.get("assortment_policy_sha256") == assortment_provenance()[
                "assortment_policy_sha256"]
            and trained <= cutoff
            and (cutoff - trained).days <= REFIT_DAYS
        )
        if compatible:
            return model, stamp, False

    root = model_dir(cfg.lag)
    root.mkdir(parents=True, exist_ok=True)
    if current is not None:
        _, prev = current
        tmp = root / "prev_stamp.tmp.json"
        tmp.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(root / "prev_stamp.json")
    model, stamp = train(shelf_df, cutoff, cfg=cfg, excluded=excluded, save=True)
    return model, stamp, True


def _safe_shelf(shelf_df: pd.DataFrame) -> pd.DataFrame:
    required = {"point_key", "sku_key", "trade_day", "grp", "sold_units", "lost_units",
                "u40_units", "wo_units", "avg_price", "supply_gap", "wo_settled"}
    missing = sorted(required - set(shelf_df.columns))
    if missing:
        raise RuntimeError(f"Shelf misses columns: {missing}")
    optional = [c for c in ("data_missing",) if c in shelf_df.columns]
    out = filter_frame(
        shelf_df[[*required, *optional]], "metric_scope", date_col="trade_day")
    out["trade_day"] = pd.to_datetime(out["trade_day"]).dt.normalize()
    if out.duplicated(["point_key", "sku_key", "trade_day"]).any():
        raise RuntimeError("Shelf has duplicate point-SKU-day rows")
    for col in ("sold_units", "lost_units", "u40_units", "wo_units", "avg_price"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if out[["sold_units", "lost_units"]].isna().any().any():
        raise RuntimeError("Shelf has an undefined sales or lost-demand target")
    return out.sort_values(["point_key", "sku_key", "trade_day"])


def build_panel(shelf_df: pd.DataFrame, end: date, *, as_of: date | None = None,
                excluded: set[date] | None = None,
                target_mode: str = "recovered") -> pd.DataFrame:
    """Build a dense pair/day panel without future metadata or unsettled labels."""
    raw = _safe_shelf(shelf_df)
    cutoff = pd.Timestamp(as_of or end)
    end_ts = pd.Timestamp(end)
    frames: list[pd.DataFrame] = []
    cols = ["grp", "sold_units", "lost_units", "u40_units", "wo_units", "avg_price",
            "supply_gap", "wo_settled"]
    if "data_missing" in raw.columns:
        cols.append("data_missing")
    for (point_key, sku_key), part in raw.groupby(["point_key", "sku_key"], sort=False):
        part = part[part["trade_day"] <= cutoff].set_index("trade_day").sort_index()
        if part.empty:
            continue
        frame = part[cols].reindex(pd.date_range(part.index.min(), end_ts, freq="D"))
        # ffill is intentional: bfill / last row would disclose a future category.
        frame["grp"] = frame["grp"].ffill().fillna("__MISSING__")
        frame["point_key"], frame["sku_key"] = str(point_key), str(sku_key)
        frames.append(frame)
    if not frames:
        raise RuntimeError("No shelf pairs available at requested as-of date")
    pan = pd.concat(frames).rename_axis("day").reset_index()
    pan = pan.sort_values(["point_key", "sku_key", "day"]).reset_index(drop=True)
    if target_mode not in {"recovered", "observed_sales"}:
        raise ValueError(f"Unknown target mode: {target_mode}")
    pan["demand_raw"] = (pan["sold_units"] + pan["lost_units"]
                         if target_mode == "recovered" else pan["sold_units"])
    supply_bad = pan["supply_gap"].fillna(False).astype(bool)
    # Дни, за которые данные ещё не загружены (shelf.core.flag_data_missing):
    # спрос неизвестен — ни в метку, ни в признак он не идёт (NaN, не ноль).
    if "data_missing" in pan.columns:
        supply_bad |= pan["data_missing"].fillna(False).astype(bool)
    settled = pan["wo_settled"].fillna(False).astype(bool)
    bad = supply_bad | ~settled
    if excluded:
        bad |= pan["day"].dt.date.isin(excluded)
    # Labels wait for the write-off watermark. Features use only what was
    # observable at issue time: recovered demand for settled days, plain sales
    # for the immature tail. This preserves D-lag signal without importing a
    # late lost-demand estimate into the historical forecast.
    pan["dem"] = pan["demand_raw"].mask(bad)
    feature_bad = supply_bad.copy()
    if excluded:
        feature_bad |= pan["day"].dt.date.isin(excluded)
    pan["feature_dem"] = pan["demand_raw"].where(settled, pan["sold_units"])
    pan["feature_dem"] = pan["feature_dem"].mask(feature_bad)
    return pan


def _roll(series: pd.Series, keys: list[pd.Series], op: str, window: int, min_periods: int) -> pd.Series:
    grouped = series.groupby(keys, sort=False)
    fn = {"mean": lambda s: s.rolling(window, min_periods=min_periods).mean(),
          "median": lambda s: s.rolling(window, min_periods=min_periods).median(),
          "std": lambda s: s.rolling(window, min_periods=min_periods).std(),
          "q75": lambda s: s.rolling(window, min_periods=min_periods).quantile(.75),
          "sum": lambda s: s.rolling(window, min_periods=min_periods).sum()}[op]
    return grouped.transform(fn)


def add_features(panel: pd.DataFrame, cfg: CatCfg = CatCfg()) -> pd.DataFrame:
    pan = panel.copy().sort_values(["point_key", "sku_key", "day"]).reset_index(drop=True)
    pair = pan.groupby(["point_key", "sku_key"], sort=False)
    keys = [pan["point_key"], pan["sku_key"]]
    signal = "feature_dem" if "feature_dem" in pan.columns else "dem"
    for value in sorted({cfg.lag, 7, 14, 21, 28}):
        pan[f"lag{value}"] = pair[signal].shift(value)
    shifted = pair[signal].shift(cfg.lag)
    for name, op in (("r28_mean", "mean"), ("r28_med", "median"),
                     ("r28_std", "std"), ("r28_q75", "q75")):
        pan[name] = _roll(shifted, keys, op, 28, 7)
    pan["weekday"] = pan["day"].dt.weekday.astype(int)
    weekday_keys = [pan["point_key"], pan["sku_key"], pan["weekday"]]
    previous_weekday = pan.groupby(
        ["point_key", "sku_key", "weekday"], sort=False)[signal].shift(1)
    for name, op in (("wd_mean", "mean"), ("wd_med", "median"),
                     ("wd_std", "std"), ("wd_q75", "q75")):
        pan[name] = _roll(previous_weekday, weekday_keys, op, 4, 2)
    price = pair["avg_price"].shift(cfg.lag)
    pan["price_r28"] = _roll(price, keys, "mean", 28, 3)
    u40, sold, wo = (pair[c].shift(cfg.lag) for c in ("u40_units", "sold_units", "wo_units"))
    pan["u40_share28"] = _roll(u40, keys, "sum", 28, 7) / np.maximum(_roll(sold, keys, "sum", 28, 7), 1.0)
    pan["wo_rate28"] = _roll(wo, keys, "mean", 28, 7)
    pan["month"] = pan["day"].dt.month.astype(int)
    pan["week"] = pan["day"].dt.isocalendar().week.astype(int)
    return pan.replace([np.inf, -np.inf], np.nan)


def _frame(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame[[*NUMERIC_FEATURES, *CAT_FEATURES]].copy()
    for col in CAT_FEATURES:
        out[col] = out[col].fillna("__MISSING__").astype(str)
    return out


def _cat_model(cfg: CatCfg):
    from catboost import CatBoostRegressor
    return CatBoostRegressor(
        loss_function=f"Quantile:alpha={cfg.quantile}",
        eval_metric=f"Quantile:alpha={cfg.quantile}",
        iterations=cfg.iterations, depth=cfg.depth, learning_rate=cfg.learning_rate,
        l2_leaf_reg=cfg.l2_leaf_reg, random_seed=cfg.seed, random_strength=.5,
        bootstrap_type="Bayesian", bagging_temperature=.5, one_hot_max_size=5000,
        allow_writing_files=False, thread_count=-1, verbose=False,
    )


def train(shelf_df: pd.DataFrame, train_end: date, *, cfg: CatCfg = CatCfg(),
          excluded: set[date] | None = None, save: bool = True):
    panel = add_features(build_panel(shelf_df, train_end, as_of=train_end, excluded=excluded,
                                    target_mode=cfg.target_mode), cfg)
    train_rows = panel[(panel["day"] <= pd.Timestamp(train_end)) & panel["dem"].notna()].copy()
    if len(train_rows) < 500:
        raise RuntimeError(f"Too few settled training rows: {len(train_rows)}")
    model = _cat_model(cfg)
    model.fit(_frame(train_rows), train_rows["dem"], cat_features=CAT_FEATURES)
    stamp = {"engine": "catboost", "features_version": FEATURES_VERSION,
             "lag": cfg.lag, "quantile": cfg.quantile, "train_cutoff": train_end.isoformat(),
             "max_label_date": train_rows["day"].max().date().isoformat(),
             "n_train_rows": int(len(train_rows)), "target_mode": cfg.target_mode,
             "assortment_policy_sha256": assortment_provenance()[
                 "assortment_policy_sha256"],
             "features": [*NUMERIC_FEATURES, *CAT_FEATURES],
             "params": cfg.__dict__}
    if save:
        root = model_dir(cfg.lag); root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(suffix=".cbm", delete=False, dir=root) as tmp:
            tmp_path = Path(tmp.name)
        try:
            model.save_model(tmp_path)
            tmp_path.replace(root / "model.cbm")
        finally:
            tmp_path.unlink(missing_ok=True)
        (root / "stamp.json").write_text(json.dumps(stamp, ensure_ascii=False, indent=2), encoding="utf-8")
    return model, stamp


def predict(model, shelf_df: pd.DataFrame, target: date, *, cfg: CatCfg = CatCfg(),
            pairs: set[tuple[str, str]] | None = None, excluded: set[date] | None = None) -> pd.DataFrame:
    # A target D may only see shelf state through D-lag.  This also makes
    # retrospective evaluation use exactly the information available then.
    pairs = filter_pairs(pairs, "factory_order")
    panel = add_features(build_panel(shelf_df, target, as_of=target - timedelta(days=cfg.lag), excluded=excluded,
                                    target_mode=cfg.target_mode), cfg)
    test = panel[panel["day"] == pd.Timestamp(target)].copy()
    test["mu_ml"] = np.floor(np.clip(model.predict(_frame(test)), 0, None) + .5)
    observed = panel[panel["dem"].notna()].groupby(["point_key", "sku_key"]).size()
    test["n_obs_days"] = [int(observed.get((r.point_key, r.sku_key), 0)) for r in test.itertuples()]
    result = test[["point_key", "sku_key", "grp", "mu_ml", "n_obs_days", "r28_mean", "wd_mean", "lag3", "lag7"]].copy()
    result = result.rename(columns={"lag3": "lag_recent"})
    if pairs:
        existing = set(zip(result["point_key"], result["sku_key"]))
        absent = sorted(pairs - existing)
        if absent:
            result = pd.concat([result, pd.DataFrame(absent, columns=["point_key", "sku_key"]).assign(mu_ml=0., n_obs_days=0)], ignore_index=True)
    return result


def explain_row(r, quantile: float = QUANTILE_DEFAULT) -> str:
    """Short customer-facing explanation built only from causal features."""
    bits = []
    if pd.notna(getattr(r, "r28_mean", np.nan)):
        bits.append(f"средний спрос 28 дн. {r.r28_mean:.1f} шт/д")
    if pd.notna(getattr(r, "wd_mean", np.nan)):
        bits.append(f"такие же дни недели {r.wd_mean:.1f}")
    if pd.notna(getattr(r, "lag_recent", np.nan)):
        bits.append(f"последний виденный день {r.lag_recent:g}")
    if not bits:
        bits.append("истории мало — использованы уровень точки и группы")
    return "CatBoost: " + "; ".join(bits) + f"; запас под пик (q{quantile:.2f})"


def catboost_forecast(con, target: date, *, cfg: CatCfg = CatCfg(),
                      shelf_df: pd.DataFrame | None = None,
                      pairs: set[tuple[str, str]] | None = None,
                      refit: bool = False) -> tuple[pd.DataFrame, dict]:
    """Facade used by ``daily.py``: shelf → CatBoost → novelty floor.

    The shelf and the model are cut off at ``target - lag``.  This is the
    same information boundary used at inference, including retrospective
    checks; it prevents a newly trained model from seeing the target day.
    """
    from prognoz.predict.demand import load_shelf
    from prognoz.predict.ml_core import load_excluded, novelty_floor

    # «Нетипичные дни» (analysis/config/excluded_days.json, форма в вебе) обязаны
    # выпадать из обучения и признаков — как в ml_core, иначе форма молча
    # перестаёт влиять на модель.
    excluded = load_excluded()
    cutoff = target - timedelta(days=cfg.lag)
    if shelf_df is None:
        # load_shelf excludes its ``asof`` day, therefore +1 includes cutoff.
        shelf_df = load_shelf(con, cutoff + timedelta(days=1))
    model, stamp, refitted = ensure_model(shelf_df, cutoff, cfg=cfg,
                                          excluded=excluded, force=refit)
    preds = predict(model, shelf_df, target, cfg=cfg, pairs=pairs, excluded=excluded)

    # Rebuild the same causal panel for the established network-level floor
    # of new SKUs.  No human order or target-day fact is used here.
    panel = add_features(build_panel(shelf_df, target, as_of=cutoff,
                                     excluded=excluded,
                                     target_mode=cfg.target_mode), cfg)
    preds = novelty_floor(preds, panel, target, cfg.lag)
    return preds, {"stamp": stamp, "refitted": refitted, "engine": "catboost"}


def predict_many(model, shelf_df: pd.DataFrame, targets: list[date], *,
                 cfg: CatCfg = CatCfg()) -> pd.DataFrame:
    """Causal forecasts for several dates using one feature-panel build.

    Features are shifted by ``lag`` before every rolling operation.  The panel
    may therefore be built through the latest known-as-of day once: a row for
    an earlier target cannot read a later ``dem`` value.  This makes a nightly
    D+2/D+3/D+4 release fast without weakening the causality guarantee.
    """
    if not targets:
        return pd.DataFrame()
    end = max(targets)
    known_until = end - timedelta(days=cfg.lag)
    panel = add_features(build_panel(shelf_df, end, as_of=known_until,
                                    target_mode=cfg.target_mode), cfg)
    wanted = {pd.Timestamp(day) for day in targets}
    out = panel[panel["day"].isin(wanted)].copy()
    out["mu_ml"] = np.floor(np.clip(model.predict(_frame(out)), 0, None) + .5)
    out["target_date"] = out["day"]
    return out[["point_key", "sku_key", "grp", "mu_ml", "target_date"]]


def monetary_error(prediction: pd.DataFrame, shelf_df: pd.DataFrame, start: date, end: date) -> dict:
    """Evaluate raw model quantities on a later untouched settled interval."""
    actual = _safe_shelf(shelf_df)
    actual = actual[(actual["trade_day"] >= pd.Timestamp(start)) & (actual["trade_day"] <= pd.Timestamp(end))]
    actual = actual[(~actual["supply_gap"].fillna(False)) & actual["wo_settled"].fillna(False)].copy()
    actual["demand"] = actual["sold_units"] + actual["lost_units"]
    merged = actual.merge(prediction, left_on=["point_key", "sku_key", "trade_day"],
                          right_on=["point_key", "sku_key", "target_date"], how="left")
    qty = merged["mu_ml"].fillna(0.0); demand = merged["demand"]
    price = merged["avg_price"].fillna(merged["avg_price"].median())
    miss = (np.maximum(qty - demand, 0) * price * .33 + np.maximum(demand - qty, 0) * price * .67).sum()
    return {"rows": int(len(merged)), "start": start.isoformat(), "end": end.isoformat(),
            "wape": float(np.abs(qty - demand).sum() / max(demand.sum(), 1.0)), "miss_rub": float(miss)}
