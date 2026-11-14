import json
import sqlite3
from datetime import datetime, timezone

from .types import Profile
from .visitors import owner


def validate_browser_decisions(values) -> dict[tuple[str, str], str]:
    """Validate a browser snapshot without reading or writing persisted decisions."""
    if not isinstance(values, list):
        raise ValueError("Неверные решения браузера")
    decisions = {}
    for entry in values:
        if (not isinstance(entry, list) or len(entry) != 3
                or any(not isinstance(value, str) or not value for value in entry[:2])
                or len(entry[0]) + len(entry[1]) + 1 > 300
                or entry[2] not in ("save", "skip")):
            raise ValueError("Неверные решения браузера")
        key = (entry[0], entry[1])
        if key in decisions:
            raise ValueError("Решения браузера не должны повторяться")
        decisions[key] = entry[2]
    return decisions


def get_profile(conn: sqlite3.Connection) -> Profile:
    if owner() != 'local':
        row = conn.execute('SELECT profile_json FROM visitor_profiles WHERE visitor_id=?', (owner(),)).fetchone()
        if row is None:
            return Profile(None, None, (), (), False, None)
        d = json.loads(row['profile_json'])
        return Profile(d['monthly_max'], d['move_in_max'], tuple(d['rooms']), tuple(d['districts']), d['pets_required'], d['sample_photo_url'])
    row = conn.execute("SELECT * FROM search_profile WHERE id = 1").fetchone()
    if row is None:
        return Profile(None, None, (), (), False, None)
    return Profile(
        monthly_max=row["monthly_max"],
        move_in_max=row["move_in_max"],
        rooms=tuple(json.loads(row["rooms_json"])),
        districts=tuple(json.loads(row["districts_json"])),
        pets_required=bool(row["pets_required"]),
        sample_photo_url=row["sample_photo_url"],
    )


def save_profile(conn: sqlite3.Connection, p: Profile) -> None:
    if owner() != 'local':
        from dataclasses import asdict
        conn.execute('INSERT OR REPLACE INTO visitor_profiles VALUES (?,?)', (owner(), json.dumps(asdict(p))))
        conn.commit()
        return
    conn.execute(
        """
        INSERT OR REPLACE INTO search_profile (
            id, monthly_max, move_in_max, rooms_json, districts_json,
            pets_required, sample_photo_url
        ) VALUES (1, ?, ?, ?, ?, ?, ?)
        """,
        (
            p.monthly_max,
            p.move_in_max,
            json.dumps(list(p.rooms), ensure_ascii=False),
            json.dumps(list(p.districts), ensure_ascii=False),
            int(p.pets_required),
            p.sample_photo_url,
        ),
    )
    conn.commit()


def decide(
    conn: sqlite3.Connection, source: str, source_id: str, action: str
) -> int:
    if action not in ("save", "skip"):
        raise ValueError("action must be 'save' or 'skip'")
    if owner() != "local":
        cursor = conn.execute("INSERT INTO visitor_decisions(visitor_id,source,source_id,action,created_at) VALUES (?,?,?,?,?)", (owner(),source,source_id,action,datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return cursor.lastrowid
    cursor = conn.execute(
        """
        INSERT INTO decisions (source, source_id, action, created_at, undone)
        VALUES (?, ?, ?, ?, 0)
        """,
        (source, source_id, action, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cursor.lastrowid


def undo_last(conn: sqlite3.Connection) -> int | None:
    if owner() != 'local':
        row=conn.execute('SELECT id FROM visitor_decisions WHERE visitor_id=? AND undone=0 ORDER BY id DESC LIMIT 1',(owner(),)).fetchone()
        if row is None:
            return None
        conn.execute('UPDATE visitor_decisions SET undone=1 WHERE id=? AND visitor_id=?',(row['id'],owner()))
        conn.commit()
        return row['id']
    row = conn.execute(
        "SELECT id FROM decisions WHERE undone = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE decisions SET undone = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    return row["id"]


def current_decisions(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    if owner() != "local":
        rows=conn.execute("SELECT source,source_id,action FROM visitor_decisions WHERE visitor_id=? AND id IN (SELECT MAX(id) FROM visitor_decisions WHERE visitor_id=? AND undone=0 GROUP BY source,source_id)",(owner(),owner()))
        return {(r["source"],r["source_id"]):r["action"] for r in rows}
    rows = conn.execute(
        """
        SELECT source, source_id, action FROM decisions
        WHERE id IN (
            SELECT MAX(id) FROM decisions
            WHERE undone = 0
            GROUP BY source, source_id
        )
        """
    )
    return {(row["source"], row["source_id"]): row["action"] for row in rows}
