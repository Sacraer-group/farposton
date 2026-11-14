import json
from contextlib import closing

import pytest

from flats import db, repo_offers
from flats.types import Offer

FULL = {
    "source": "farpost", "source_id": 1, "group_id": 7, "url": "https://example.test/1",
    "address": "ул. Светланская, 1", "district": "Центр", "lat": 43.1, "lon": 131.9,
    "rooms": 2, "area_total": 54.5, "floor": 3, "floors_total": 9, "price": 45_000,
    "deposit_raw": "45 000", "commission_raw": "50%", "prepayment_raw": "1 месяц",
    "utilities_raw": "включены", "pets_raw": "можно", "rent_period_raw": "длительно",
    "description": "Светлая квартира", "photos": ["https://example.test/a.jpg", "https://example.test/b.jpg"],
    "published_at": "2026-11-01", "last_seen": "2026-11-13",
    "raw": {"title": "2-к"}, "unknown_key": "ignored",
}


@pytest.fixture
def conn(tmp_path):
    with closing(db.connect(tmp_path / "t.db")) as connection:
        db.init_db(connection)
        connection.execute(
            "INSERT INTO import_runs (id, started_at, status) VALUES (1, '2026-11-13', 'ok')"
        )
        yield connection


def test_insert_and_read_back(conn):
    repo_offers.upsert_offer(conn, FULL, 1)
    [offer] = repo_offers.all_offers(conn)
    assert offer == Offer(
        "farpost", "1", "7", "https://example.test/1", "ул. Светланская, 1", "Центр",
        43.1, 131.9, 2, 54.5, 3, 9, 45_000, "45 000", "50%", "1 месяц", "включены",
        "можно", "длительно", "Светлая квартира",
        ("https://example.test/a.jpg", "https://example.test/b.jpg"),
        "2026-11-01", "2026-11-13",
    )
    assert isinstance(offer.photos, tuple)
    row = conn.execute("SELECT raw_json, import_run_id FROM offers").fetchone()
    assert json.loads(row["raw_json"]) == {"title": "2-к"}
    assert row["import_run_id"] == 1
    assert repo_offers.OFFER_FIELDS[:2] == ("source", "source_id")


def test_upsert_updates_existing_row(conn):
    conn.execute("INSERT INTO import_runs (id, started_at, status) VALUES (2, '2026-11-14', 'ok')")
    repo_offers.upsert_offer(conn, FULL, 1)
    repo_offers.upsert_offer(conn, {**FULL, "price": 50_000}, 2)
    rows = conn.execute("SELECT price, import_run_id FROM offers").fetchall()
    assert [tuple(r) for r in rows] == [(50_000, 2)]


def test_only_required_fields(conn):
    repo_offers.upsert_offer(conn, {"source": "farpost", "source_id": "2"}, 1)
    [offer] = repo_offers.all_offers(conn)
    assert (offer.source, offer.source_id) == ("farpost", "2")
    assert offer.photos == ()
    assert all(
        getattr(offer, name) is None
        for name in Offer.__dataclass_fields__ if name not in ("source", "source_id", "photos")
    )
    assert conn.execute("SELECT raw_json FROM offers").fetchone()[0] == "{}"


@pytest.mark.parametrize("row", [
    {"source": "farpost"}, {"source": "farpost", "source_id": ""},
    {"source_id": "1"}, {"source": "", "source_id": "1"},
])
def test_missing_required_raises(conn, row):
    with pytest.raises(ValueError):
        repo_offers.upsert_offer(conn, row, 1)


def test_private_raw_keys_are_dropped(conn):
    raw = {"title": "x", "phone": "+7900", "phones": ["+7901"], "seller_name": "Иван"}
    original = dict(raw)
    repo_offers.upsert_offer(conn, {"source": "farpost", "source_id": "3", "raw": raw}, 1)
    assert raw == original
    raw_json = conn.execute("SELECT raw_json FROM offers").fetchone()[0]
    assert json.loads(raw_json) == {"title": "x"}
    for secret in ("phone", "seller_name", "+7900", "+7901", "Иван"):
        assert secret not in raw_json


def test_offers_by_key(conn):
    repo_offers.upsert_offer(conn, {"source": "farpost", "source_id": "1", "group_id": "g1"}, 1)
    repo_offers.upsert_offer(conn, {"source": "avito", "source_id": "9", "group_id": "g1"}, 1)
    repo_offers.upsert_offer(conn, {"source": "farpost", "source_id": "5"}, 1)

    assert [(o.source, o.source_id) for o in repo_offers.offers_by_key(conn, "g1")] == [
        ("avito", "9"), ("farpost", "1"),
    ]
    assert [(o.source, o.source_id) for o in repo_offers.offers_by_key(conn, "farpost:5")] == [
        ("farpost", "5"),
    ]
    assert repo_offers.offers_by_key(conn, "farpost:1") == []
    assert repo_offers.offers_by_key(conn, "nope") == []
    assert repo_offers.offers_by_key(conn, "farpost:404") == []


def test_source_id_with_colon(conn):
    repo_offers.upsert_offer(conn, {"source": "avito", "source_id": "a:b"}, 1)
    [offer] = repo_offers.offers_by_key(conn, "avito:a:b")
    assert (offer.source, offer.source_id) == ("avito", "a:b")


def test_last_ok_run(conn):
    conn.execute("DELETE FROM import_runs")
    assert repo_offers.last_ok_run(conn) is None
    conn.executemany(
        "INSERT INTO import_runs (id, started_at, status) VALUES (?, '2026-11-13', ?)",
        [(1, "ok"), (2, "ok"), (3, "failed"), (4, "running")],
    )
    assert repo_offers.last_ok_run(conn)["id"] == 2


def test_upsert_does_not_commit(conn, tmp_path):
    conn.commit()
    repo_offers.upsert_offer(conn, {"source": "farpost", "source_id": "1"}, 1)
    assert conn.in_transaction
    with closing(db.connect(tmp_path / "t.db")) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM offers").fetchone()[0] == 0

def test_safe_url_rejects_executable_schemes_and_credentials():
    from flats.repo_offers import safe_url
    assert safe_url('https://example.com/image.jpg')
    for value in ('javascript:alert(1)', 'https://example.com\n.evil/', 'https://user@example.com/', '//example.com/x'):
        assert safe_url(value) is None

def test_existing_unsafe_offer_urls_are_not_rendered(conn):
    from flats import repo_offers
    conn.execute("INSERT INTO import_runs(id,started_at,status) VALUES (77,'2026-01-01','ok')")
    conn.execute("INSERT INTO offers(source,source_id,url,photos_json,raw_json,import_run_id) VALUES ('test','unsafe','javascript:alert(1)',?, '{}',77)", ('["javascript:alert(1)","https://example.com/a"]',))
    offer = next(o for o in repo_offers.all_offers(conn) if o.source_id == 'unsafe')
    assert offer.url is None
    assert offer.photos == ('https://example.com/a',)
