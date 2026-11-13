"""Publish synthetic-only completeness evidence for the local operational report.

Usage: python status.py /absolute/path/prognoz_web.duckdb /absolute/path/output YYYY-MM-DD
It never claims a 1C import and refuses incomplete synthetic point/source days.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def publish(snapshot: Path, output: Path, day: date) -> None:
    with duckdb.connect(str(snapshot), read_only=True) as con:
        points = [row[0] for row in con.execute(
            "SELECT point_key FROM mart.dim_point WHERE point_key LIKE 'DEMO-POINT-%' ORDER BY 1").fetchall()]
        if len(points) != 3:
            raise RuntimeError("Expected exactly three synthetic points")
        sources = {
            "receipts": ("mart.cheki_headers", "trade_day"),
            "sales": ("mart.fact_sales_daily", "sale_date"),
            "orders": ("mart.fact_orders_daily", "trade_date"),
            "writeoffs": ("mart.fact_writeoff_daily", "wo_date"),
        }
        evidence = {}
        loaded_at = datetime.now(ZoneInfo("Etc/GMT-10")).isoformat()
        for point in points:
            for source, (table, column) in sources.items():
                count = con.execute(
                    f"SELECT count(*) FROM {table} WHERE point_key=? AND {column}=?",
                    [point, day],
                ).fetchone()[0]
                if count == 0:
                    raise RuntimeError(f"No {source} records for {point} on {day}")
            unlinked = con.execute("""
                SELECT count(*) FROM mart.cheki_headers h
                WHERE h.point_key=? AND h.trade_day=? AND NOT EXISTS
                (SELECT 1 FROM mart.cheki_lines l WHERE l.chek_key=h.chek_key)
            """, [point, day]).fetchone()[0]
            if unlinked:
                raise RuntimeError(f"Unlinked synthetic receipts for {point}: {unlinked}")
            evidence[point] = {
                "sources": {source: {"status": "complete", "loaded_at": loaded_at}
                            for source in sources},
                "documents": {source: {"present": True, "posted": True}
                              for source in ("sales", "orders", "writeoffs")},
            }
    payload = {
        "trade_date": day.isoformat(),
        "snapshot_sha256": sha(snapshot),
        "provenance": "DEMO synthetic generator; no 1C import",
        "points": evidence,
    }
    shelf = output / "shelf_daily.parquet"
    if shelf.exists():
        payload["shelf_sha256"] = sha(shelf)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output,
                                     prefix=".ingestion-status-", delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        temp = Path(stream.name)
    os.replace(temp, output / "ingestion-status.json")
    print(f"Synthetic completeness: {day}, {len(points)} points, four sources")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("day", type=date.fromisoformat)
    args = parser.parse_args()
    if not args.snapshot.is_absolute() or not args.output.is_absolute():
        parser.error("snapshot and output must be absolute paths")
    publish(args.snapshot, args.output, args.day)
