"""Read-only weekly order rehearsal on fictional point/SKU history."""
from __future__ import annotations

import hashlib
import math
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parent.parent

FEATURES = ["point", "sku", "weekday", "recent", "same_weekday", "trend"]
HISTORY_COLUMNS = {"date", "point_key", "sku_key", "sku_name", "qty_sold", "qty_demand"}
PLANNING_COLUMNS = {"point_key", "sku_key", "stock_on_hand", "incoming_qty",
                    "safety_stock", "pack_size", "min_order", "shelf_life_days",
                    "lead_time_days"}


def data_dir() -> Path:
    raw = os.getenv("WEEKLY_DEMO_DATA_DIR", "").strip()
    return Path(raw).resolve() if raw else ROOT / "demo" / "weekly"


def _integer_column(frame: pd.DataFrame, column: str, minimum: int = 0) -> None:
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any() or (~values.eq(values.round())).any() or (values < minimum).any():
        raise ValueError(f"Invalid {column} in weekly demo data")
    frame[column] = values.astype(int)


def _load(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    history_path, planning_path = path / "history.csv", path / "planning.csv"
    history = pd.read_csv(history_path, dtype={"point_key": str, "sku_key": str, "sku_name": str})
    planning = pd.read_csv(planning_path, dtype={"point_key": str, "sku_key": str})
    if not HISTORY_COLUMNS <= set(history) or not PLANNING_COLUMNS <= set(planning):
        raise ValueError("Weekly demo CSV columns are missing")
    if history.empty or planning.empty:
        raise ValueError("Weekly demo CSV is empty")
    history["date"] = pd.to_datetime(history["date"], format="%Y-%m-%d", errors="raise")
    for col in ("qty_sold", "qty_demand"):
        _integer_column(history, col)
    for col in PLANNING_COLUMNS - {"point_key", "sku_key"}:
        _integer_column(planning, col, 1 if col in {"pack_size", "shelf_life_days"} else 0)
    if (planning["lead_time_days"] >= 7).any():
        raise ValueError("Lead time must fall within the seven-day forecast")
    if (history["qty_demand"] < history["qty_sold"]).any():
        raise ValueError("Demand below sold quantity")
    if history.duplicated(["date", "point_key", "sku_key"]).any():
        raise ValueError("Duplicate history row")
    if planning.duplicated(["point_key", "sku_key"]).any():
        raise ValueError("Duplicate planning row")
    if history[["point_key", "sku_key", "sku_name"]].isna().any().any():
        raise ValueError("Missing history key/name")
    pairs = set(map(tuple, history[["point_key", "sku_key"]].drop_duplicates().to_numpy()))
    if pairs != set(map(tuple, planning[["point_key", "sku_key"]].to_numpy())):
        raise ValueError("Planning pairs differ from history")
    dates = pd.date_range(history["date"].min(), history["date"].max())
    if len(dates) < 70 or len(history) != len(dates) * len(pairs):
        raise ValueError("Need at least ten complete daily weeks for every pair")
    stamp = hashlib.sha256(history_path.read_bytes() + planning_path.read_bytes()).hexdigest()[:16]
    return history.sort_values("date"), planning, stamp


def _features(history: pd.DataFrame, target: pd.Timestamp,
              pairs: list[tuple[str, str]], point_ids: dict, sku_ids: dict) -> pd.DataFrame:
    rows = []
    for point, sku in pairs:
        past = history[(history["point_key"] == point) & (history["sku_key"] == sku)
                       & (history["date"] < target)].sort_values("date")
        recent = past.tail(28)
        weekday = recent[recent["date"].dt.weekday == target.weekday()]
        rows.append({
            "point_key": point, "sku_key": sku, "date": target,
            "point": point_ids[point], "sku": sku_ids[sku], "weekday": target.weekday(),
            "recent": float(recent["qty_demand"].mean()) if len(recent) else 0.0,
            "same_weekday": float(weekday["qty_demand"].mean()) if len(weekday) else 0.0,
            "trend": (float(recent.tail(7)["qty_demand"].mean()
                            - recent.head(21)["qty_demand"].mean())
                      if len(recent) >= 28 else 0.0),
        })
    return pd.DataFrame(rows)


def _model(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    model = HistGradientBoostingRegressor(
        loss="quantile", quantile=0.70, max_iter=70, max_leaf_nodes=15,
        min_samples_leaf=15, random_state=7)
    model.fit(train[FEATURES], train["actual"])
    return model


def _predictions(history: pd.DataFrame, train_panel: pd.DataFrame, cutoff: pd.Timestamp,
                 pairs: list[tuple[str, str]], point_ids: dict, sku_ids: dict) -> pd.DataFrame:
    train = train_panel[train_panel["date"] < cutoff]
    if train.empty:
        raise ValueError("Too little history for ML")
    model = _model(train)
    future = pd.concat(
        [_features(history, day, pairs, point_ids, sku_ids)
         for day in pd.date_range(cutoff, periods=7)],
        ignore_index=True)
    future["baseline"] = future["same_weekday"].clip(lower=0).round().astype(int)
    future["ml"] = pd.Series(model.predict(future[FEATURES])).clip(lower=0).round().astype(int)
    return future


def build(path: Path | None = None) -> dict:
    history, planning, stamp = _load(path or data_dir())
    pairs = sorted(set(map(tuple, planning[["point_key", "sku_key"]].to_numpy())))
    point_ids = {key: n for n, key in enumerate(sorted(history["point_key"].unique()))}
    sku_ids = {key: n for n, key in enumerate(sorted(history["sku_key"].unique()))}
    end = history["date"].max()
    next_day = end + pd.Timedelta(days=1)
    panel = pd.concat([
        _features(history, day, pairs, point_ids, sku_ids)
        for day in pd.date_range(history["date"].min() + pd.Timedelta(days=28), end)
    ], ignore_index=True)
    panel = panel.merge(
        history[["date", "point_key", "sku_key", "qty_demand"]],
        on=["date", "point_key", "sku_key"], validate="one_to_one")
    panel = panel.rename(columns={"qty_demand": "actual"})
    # Four disjoint complete weeks; each model sees only earlier history.
    backtest = []
    for weeks_back in range(4, 0, -1):
        cutoff = next_day - pd.Timedelta(days=7 * weeks_back)
        predicted = _predictions(history[history["date"] < cutoff], panel, cutoff,
                                 pairs, point_ids, sku_ids)
        actual = history[(history["date"] >= cutoff)
                         & (history["date"] < cutoff + pd.Timedelta(days=7))]
        compared = predicted.merge(actual[["date", "point_key", "sku_key", "qty_demand"]],
                                   on=["date", "point_key", "sku_key"], validate="one_to_one")
        if len(compared) != 7 * len(pairs):
            raise ValueError("Incomplete backtest week")
        backtest.append(compared)
    checked = pd.concat(backtest, ignore_index=True)
    denominator = int(checked["qty_demand"].sum())
    if denominator <= 0:
        raise ValueError("No observed demand for backtest")
    errors = {name: int((checked[name] - checked["qty_demand"]).abs().sum())
              for name in ("baseline", "ml")}
    winner = min(errors, key=errors.get)
    future = _predictions(history, panel, next_day, pairs, point_ids, sku_ids)
    future["selected"] = future[winner]
    names = history[["point_key", "sku_key", "sku_name"]].drop_duplicates(
        ["point_key", "sku_key"])
    future = future.merge(names, on=["point_key", "sku_key"], validate="many_to_one")
    future = future.merge(planning, on=["point_key", "sku_key"], validate="many_to_one")
    rows = []
    for (point, sku), group in future.groupby(["point_key", "sku_key"], sort=True):
        group = group.sort_values("date")
        first = group.iloc[0]
        lead = int(first["lead_time_days"])
        life = int(first["shelf_life_days"])
        window = group.iloc[lead:min(7, lead + life)]
        forecast = int(group["selected"].sum())
        covered = int(window["selected"].sum())
        stock = int(first["stock_on_hand"])
        incoming = int(first["incoming_qty"])
        lead_demand = int(group.iloc[:lead]["selected"].sum())
        available = max(0, stock + incoming - lead_demand)
        safety = int(first["safety_stock"])
        pack = int(first["pack_size"])
        minimum = int(first["min_order"])
        needed = max(0, covered + safety - available)
        draft = max(minimum, math.ceil(needed / pack) * pack) if needed else 0
        if draft:
            draft = math.ceil(draft / pack) * pack
        rows.append({
            "point_key": point, "sku_key": sku, "sku_name": str(first["sku_name"]),
            "forecast_week": forecast, "first_delivery_demand": covered,
            "delivery_date": (next_day + pd.Timedelta(days=lead)).date().isoformat(),
            "covered_days": len(window), "stock_on_hand": stock, "incoming_qty": incoming,
            "lead_demand": lead_demand, "available_delivery": available,
            "lead_gap": max(0, lead_demand - stock - incoming),
            "safety_stock": safety, "pack_size": pack, "min_order": minimum,
            "shelf_life_days": life, "lead_time_days": lead,
            "draft_qty": draft,
        })
    return {
        "scenario_id": stamp, "as_of": end.date().isoformat(),
        "week_start": next_day.date().isoformat(),
        "week_end": (next_day + pd.Timedelta(days=6)).date().isoformat(),
        "backtest_weeks": 4, "backtest_demand": denominator,
        "wape": {name: round(100 * value / denominator, 1) for name, value in errors.items()},
        "chosen_method": winner,
        "method_note": "Учебная ML-модель того же семейства HGB обучена отдельно на вымышленной истории; это не рабочая дневная модель.",
        "rows": rows,
    }


@lru_cache(maxsize=2)
def _cached(path: str, history_mtime: int, planning_mtime: int) -> dict:
    return build(Path(path))


def scenario() -> dict:
    path = data_dir()
    return _cached(str(path), (path / "history.csv").stat().st_mtime_ns,
                   (path / "planning.csv").stat().st_mtime_ns)
