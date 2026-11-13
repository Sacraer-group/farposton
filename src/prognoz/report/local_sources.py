"""Read one day's local snapshot, immutable forecasts and edit history only."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from prognoz.assortment import filter_frame
from prognoz.report.operational import SOURCES, LOCAL_TZ, now_local, number
from prognoz.scope import sku_filter, sku_twins_cte

ROOT = Path(__file__).resolve().parents[3]


def output_root() -> Path:
    return Path(os.getenv("WEB_OUTPUT_ROOT") or os.getenv("APP_OUTPUT_ROOT") or ROOT / "analysis/output")


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validated_ingest_status(root: Path, day: str, db_path: Path, keys, generated_at):
    """Bridge the deployed acceptance contract, never generalise to later days.

    check_ingest_acceptance validates receipt headers/lines for six points.
    It does not certify order or writeoff completeness, nor document regulations.
    """
    path = root / 'ingest-status.json'
    if not path.exists() or not db_path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
        stamp = datetime.fromisoformat(raw['completed_at'])
        if (raw.get('one_c_ingest_succeeded') is not True
                or raw.get('one_c_ingest') is not True
                or raw.get('one_c_write') is not False
                or raw.get('validated_day') != day or stamp.tzinfo is None
                or stamp > generated_at or raw.get('snapshot_sha256') != file_hash(db_path)):
            return {}
        with duckdb.connect(str(db_path), read_only=True,
                            config={'memory_limit': '500MB', 'threads': 1}) as con:
            rows = con.execute('''SELECT coalesce(nullif(p.merged_into,''), h.point_key),
                count(*), count(DISTINCT h.chek_key),
                count(*) FILTER (WHERE h.posted AND h.op='Продажа'),
                count(*) FILTER (WHERE h.posted AND h.op IN ('Продажа','Возврат')
                  AND NOT EXISTS (SELECT 1 FROM mart.cheki_lines l WHERE l.chek_key=h.chek_key))
                FROM mart.cheki_headers h JOIN mart.dim_point p USING(point_key)
                WHERE h.trade_day=CAST(? AS DATE) GROUP BY 1''', [day]).fetchall()
        evidence = {key: {'sources': {'receipts': {'status': 'complete',
                     'loaded_at': stamp.isoformat()}}}
                    for key, headers, unique, sales, missing in rows
                    if key in keys and headers == unique and sales > 0 and missing == 0}
        return {'trade_date': day, 'snapshot_sha256': raw['snapshot_sha256'], 'points': evidence}
    except (OSError, ValueError, KeyError, TypeError, duckdb.Error):
        return {}


def config_path() -> Path:
    return Path(os.getenv("PROGNOZ_CONFIG_DIR") or ROOT / "analysis/config") / "operational_reports.json"


def read_config() -> dict:
    path = config_path()
    cfg = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not cfg.get("points"):
        path = Path(os.getenv("PROGNOZ_CONFIG_DIR") or ROOT / "analysis/config") / "points_map.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            cfg["points"] = [{"point_key": p["point_key"], "name": p["canon_name"],
                              "activation_pending": p["role"] == "market_future"}
                             for p in csv.DictReader(f) if p["role"] in {"market", "market_hub"}
                             or (p["role"] == "market_future" and p["canon_name"] in cfg.get("pending_point_names", []))]
    managers_path = path.parent / "point_managers.json"
    if managers_path.is_file():
        managers = json.loads(managers_path.read_text(encoding="utf-8-sig")).get("points", {})
        for point in cfg["points"]:
            names = managers.get(point["point_key"], {}).get("managers", [])
            if names and not point.get("owner"):
                point["owner"] = "; ".join(names)
    return cfg


def read_forecast(day: str, root: Path) -> tuple[pd.DataFrame, str | None]:
    """Select latest proven pre-trade issue, never today's recomputation of yesterday."""
    candidates = []
    for path in sorted((root / "releases").glob("*/forecast_log/" + day + ".parquet")):
        manifest = path.parent.parent / "provenance.json"
        if not manifest.exists():
            continue
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        entry = next((x for x in meta.get("files", []) if x.get("target_date") == day), None)
        if not entry or file_hash(path) != entry.get("sha256"):
            continue
        candidates.append(path)
    legacy = root / "forecast_log" / (day + ".parquet")
    if legacy.exists():
        candidates.append(legacy)
    valid = []
    for path in candidates:
        before = path.stat()
        frame = pd.read_parquet(path)
        if "target_date" in frame.columns and not pd.to_datetime(frame["target_date"]).dt.date.eq(date.fromisoformat(day)).all():
            continue
        if {"_issue_date", "issue_date"}.issubset(frame.columns) and not pd.to_datetime(frame["_issue_date"]).eq(pd.to_datetime(frame["issue_date"])).all():
            continue
        if "_issue_date" in frame.columns:
            frame = frame.rename(columns={"_issue_date": "issue_date"}) if "issue_date" not in frame.columns else frame
        if not {"issue_date", "_point_key", "_sku_key", "Прогноз, шт"}.issubset(frame.columns):
            continue
        issues = pd.to_datetime(frame["issue_date"]).dt.date
        if frame.empty or issues.isna().any() or issues.nunique() != 1 or issues.iloc[0] >= date.fromisoformat(day):
            continue
        sha = file_hash(path)
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise RuntimeError("Forecast changed during report read")
        valid.append((issues.iloc[0], path.as_posix(), frame, "sha256:" + sha))
    if not valid:
        return pd.DataFrame(), None
    _, source_path, frame, release_id = max(valid, key=lambda x: (x[0], x[1]))
    frame = frame.copy()
    frame["target_date"] = day
    frame = filter_frame(frame, "factory_order", point_col="_point_key", sku_col="_sku_key", date_col="target_date")
    if frame.duplicated(["_point_key", "_sku_key"]).any():
        raise ValueError("Duplicate forecast pair")
    frame.attrs["provenance"] = "immutable" if Path(source_path).parent.parent.parent.name == "releases" else "legacy_unverified"
    return frame, release_id


def read_edits(day: str, release_id: str | None) -> dict:
    if release_id is None:
        return {}
    # Read the append-only journal directly: importing the web writer is unnecessary.
    path = Path(os.getenv("WEB_EDITS_DB") or ROOT / "analysis/db/web_edits.sqlite")
    if not path.is_file():
        return {}
    cutoff = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time.min, LOCAL_TZ)
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        columns = {r[1] for r in con.execute("PRAGMA table_info(edits)")}
        if "release_id" not in columns:
            raise RuntimeError("Edit history has no release binding")
        history = con.execute("SELECT point_key,sku_key,qty_user,edited_at FROM edits "
                              "WHERE target_date=? AND release_id=? ORDER BY id",
                              (day, release_id)).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError("Edit history unavailable") from exc
    finally:
        con.close()
    result = {}
    for pk, sk, qty, ts in history:
        stamped = datetime.fromisoformat(ts)
        if stamped.tzinfo is not None and stamped < cutoff:
            if qty is None:
                result.pop((pk, sk), None)
            else:
                result[(pk, sk)] = float(qty)
    return result


QUERIES = {
    "receipts": ("cheki_sales_net", "trade_day", """sum(f.qty) AS sales,
        -sum(CASE WHEN f.sign < 0 THEN f.qty ELSE 0 END) AS returns,
        max(CASE WHEN f.sign > 0 THEN f.ts END) AS last_sale_at""", ""),
    "sales": ("fact_sales_daily", "sale_date", """sum(f.qty_sold-f.qty_returned) AS document_sales,
        sum(f.n_docs) AS document_rows, bool_and(f.all_posted) AS all_posted""", ""),
    "orders": ("fact_orders_daily", "trade_date", "sum(f.qty_ordered) AS order_qty", "AND f.channel = 'transfer'"),
    "writeoffs": ("fact_writeoff_daily", "wo_date", """sum(f.qty_writeoff) AS writeoff,
        CASE WHEN count(*) FILTER (WHERE f.qty_writeoff <> 0 AND (f.wo_sum IS NULL OR f.wo_sum = 0)) > 0
             THEN NULL ELSE sum(f.wo_sum) END AS writeoff_rub""", ""),
}


def collect(day: str, *, generated_at: datetime | None = None) -> dict:
    date.fromisoformat(day)
    generated_at = generated_at or now_local()
    cfg = read_config()
    root = output_root()
    db_path = Path(os.getenv("WEB_DB_SNAPSHOT") or ROOT / "analysis/db/prognoz_web.duckdb")
    registry = cfg["points"]
    points = {p["point_key"]: dict(p, sources={}, rows=[], documents={}) for p in registry}
    rows = {key: {} for key in points}
    def record(pk, sk, name=None):
        return rows[pk].setdefault(sk, {"sku_key": sk, "sku": name or sk})
    try:
        forecast, release_id = read_forecast(day, root)
        edits = read_edits(day, release_id)
        for r in forecast.to_dict("records"):
            pk, sk = str(r["_point_key"]), str(r["_sku_key"])
            if pk in points:
                item = record(pk, sk, r.get("Позиция"))
                item.update(forecast=number(r["Прогноз, шт"]), edited=edits.get((pk, sk)))
                points[pk]["forecast_release"] = release_id
                points[pk]["forecast_provenance"] = forecast.attrs.get("provenance", "legacy_unverified")
                points[pk]["forecast_issue_date"] = str(r["issue_date"])[:10]
    except (OSError, ValueError, RuntimeError):
        for p in points.values():
            p["forecast_error"] = "Прогноз или журнал правок не прочитан"

    # A producer publishes this ONLY after import and atomic snapshot publication.
    status_path = root / "ingestion-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    if not status:
        status = validated_ingest_status(root, day, db_path, points, generated_at)
    snapshot_stat = db_path.stat() if db_path.exists() else None
    if status.get("trade_date") == day and db_path.is_file():
        bound = status.get("snapshot_sha256") == file_hash(db_path)
    else:
        bound = False
    table_ok = set()
    try:
        con = duckdb.connect(str(db_path), read_only=True, config={"memory_limit": "500MB", "threads": 1})
    except (duckdb.Error, OSError):
        con = None
    if con is not None:
        try:
            for source, (table, day_column, expressions, condition) in QUERIES.items():
                try:
                    # Canonicalisation also covers pending points for diagnostics; no forecast activation here.
                    frame = con.execute(f"""WITH {sku_twins_cte()}
                        SELECT coalesce(nullif(p.merged_into,''), f.point_key) AS point_key,
                            coalesce(tw.live,f.sku_key) AS sku_key, min(s.name) AS sku,
                            {expressions}
                        FROM mart.{table} f
                        JOIN mart.dim_point p ON p.point_key=f.point_key
                        LEFT JOIN tw ON tw.ghost=f.sku_key
                        JOIN mart.dim_sku s ON s.sku_key=coalesce(tw.live,f.sku_key)
                        WHERE f.{day_column}=? AND {sku_filter()} {condition}
                        GROUP BY 1,2""", [day]).df()
                    frame["trade_day"] = day
                    frame = filter_frame(frame, "metric_scope", date_col="trade_day")
                    for r in frame.to_dict("records"):
                        pk, sk = r.pop("point_key"), r.pop("sku_key")
                        if pk not in points:
                            continue
                        item = record(pk, sk, r.pop("sku"))
                        for key, value in r.items():
                            if key == "trade_day":
                                continue
                            if key == "order_qty":
                                key = "order"
                            item[key] = None if pd.isna(value) else (
                                str(value) if key == "last_sale_at" else bool(value) if key == "all_posted" else number(value))
                    table_ok.add(source)
                    freshness = con.execute(f"""SELECT
                        coalesce(nullif(p.merged_into,''), f.point_key),
                        max(f.{day_column}),
                        count(*) FILTER (WHERE f.{day_column}=?)
                        FROM mart.{table} f JOIN mart.dim_point p USING(point_key)
                        WHERE f.{day_column}<=? {condition}
                        GROUP BY 1""", [day, day]).fetchall()
                    for pk, latest, count in freshness:
                        if pk in points:
                            points[pk].setdefault("source_freshness", {})[source] = {
                                "latest_date": str(latest) if latest else None,
                                "rows_on_report_date": count,
                            }
                except duckdb.Error:
                    pass  # An absent table is an explicit per-source error, never zero.
        finally:
            con.close()
    if snapshot_stat is not None:
        current_stat = db_path.stat()
        if (current_stat.st_size, current_stat.st_mtime_ns) != (snapshot_stat.st_size, snapshot_stat.st_mtime_ns):
            raise RuntimeError("Snapshot changed while assembling report; retry from a coherent snapshot")
    for key, p in points.items():
        evidence = status.get("points", {}).get(key, {}) if bound else {}
        confirmation = evidence.get("confirmation", {})
        if confirmation.get("release_id") == p.get("forecast_release") and p.get("forecast_release"):
            stamped = confirmation.get("confirmed_at")
            cutoff = datetime.combine(date.fromisoformat(day) + timedelta(days=1), time.min, LOCAL_TZ)
            valid_time = bool(stamped and datetime.fromisoformat(stamped).tzinfo is not None
                              and datetime.fromisoformat(stamped) < cutoff)
            p["confirmation"] = {"unchanged": confirmation.get("unchanged") is True and valid_time,
                                 "confirmed_at": stamped}
        else:
            p.pop("confirmation", None)
        for source in SOURCES:
            s = evidence.get("sources", {}).get(source, {})
            p["sources"][source] = {
                "status": s.get("status", "unknown") if source in table_ok else "error",
                "trade_date": day, "loaded_at": s.get("loaded_at"),
                "snapshot_id": status.get("snapshot_sha256") if bound else None}
        for item in rows[key].values():
            for source, fields in (("receipts", ("sales", "returns")),
                                   ("writeoffs", ("writeoff", "writeoff_rub"))):
                if p["sources"][source]["status"] == "complete":
                    for field in fields:
                        item.setdefault(field, 0.0)
        docs = evidence.get("documents", {})
        for source in ("sales", "orders", "writeoffs"):
            d = dict(docs.get(source, {}))
            d["required"] = p.get("required_documents", {}).get(source)
            if source == "sales" and "present" not in d:
                seen = [r for r in rows[key].values() if r.get("document_rows", 0) > 0]
                d.update(present=bool(seen) if source in table_ok else None,
                         posted=all(r.get("all_posted") for r in seen) if seen else None)
            if source == "orders" and "present" not in d:
                d["present"] = any(r.get("order") is not None for r in rows[key].values()) if source in table_ok else None
            if source == "writeoffs" and "present" not in d:
                observed = p.get("source_freshness", {}).get(source, {}).get("rows_on_report_date", 0)
                d["present"] = observed > 0 if source in table_ok else None
            p["documents"][source] = d
        p["rows"] = sorted(rows[key].values(), key=lambda r: r["sku_key"])
        comparable = [r for r in p["rows"] if r.get("sales") is not None and r.get("document_rows", 0) > 0]
        difference = sum(abs(r["sales"]-r["document_sales"]) for r in comparable)
        p["reconciliation"] = (f"Сверка чеков и документов: {len(comparable)} позиций; сумма абсолютных расхождений {difference:g} шт."
                                if comparable else "Сверка чеков и документов: сопоставимых строк нет.")
    shelf = root / "shelf_daily.parquet"
    for p in points.values():
        p["early_stop_status"] = "нет подтверждённой привязки полки к снимку"
    if shelf.exists() and bound and status.get("shelf_sha256") == file_hash(shelf):
        before = shelf.stat()
        frame = pd.read_parquet(shelf)
        after = shelf.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Shelf changed during report read")
        for p in points.values():
            p["early_stop_status"] = "оценка по полке, связанной с текущим снимком"
        if {"point_key", "sku_key", "trade_day", "wo_settled", "outcome"}.issubset(frame.columns):
            for r in frame.loc[pd.to_datetime(frame["trade_day"]).dt.date == date.fromisoformat(day)].to_dict("records"):
                item = rows.get(r["point_key"], {}).get(r["sku_key"])
                if item is not None:
                    item["early_stop_estimate"] = bool(r["wo_settled"]) and r["outcome"] == "кончилось рано"
    return {"trade_date": day, "generated_at": generated_at.astimezone(LOCAL_TZ).isoformat(), "points": list(points.values())}
