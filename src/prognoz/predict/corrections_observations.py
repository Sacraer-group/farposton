"""Причинно безопасная исследовательская выборка ручных правок.

Правка менеджера не является фактическим спросом. Модуль не подключён к
обучению или ночному выпуску и лишь публикует append-only наблюдения для
будущего walk-forward кандидата. Строка пригодна только когда одновременно:

* правка привязана к неизменяемому parquet-выпуску по SHA-256;
* выпуск содержит тот же отпечаток ассортиментной политики;
* дедлайн конкретного выпуска и цели задан timezone-aware политикой;
* событие правки содержит смещение UTC и произошло до дедлайна;
* факт имеет timezone-aware время первого созревания, зрел и не относится к
  срыву поставки.

Даже пригодная строка в walk-forward доступна лишь после ``matured_at``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from prognoz.assortment import filter_frame, provenance as assortment_provenance


SAFE_EDIT_COLUMNS = (
    "id", "target_date", "release_id", "point_key", "sku_key",
    "qty_user", "edited_at",
)
EXPECTED_POINT_COUNT = 6      # 18.09: + Точка 6 (demo-three-points-v1)
CUTOFF_COLUMNS = (
    "release_id", "target_date", "decision_cutoff_at", "cutoff_policy_version",
)
OUTPUT_COLUMNS = (
    "release_id", "issue_id", "forecast_sha256", "release_manifest_sha256",
    "issue_date",
    "target_date", "horizon_days", "decision_cutoff_at",
    "cutoff_policy_version", "point_key", "sku_key", "edit_id", "edited_at",
    "edit_timestamp_basis", "base_qty", "human_qty", "delta_qty",
    "had_correction", "engine", "version", "sold_units", "lost_units",
    "demand", "avg_price", "matured_at", "outcome_snapshot_sha256",
    "assortment_policy_version", "assortment_policy_sha256", "eligible",
    "rejection_reason",
)


@dataclass(frozen=True)
class ReleaseFile:
    release_id: str
    issue_id: str
    target_date: str
    path: Path
    sha256: str
    manifest_path: Path
    manifest_sha256: str
    point_keys: tuple[str, ...]
    assortment_policy_version: str
    assortment_policy_sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def build_release_index(release_root: Path) -> dict[str, ReleaseFile]:
    """Проиндексировать только полные файлы с совпавшей контрольной суммой."""
    root = Path(release_root)
    if not root.is_dir():
        return {}
    result: dict[str, ReleaseFile] = {}
    for release_dir in sorted(root.iterdir()):
        if not release_dir.is_dir() or release_dir.name.startswith("."):
            continue
        manifest_path = release_dir / "provenance.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_sha256 = _sha256(manifest_path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if manifest.get("issue_id") != release_dir.name:
            continue
        provenance = manifest.get("provenance") or {}
        assortment = provenance.get("assortment") or {}
        point_keys = tuple(sorted(str(value) for value in
                                  (provenance.get("point_keys") or [])))
        for item in manifest.get("files") or []:
            try:
                target_date = str(item["target_date"])
                expected_hash = str(item["sha256"]).lower()
                path = (release_dir / str(item["path"])).resolve()
                expected_bytes = int(item["bytes"])
            except (KeyError, TypeError, ValueError):
                continue
            if (not _inside(path, release_dir) or not path.is_file()
                    or path.stat().st_size != expected_bytes
                    or _sha256(path) != expected_hash):
                continue
            release_id = f"sha256:{expected_hash}"
            record = ReleaseFile(
                release_id=release_id,
                issue_id=release_dir.name,
                target_date=target_date,
                path=path,
                sha256=expected_hash,
                manifest_path=manifest_path.resolve(),
                manifest_sha256=manifest_sha256,
                point_keys=point_keys,
                assortment_policy_version=str(
                    assortment.get("assortment_policy_version") or ""),
                assortment_policy_sha256=str(
                    assortment.get("assortment_policy_sha256") or ""),
            )
            previous = result.get(release_id)
            if previous is not None and previous.path != record.path:
                raise ValueError(
                    f"Один SHA-256 найден в двух выпусках: {release_id}")
            result[release_id] = record
    return result


def read_edits_read_only(path: Path) -> pd.DataFrame:
    """Прочитать SQLite без создания схемы, WAL и служебных записей."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    with sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10,
    ) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(edits)").fetchall()
        }
        missing = set(SAFE_EDIT_COLUMNS) - columns
        if missing:
            raise ValueError(
                "Журнал правок не содержит: " + ", ".join(sorted(missing)))
        query = "SELECT " + ", ".join(SAFE_EDIT_COLUMNS) + " FROM edits ORDER BY id"
        return pd.read_sql_query(query, connection)


def read_decision_cutoffs(path: Path) -> pd.DataFrame:
    """Прочитать версионный JSON дедлайнов issue×target."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("cutoffs") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Файл дедлайнов должен содержать массив cutoffs")
    frame = pd.DataFrame(rows)
    missing = set(CUTOFF_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(
            "Файл дедлайнов не содержит: " + ", ".join(sorted(missing)))
    return frame.loc[:, CUTOFF_COLUMNS].copy()


def read_expected_points(path: Path, *, expected_count: int = EXPECTED_POINT_COUNT) -> dict:
    """Прочитать утверждённый версионный набор точек и зафиксировать его хэш."""
    config_path = Path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Конфигурация точек должна быть JSON-объектом")
    version = str(raw.get("policy_version") or "").strip()
    values = raw.get("point_keys")
    if not version or not isinstance(values, list):
        raise ValueError("Конфигурации точек нужны policy_version и point_keys")
    point_keys = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    if len(point_keys) != len(values):
        raise ValueError("Ключи точек пусты или продублированы")
    if len(point_keys) != expected_count:
        raise ValueError(
            f"Ожидалось точек: {expected_count}; в конфигурации: {len(point_keys)}")
    return {
        "policy_version": version,
        "point_keys": point_keys,
        "sha256": _sha256(config_path),
    }


def _one(frame: pd.DataFrame, column: str):
    if column not in frame:
        raise ValueError(f"Выпуск не содержит {column}")
    values = frame[column].dropna().unique().tolist()
    if len(values) != 1:
        raise ValueError(f"В выпуске {column} должен иметь одно значение")
    return values[0]


def _aware_utc(value) -> datetime | None:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp) or stamp.tzinfo is None:
        return None
    return stamp.tz_convert("UTC").to_pydatetime()


def _true_flag(value) -> bool | None:
    if pd.isna(value):
        return None
    # Pandas обычно возвращает numpy.bool_, который не обязан быть экземпляром
    # встроенного bool. Нормализация через строку сохраняет строгий whitelist и
    # не превращает произвольное ненулевое значение в True.
    folded = str(value).strip().casefold()
    if folded in {"true", "1", "yes"}:
        return True
    if folded in {"false", "0", "no"}:
        return False
    return None


def _reason(*parts: str | None) -> str:
    return ";".join(dict.fromkeys(part for part in parts if part))


def _blank_event(event: pd.Series, reason: str) -> dict:
    return {
        "release_id": str(event.get("release_id") or ""),
        "target_date": str(event.get("target_date") or "")[:10],
        "point_key": str(event.get("point_key") or ""),
        "sku_key": str(event.get("sku_key") or ""),
        "edit_id": int(event.get("id") or 0),
        "edited_at": str(event.get("edited_at") or ""),
        "edit_timestamp_basis": "unknown_or_legacy_naive",
        "had_correction": pd.notna(event.get("qty_user")),
        "eligible": False,
        "rejection_reason": reason,
    }


def _normalise_outcomes(outcomes: pd.DataFrame) -> pd.DataFrame:
    required = {
        "point_key", "sku_key", "trade_day", "sold_units", "lost_units",
        "supply_gap", "wo_settled",
    }
    missing = required - set(outcomes.columns)
    if missing:
        raise ValueError(
            "Витрина результата не содержит: " + ", ".join(sorted(missing)))
    scoped = filter_frame(
        outcomes.copy(), "metric_scope", point_col="point_key",
        sku_col="sku_key", date_col="trade_day",
    )
    scoped["target_date"] = pd.to_datetime(
        scoped["trade_day"], errors="coerce").dt.strftime("%Y-%m-%d")
    if "matured_at" not in scoped:
        scoped["matured_at"] = None
    return scoped


def _cutoff_lookup(decision_cutoffs: pd.DataFrame) -> dict[tuple[str, str], dict]:
    missing = set(CUTOFF_COLUMNS) - set(decision_cutoffs.columns)
    if missing:
        raise ValueError("Дедлайны не содержат: " + ", ".join(sorted(missing)))
    result: dict[tuple[str, str], dict] = {}
    for _, row in decision_cutoffs.iterrows():
        key = (str(row["release_id"]), str(row["target_date"])[:10])
        if key in result:
            raise ValueError(f"Дедлайн продублирован: {key}")
        aware = _aware_utc(row["decision_cutoff_at"])
        if aware is None:
            raise ValueError(f"Дедлайн должен содержать смещение UTC: {key}")
        version = str(row["cutoff_policy_version"] or "").strip()
        if not version:
            raise ValueError(f"У дедлайна нет версии политики: {key}")
        result[key] = {
            "decision_cutoff_at": aware,
            "cutoff_policy_version": version,
        }
    return result


def build_observations(
    edits: pd.DataFrame,
    release_index: dict[str, ReleaseFile],
    outcomes: pd.DataFrame,
    decision_cutoffs: pd.DataFrame,
    *,
    outcome_snapshot_sha256: str,
) -> pd.DataFrame:
    """Собрать последнее состояние до явного дедлайна, не делая допущений о зоне."""
    missing = set(SAFE_EDIT_COLUMNS) - set(edits.columns)
    if missing:
        raise ValueError("События не содержат: " + ", ".join(sorted(missing)))
    if not (len(outcome_snapshot_sha256) == 64
            and all(ch in "0123456789abcdef" for ch in outcome_snapshot_sha256.lower())):
        raise ValueError("Нужен SHA-256 входной витрины результата")
    outcome_snapshot_sha256 = outcome_snapshot_sha256.lower()
    cutoffs = _cutoff_lookup(decision_cutoffs)
    facts = _normalise_outcomes(outcomes)
    fact_groups = facts.groupby(
        ["target_date", "point_key", "sku_key"], sort=False, dropna=False)
    events = edits.loc[:, SAFE_EDIT_COLUMNS].copy()
    if events.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    policy = assortment_provenance()
    current_policy_version = str(policy["assortment_policy_version"])
    current_policy_sha256 = str(policy["assortment_policy_sha256"])
    rows: list[dict] = []
    forecast_cache: dict[str, tuple[pd.DataFrame, pd.DataFrame, dict]] = {}

    for release_value, release_events in events.groupby(
            "release_id", dropna=False, sort=False):
        release_id = str(release_value) if pd.notna(release_value) else ""
        record = release_index.get(release_id)
        if record is None:
            latest = release_events.sort_values("id").groupby(
                ["target_date", "point_key", "sku_key"], sort=False,
                as_index=False).tail(1)
            rows.extend(_blank_event(row, "release_unresolved")
                        for _, row in latest.iterrows())
            continue

        if (not record.manifest_path.is_file()
                or _sha256(record.manifest_path) != record.manifest_sha256):
            raise ValueError(
                f"Provenance выпуска изменился после индексации: {record.issue_id}")

        if release_id not in forecast_cache:
            raw_forecast = pd.read_parquet(record.path)
            required = {"_point_key", "_sku_key", "Прогноз, шт", "_issue_date",
                        "_horizon_days", "target_date"}
            absent = required - set(raw_forecast.columns)
            if absent:
                raise ValueError(
                    f"Выпуск {record.issue_id} не содержит: "
                    + ", ".join(sorted(absent)))
            issue_date = pd.Timestamp(_one(raw_forecast, "_issue_date")).date()
            target_date = pd.Timestamp(_one(raw_forecast, "target_date")).date()
            if target_date.isoformat() != record.target_date:
                raise ValueError("Дата внутри выпуска не совпадает с provenance")
            scoped_forecast = filter_frame(
                raw_forecast, "metric_scope", point_col="_point_key",
                sku_col="_sku_key", date_col="target_date",
            )
            meta = {
                "issue_date": issue_date,
                "target_date": target_date,
                "horizon_days": int(_one(raw_forecast, "_horizon_days")),
                "engine": (str(_one(raw_forecast, "engine"))
                           if "engine" in raw_forecast else ""),
                "version": (str(_one(raw_forecast, "version"))
                            if "version" in raw_forecast else ""),
            }
            forecast_cache[release_id] = raw_forecast, scoped_forecast, meta
        raw_forecast, forecast, meta = forecast_cache[release_id]
        cutoff_meta = cutoffs.get((release_id, meta["target_date"].isoformat()))

        for (_target, _point, _sku), history in release_events.groupby(
                ["target_date", "point_key", "sku_key"], sort=False,
                dropna=False):
            history = history.sort_values("id")
            parsed = history["edited_at"].map(_aware_utc)
            if parsed.isna().any():
                rows.append(_blank_event(
                    history.iloc[-1], "edit_timestamp_timezone_unknown"))
                continue
            if cutoff_meta is None:
                rows.append(_blank_event(
                    history.iloc[-1], "decision_cutoff_missing"))
                continue
            cutoff_at = cutoff_meta["decision_cutoff_at"]
            stamped = history.assign(_edited_utc=parsed)
            before = stamped[stamped["_edited_utc"].le(cutoff_at)]
            if before.empty:
                rows.append(_blank_event(
                    history.iloc[-1], "after_decision_cutoff"))
                continue
            event = before.sort_values(["_edited_utc", "id"]).iloc[-1]
            target = str(event["target_date"])[:10]
            point_key, sku_key = str(event["point_key"]), str(event["sku_key"])
            rejection = ""
            if target != meta["target_date"].isoformat():
                rejection = _reason(rejection, "target_release_mismatch")
            if not record.assortment_policy_sha256:
                rejection = _reason(rejection, "assortment_policy_unbound")
            elif (record.assortment_policy_sha256 != current_policy_sha256
                  or record.assortment_policy_version != current_policy_version):
                rejection = _reason(rejection, "assortment_policy_mismatch")

            raw_match = raw_forecast[
                raw_forecast["_point_key"].astype(str).eq(point_key)
                & raw_forecast["_sku_key"].astype(str).eq(sku_key)]
            match = forecast[
                forecast["_point_key"].astype(str).eq(point_key)
                & forecast["_sku_key"].astype(str).eq(sku_key)]
            if raw_match.shape[0] == 1 and match.empty:
                rejection = _reason(rejection, "outside_metric_scope")
            if len(match) != 1:
                rejection = _reason(
                    rejection,
                    "forecast_row_missing" if match.empty else "forecast_row_duplicated")
                base_qty = None
            else:
                base_qty = pd.to_numeric(
                    match["Прогноз, шт"], errors="coerce").iloc[0]
                if pd.isna(base_qty) or float(base_qty) < 0:
                    rejection = _reason(rejection, "forecast_qty_invalid")
                    base_qty = None
                else:
                    base_qty = float(base_qty)

            raw_human = event["qty_user"]
            had_correction = pd.notna(raw_human)
            if not had_correction:
                rejection = _reason(rejection, "correction_removed")
                human_qty = base_qty
            else:
                human_qty = pd.to_numeric(
                    pd.Series([raw_human]), errors="coerce").iloc[0]
                if pd.isna(human_qty) or float(human_qty) < 0:
                    rejection = _reason(rejection, "human_qty_invalid")
                    human_qty = None
                else:
                    human_qty = float(human_qty)

            try:
                fact = fact_groups.get_group((target, point_key, sku_key))
            except KeyError:
                fact = pd.DataFrame()
            sold = lost = demand = avg_price = matured_at = None
            if len(fact) != 1:
                rejection = _reason(
                    rejection,
                    "outcome_missing" if fact.empty else "outcome_duplicated")
            else:
                settled = _true_flag(fact["wo_settled"].iloc[0])
                gap = _true_flag(fact["supply_gap"].iloc[0])
                if settled is not True:
                    rejection = _reason(rejection, "outcome_not_mature")
                if gap is None:
                    rejection = _reason(rejection, "outcome_quality_unknown")
                elif gap:
                    rejection = _reason(rejection, "supply_gap")
                matured_value = fact["matured_at"].iloc[0]
                matured_dt = _aware_utc(matured_value)
                if matured_dt is None:
                    rejection = _reason(
                        rejection, "outcome_maturity_time_unknown")
                else:
                    # Факт не мог быть известен в момент решения. Сравнение
                    # выполняется между двумя timezone-aware моментами, без
                    # искусственного предположения о локальной полуночи цели.
                    if matured_dt <= cutoff_at:
                        rejection = _reason(
                            rejection, "outcome_maturity_before_or_at_decision")
                    matured_at = matured_dt.isoformat(timespec="seconds")
                sold = pd.to_numeric(fact["sold_units"], errors="coerce").iloc[0]
                lost = pd.to_numeric(fact["lost_units"], errors="coerce").iloc[0]
                avg_price = (pd.to_numeric(
                    fact["avg_price"], errors="coerce").iloc[0]
                    if "avg_price" in fact else None)
                if pd.isna(sold) or pd.isna(lost) or float(sold) < 0 or float(lost) < 0:
                    rejection = _reason(rejection, "outcome_qty_invalid")
                    sold = lost = None
                else:
                    sold, lost = float(sold), float(lost)
                    demand = sold + lost
                if avg_price is not None and pd.isna(avg_price):
                    avg_price = None
                elif avg_price is not None:
                    avg_price = float(avg_price)

            event_time = _aware_utc(event["edited_at"])
            rows.append({
                "release_id": release_id,
                "issue_id": record.issue_id,
                "forecast_sha256": record.sha256,
                "release_manifest_sha256": record.manifest_sha256,
                "issue_date": meta["issue_date"].isoformat(),
                "target_date": target,
                "horizon_days": int(meta["horizon_days"]),
                "decision_cutoff_at": cutoff_at.isoformat(timespec="seconds"),
                "cutoff_policy_version": cutoff_meta["cutoff_policy_version"],
                "point_key": point_key,
                "sku_key": sku_key,
                "edit_id": int(event["id"]),
                "edited_at": event_time.isoformat(timespec="seconds"),
                "edit_timestamp_basis": "offset_aware",
                "base_qty": base_qty,
                "human_qty": human_qty,
                "delta_qty": (human_qty - base_qty
                              if human_qty is not None and base_qty is not None else None),
                "had_correction": had_correction,
                "engine": meta["engine"],
                "version": meta["version"],
                "sold_units": sold,
                "lost_units": lost,
                "demand": demand,
                "avg_price": avg_price,
                "matured_at": matured_at,
                "outcome_snapshot_sha256": outcome_snapshot_sha256,
                "assortment_policy_version": current_policy_version,
                "assortment_policy_sha256": current_policy_sha256,
                "eligible": not rejection,
                "rejection_reason": rejection,
            })

    output = pd.DataFrame(rows)
    for column in OUTPUT_COLUMNS:
        if column not in output:
            output[column] = None
    return output.loc[:, OUTPUT_COLUMNS].sort_values(
        ["target_date", "point_key", "sku_key", "edit_id"],
        kind="mergesort", na_position="last",
    ).reset_index(drop=True)


def readiness_report(
    observations: pd.DataFrame,
    *,
    expected_point_keys: set[str],
    expected_point_count: int = EXPECTED_POINT_COUNT,
    min_active_weeks: int = 12,
    min_active_weeks_per_point: int | None = None,
    min_rows: int = 500,
    min_rows_per_point: int = 50,
) -> dict:
    """Проверить объём, активные недели и точный состав рынков."""
    eligible = observations[
        observations.get("eligible", pd.Series(False, index=observations.index))
        .fillna(False).astype(bool)
    ].copy()
    dates = pd.to_datetime(eligible.get("target_date"), errors="coerce")
    iso = dates.dt.isocalendar() if not dates.empty else pd.DataFrame()
    active_weeks = (len(set(zip(iso["year"], iso["week"])))
                    if not iso.empty else 0)
    per_point = ({str(key): int(value) for key, value in
                  eligible.groupby("point_key").size().sort_index().items()}
                 if not eligible.empty else {})
    weeks_per_point = {}
    for point_key, group in eligible.groupby("point_key"):
        point_dates = pd.to_datetime(group["target_date"], errors="coerce")
        point_iso = point_dates.dt.isocalendar()
        weeks_per_point[str(point_key)] = len(
            set(zip(point_iso["year"], point_iso["week"])))
    expected_points = {str(value) for value in expected_point_keys}
    if min_active_weeks_per_point is None:
        min_active_weeks_per_point = min_active_weeks
    exact_points = set(per_point) == expected_points
    checks = {
        "expected_point_config_cardinality": len(expected_points) == expected_point_count,
        "minimum_rows": len(eligible) >= min_rows,
        "minimum_distinct_active_weeks": active_weeks >= min_active_weeks,
        "exact_expected_points": exact_points,
        "minimum_distinct_active_weeks_per_point": (
            exact_points and all(
                weeks_per_point.get(point, 0) >= min_active_weeks_per_point
                for point in expected_points)),
        "minimum_rows_per_point": (
            exact_points and all(value >= min_rows_per_point
                                 for value in per_point.values())),
    }
    return {
        "status": ("ready_for_walk_forward_challenger" if all(checks.values())
                   else "collect_more_observations"),
        "must_filter_matured_at_at_every_fold": True,
        "must_not_change_production_target_automatically": True,
        "eligible_rows": int(len(eligible)),
        "total_rows": int(len(observations)),
        "distinct_active_weeks": active_weeks,
        "rows_per_point": per_point,
        "active_weeks_per_point": weeks_per_point,
        "expected_point_keys": sorted(expected_points),
        "thresholds": {
            "expected_point_count": expected_point_count,
            "min_active_weeks": min_active_weeks,
            "min_active_weeks_per_point": min_active_weeks_per_point,
            "min_rows": min_rows,
            "min_rows_per_point": min_rows_per_point,
        },
        "checks": checks,
        "rejections": ({str(key): int(value) for key, value in
                        observations.loc[~observations["eligible"].fillna(False),
                                         "rejection_reason"].value_counts().items()}
                       if not observations.empty else {}),
    }


def publish_run(output_parent: Path, observations: pd.DataFrame,
                report: dict, parameters: dict) -> Path:
    """Атомарно опубликовать каталог data+report+manifest с SHA-256."""
    parent = Path(output_parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = parent / f"corrections-{stamp}"
    temporary = parent / f".{final.name}.tmp"
    if final.exists() or temporary.exists():
        raise FileExistsError("Каталог этого запуска уже существует")
    temporary.mkdir()
    try:
        data_path = temporary / "observations.parquet"
        report_path = temporary / "readiness.json"
        observations.to_parquet(data_path, index=False)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "parameters": parameters,
            "rows": int(len(observations)),
            "files": [
                {"name": data_path.name, "bytes": data_path.stat().st_size,
                 "sha256": _sha256(data_path)},
                {"name": report_path.name, "bytes": report_path.stat().st_size,
                 "sha256": _sha256(report_path)},
            ],
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edits-db", required=True, type=Path)
    parser.add_argument("--release-root", required=True, type=Path)
    parser.add_argument("--shelf", required=True, type=Path)
    parser.add_argument("--decision-cutoffs", required=True, type=Path)
    parser.add_argument("--expected-points-config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    releases = build_release_index(args.release_root)
    shelf_hash = _sha256(args.shelf)
    observations = build_observations(
        read_edits_read_only(args.edits_db), releases,
        pd.read_parquet(args.shelf), read_decision_cutoffs(args.decision_cutoffs),
        outcome_snapshot_sha256=shelf_hash,
    )
    point_policy = read_expected_points(args.expected_points_config)
    report = readiness_report(
        observations, expected_point_keys=set(point_policy["point_keys"]))
    report["expected_points_policy_version"] = point_policy["policy_version"]
    report["expected_points_config_sha256"] = point_policy["sha256"]
    published = publish_run(args.output_dir, observations, report, {
        "edits_db": str(args.edits_db),
        "release_root": str(args.release_root),
        "shelf_sha256": shelf_hash,
        "decision_cutoffs_sha256": _sha256(args.decision_cutoffs),
        "expected_points_policy_version": point_policy["policy_version"],
        "expected_points_config_sha256": point_policy["sha256"],
    })
    print(json.dumps({"published": str(published), "report": report},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
