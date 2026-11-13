"""Train a next-day ensemble on a strictly synthetic venue-history snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from threadpoolctl import threadpool_limits

from web.places_history import DEFAULT_DB

FEATURES = ["weekday", "hour", "lag_1d", "lag_2d", "lag_7d", "mean_past_7d"]
MAX_EXTRA_ROWS = 250_000
SEED = 42


def read_source(path: Path):
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        meta = {k: json.loads(v) for k, v in conn.execute("SELECT key,value FROM metadata")}
        if meta.get("synthetic") is not True or meta.get("unit") != "synthetic_index_0_100":
            raise ValueError("Для этой модели разрешена только явно помеченная синтетическая история")
        start, end = date.fromisoformat(meta["start_date"]), date.fromisoformat(meta["end_date"])
        count = (end - start).days + 1
        if count < 22 or count != meta["days"]:
            raise ValueError("Нужна непрерывная история минимум за 22 дня")
        ids = [row[0] for row in conn.execute("SELECT id FROM venues ORDER BY id")]
        if len(ids) != meta["venues"] or not ids:
            raise ValueError("Количество точек не совпадает с метаданными")
        lookup = {ident: index for index, ident in enumerate(ids)}
        matrix = np.full((len(ids), count, 24), np.nan, dtype=np.float32)
        seen = 0
        for ident, day_text, raw in conn.execute("SELECT venue_id,day,hourly FROM daily"):
            day = date.fromisoformat(day_text)
            day_index = (day - start).days
            if ident not in lookup or not 0 <= day_index < count or len(raw) != 24:
                raise ValueError("Некорректная строка истории")
            values = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            if np.any((values > 100) & (values != 255)):
                raise ValueError("Демо-индекс вне допустимого диапазона")
            values[values == 255] = np.nan
            matrix[lookup[ident], day_index] = values
            seen += 1
        if seen != len(ids) * count:
            raise ValueError("История неполна по точкам или датам")
    return meta, start, matrix


def features_for_day(matrix: np.ndarray, start: date, index: int):
    """Features for day D use only samples up to the end of D-1."""
    if index < 7 or index >= matrix.shape[1]:
        raise ValueError("Для признаков нужна неделя прошлого")
    target = matrix[:, index, :]
    previous = matrix[:, index - 7:index, :]
    valid_count = np.isfinite(previous).sum(axis=1)
    prior_mean = np.divide(np.nansum(previous, axis=1), valid_count,
                           out=np.full(target.shape, np.nan, dtype=np.float32),
                           where=valid_count > 0)
    lag1, lag2, lag7 = matrix[:, index - 1, :], matrix[:, index - 2, :], matrix[:, index - 7, :]
    eligible = np.isfinite(target) & np.isfinite(lag7)
    venue_index, hour = np.nonzero(eligible)
    y = target[eligible].astype(np.float32)
    x = np.column_stack((
        np.full(len(y), (start + timedelta(days=index)).weekday(), dtype=np.float32),
        hour.astype(np.float32), lag1[eligible], lag2[eligible], lag7[eligible], prior_mean[eligible],
    )).astype(np.float32)
    np.nan_to_num(x, copy=False, nan=-1.0)
    return x, y, venue_index, int(np.isfinite(target).sum())


def scores(y, prediction):
    prediction = np.clip(prediction, 0, 100)
    error = prediction - y
    return {"mae": round(float(mean_absolute_error(y, prediction)), 4),
            "rmse": round(float(np.sqrt(mean_squared_error(y, prediction))), 4),
            "r2": round(float(r2_score(y, prediction)), 4),
            "within_10_index_points_pct": round(float(np.mean(np.abs(error) <= 10) * 100), 2),
            "bias": round(float(np.mean(error)), 4)}


def deterministic_sample(length: int, limit: int = MAX_EXTRA_ROWS):
    if length <= limit:
        return np.arange(length)
    return np.random.default_rng(SEED).choice(length, size=limit, replace=False)


def models():
    return (HistGradientBoostingRegressor(max_iter=120, learning_rate=0.06, max_leaf_nodes=31,
                                          l2_regularization=0.5, early_stopping=False, random_state=SEED),
            ExtraTreesRegressor(n_estimators=24, max_depth=15, min_samples_leaf=35,
                                max_features=0.9, n_jobs=3, random_state=SEED))


def train(source: Path, output: Path):
    t0 = time.monotonic()
    meta, start, matrix = read_source(source)
    count = matrix.shape[1]
    train_indices, valid_indices, test_indices = list(range(7, count - 14)), list(range(count - 14, count - 7)), list(range(count - 7, count))
    if not train_indices:
        raise ValueError("Недостаточно дней для обучения до проверки")
    print("Подготовка временных признаков без использования будущих данных…", flush=True)
    features, labels, groups, eligible_totals = {}, {}, {}, {}
    for i in train_indices + valid_indices + test_indices:
        features[i], labels[i], groups[i], eligible_totals[i] = features_for_day(matrix, start, i)
        if len(labels[i]) == 0:
            raise ValueError("Нет допустимых наблюдений за один из дней")
    del matrix

    def combine(indices):
        return np.concatenate([features[i] for i in indices]), np.concatenate([labels[i] for i in indices]), np.concatenate([groups[i] for i in indices])

    x_train, y_train, group_train = combine(train_indices)
    x_valid, y_valid, group_valid = combine(valid_indices)
    x_test, y_test, group_test = combine(test_indices)
    validation_tree_sample = deterministic_sample(len(y_train))
    hgb, extra = models()
    print(f"Обучение бустинга: {len(y_train):,} строк; деревья: {len(validation_tree_sample):,} строк…", flush=True)
    with threadpool_limits(limits=3):
        hgb.fit(x_train, y_train)
        extra.fit(x_train[validation_tree_sample], y_train[validation_tree_sample])
        hgb_valid, extra_valid = hgb.predict(x_valid), extra.predict(x_valid)
    delta = hgb_valid - extra_valid
    denominator = float(np.dot(delta, delta))
    weight = float(np.clip(np.dot(y_valid - extra_valid, delta) / denominator, 0.1, 0.9)) if denominator else 0.5
    valid_blend = weight * hgb_valid + (1.0 - weight) * extra_valid
    validation = {"baseline_lag7": scores(y_valid, x_valid[:, 4]), "hgb": scores(y_valid, hgb_valid),
                  "extra_trees": scores(y_valid, extra_valid), "ensemble": scores(y_valid, valid_blend)}
    del hgb, extra, hgb_valid, extra_valid, valid_blend, delta, x_train, y_train, x_valid, y_valid

    x_final, y_final, groups_final = combine(train_indices + valid_indices)
    final_tree_sample = deterministic_sample(len(y_final))
    hgb, extra = models()
    print(f"Финальное обучение на истории до тестового периода: {len(y_final):,} строк…", flush=True)
    with threadpool_limits(limits=3):
        hgb.fit(x_final, y_final)
        extra.fit(x_final[final_tree_sample], y_final[final_tree_sample])
        hgb_test, extra_test = hgb.predict(x_test), extra.predict(x_test)
    prediction = weight * hgb_test + (1.0 - weight) * extra_test
    test = {"baseline_lag7": scores(y_test, x_test[:, 4]), "hgb": scores(y_test, hgb_test),
            "extra_trees": scores(y_test, extra_test), "ensemble": scores(y_test, prediction)}
    test["ensemble"]["mae_improvement_over_lag7_pct"] = round((test["baseline_lag7"]["mae"] - test["ensemble"]["mae"]) / test["baseline_lag7"]["mae"] * 100, 2) if test["baseline_lag7"]["mae"] else None
    daily = []
    offset = 0
    for i in test_indices:
        n = len(labels[i])
        daily.append({"date": (start + timedelta(days=i)).isoformat(), "eligible_rows": n,
                      "all_nonempty_targets": eligible_totals[i],
                      "baseline_mae": scores(y_test[offset:offset+n], x_test[offset:offset+n, 4])["mae"],
                      "ensemble_mae": scores(y_test[offset:offset+n], prediction[offset:offset+n])["mae"]})
        offset += n

    def span(indices):
        return {"start": (start + timedelta(days=indices[0])).isoformat(),
                "end": (start + timedelta(days=indices[-1])).isoformat(), "days": len(indices)}

    metrics = {"task": "next_day_synthetic_index_regression", "synthetic_only": True,
               "unit": "synthetic index points (0–100), not people or seats", "timezone": meta["timezone"],
               "source_algorithm": meta.get("algorithm"), "source_scenario": meta.get("scenario"),
               "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "split": {"history_start": start.isoformat(), "train": span(train_indices),
                         "validation": span(valid_indices), "test": span(test_indices),
                         "method": "chronological; rolling next-day evaluation: prior test days are available as history, never used for model fitting"},
               "counts": {"venues_in_source": meta["venues"], "train_rows_before_refit": len(group_train),
                          "validation_rows": len(group_valid), "refit_rows": len(y_final),
                          "extra_trees_refit_rows": len(final_tree_sample),
                          "venues_in_refit": int(len(np.unique(groups_final))),
                          "venues_in_extra_trees_refit": int(len(np.unique(groups_final[final_tree_sample]))),
                          "test_rows": len(y_test), "venues_in_test": int(len(np.unique(group_test))),
                          "test_nonempty_targets": sum(eligible_totals[i] for i in test_indices)},
               "features": FEATURES, "baseline": "same venue and hour, seven days earlier",
               "ensemble": {"hgb_weight": round(weight, 4), "extra_trees_weight": round(1.0 - weight, 4),
                            "weight_selection": "validation squared error, constrained to 10–90% for each model"},
               "validation_metrics": validation, "test_metrics": test, "test_by_day": daily,
               "sklearn_version": sklearn.__version__, "duration_seconds": round(time.monotonic() - t0, 1),
               "limitations": ["The source is a hand-shaped synthetic presentation scenario, not measured visitors or occupancy.",
                               "The last seven days were not used to fit models or select weights.",
                               "Rolling next-day evaluation can use prior test dates as past observations; this is not a seven-day-ahead forecast.",
                               "The test mostly contains venues already present in training; performance on new venues and actual venues is unverified.",
                               "Closed or missing target hours and rows without a seven-day lag are excluded; coverage is reported."]}
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    model_path = output / "ensemble.joblib"
    joblib.dump({"hgb": hgb, "extra_trees": extra, "hgb_weight": weight,
                 "features": FEATURES, "synthetic_only": True, "metadata": metrics}, model_path, compress=3)
    model_path.chmod(0o600)
    metrics_path = output / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metrics_path.chmod(0o600)
    print(json.dumps({"output": str(output), "test": test, "counts": metrics["counts"],
                      "weights": metrics["ensemble"], "duration_seconds": metrics["duration_seconds"]}, ensure_ascii=False, indent=2), flush=True)
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Папка результата уже существует; данные не перезаписаны")
    train(args.source, args.output)


if __name__ == "__main__":
    main()
