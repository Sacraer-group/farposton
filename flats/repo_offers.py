import json
import sqlite3
from urllib.parse import urlsplit

from .types import Offer

OFFER_FIELDS: tuple[str, ...] = (
    "source", "source_id", "group_id", "url", "address", "district",
    "lat", "lon", "rooms", "area_total", "floor", "floors_total", "price",
    "deposit_raw", "commission_raw", "prepayment_raw",
    "utilities_raw", "pets_raw", "rent_period_raw",
    "description", "photos_json", "published_at", "last_seen",
    "raw_json", "import_run_id",
)

_PRIVATE_RAW_KEYS = ("phone", "phones", "seller_name")

_UPSERT_SQL = (
    f"INSERT INTO offers ({', '.join(OFFER_FIELDS)}) "
    f"VALUES ({', '.join('?' for _ in OFFER_FIELDS)}) "
    "ON CONFLICT(source, source_id) DO UPDATE SET "
    + ", ".join(f"{name} = excluded.{name}" for name in OFFER_FIELDS[2:])
)


def safe_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def upsert_offer(conn: sqlite3.Connection, row: dict, run_id: int) -> None:
    if not row.get("source") or row.get("source_id") in (None, ""):
        raise ValueError("source and source_id are required")
    raw = {k: v for k, v in (row.get("raw") or {}).items() if k not in _PRIVATE_RAW_KEYS}
    values = dict(row)
    values["url"] = safe_url(row.get("url"))
    values["source_id"] = str(row["source_id"])
    if row.get("group_id") is not None:
        values["group_id"] = str(row["group_id"])
    values["photos_json"] = json.dumps([url for value in (row.get("photos") or []) if (url := safe_url(value))], ensure_ascii=False)
    values["raw_json"] = json.dumps(raw, ensure_ascii=False)
    values["import_run_id"] = run_id
    conn.execute(_UPSERT_SQL, [values.get(name) for name in OFFER_FIELDS])


def _to_offer(row: sqlite3.Row) -> Offer:
    data = {name: row[name] for name in OFFER_FIELDS if name not in ("photos_json", "raw_json", "import_run_id")}
    if isinstance(data["address"], str):
        data["address"] = data["address"].removesuffix(" Подробности о доме").strip()
    data["url"] = safe_url(data["url"])
    return Offer(**data, photos=tuple(url for value in json.loads(row["photos_json"]) if (url := safe_url(value))))


def all_offers(conn: sqlite3.Connection) -> list[Offer]:
    rows = conn.execute("SELECT * FROM offers ORDER BY source, source_id")
    return [_to_offer(row) for row in rows]


def offers_by_key(conn: sqlite3.Connection, key: str) -> list[Offer]:
    rows = conn.execute(
        "SELECT * FROM offers WHERE group_id = ? ORDER BY source, source_id", (key,)
    ).fetchall()
    if not rows and ":" in key:
        source, source_id = key.split(":", 1)
        rows = conn.execute(
            "SELECT * FROM offers WHERE source = ? AND source_id = ? AND group_id IS NULL",
            (source, source_id),
        ).fetchall()
    return [_to_offer(row) for row in rows]


def last_ok_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM import_runs WHERE status = 'ok' ORDER BY id DESC LIMIT 1"
    ).fetchone()
