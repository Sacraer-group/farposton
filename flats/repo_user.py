import json
import sqlite3
from datetime import datetime, timezone

from .types import Profile


def get_profile(conn: sqlite3.Connection) -> Profile:
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
    row = conn.execute(
        "SELECT id FROM decisions WHERE undone = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE decisions SET undone = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    return row["id"]


def current_decisions(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
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
