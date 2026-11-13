import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from flats import repo_offers


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_import(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    run_id = conn.execute(
        "INSERT INTO import_runs (started_at, status) VALUES (?, 'running')", (_now(),)
    ).lastrowid
    conn.commit()
    try:
        loaded = seen = 0
        with conn:
            for row in rows:
                seen += 1
                if not isinstance(row, dict) or not row.get("source") or not row.get("source_id"):
                    continue
                repo_offers.upsert_offer(conn, row, run_id)
                loaded += 1
            if seen and not loaded:
                raise ValueError("no valid offers")
    except Exception as exc:
        conn.execute(
            "UPDATE import_runs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
            (_now(), str(exc)[:500], run_id),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE import_runs SET status = 'ok', finished_at = ?, fetched = ? WHERE id = ?",
        (_now(), loaded, run_id),
    )
    conn.commit()
    return run_id


def load_json(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("expected a JSON list of offers")
    return data
