import sqlite3
from pathlib import Path

from . import config


def connect(path: Path | None = None) -> sqlite3.Connection:
    if path is None:
        path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    from flats.visitors import init_visitors
    init_visitors(conn)
    from flats.repo_houses import init_house_db
    init_house_db(conn)
    from flats.trips import init_trips_db
    init_trips_db(conn)
