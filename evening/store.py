from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .timeutil import TZ, iso_vl, now_vl

DEFAULT_DEMO_DIR = Path(".demo/evening")


def demo_dir() -> Path:
    return Path(os.getenv("EVENING_DEMO_DIR", DEFAULT_DEMO_DIR))


def db_path() -> Path:
    return Path(os.getenv("EVENING_DB_PATH", str(demo_dir() / "plans.sqlite3")))


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    return conn


def create_plan(snapshot: dict) -> dict:
    plan_id = str(uuid4())
    created = now_vl()
    expires = created + timedelta(hours=24)
    record = {
        "id": plan_id,
        "created_at": iso_vl(created),
        "expires_at": iso_vl(expires),
        **snapshot,
    }
    # store snapshot without current
    to_store = {k: v for k, v in record.items() if k != "current"}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO plans (id, snapshot_json, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (plan_id, json.dumps(to_store, ensure_ascii=False), iso_vl(created), iso_vl(expires)),
        )
        conn.commit()
    return record


def get_plan(plan_id: str):
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, snapshot_json, created_at, expires_at FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
    if row is None:
        return None, "not_found"
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except Exception:
        expires_at = now_vl() - timedelta(seconds=1)
    if expires_at <= now_vl():
        return None, "expired"
    snapshot = json.loads(row["snapshot_json"])
    snapshot["id"] = row["id"]
    snapshot["created_at"] = row["created_at"]
    snapshot["expires_at"] = row["expires_at"]
    return snapshot, "ok"
