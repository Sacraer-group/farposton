import sys
from contextlib import closing
from types import ModuleType

import pytest

from flats import db, repo_offers, repo_user
from flats.search import search
from flats.types import Profile

KNOWN = dict(deposit_raw="Без залога", commission_raw="Без комиссии",
             prepayment_raw="за 1 месяц", pets_raw="можно")
EMPTY = Profile(None, None, (), (), False, None)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "flats.similar", None)  # visual search not available by default
    with closing(db.connect(tmp_path / "t.db")) as connection:
        db.init_db(connection)
        connection.execute("INSERT INTO import_runs (id, started_at, status) VALUES (1, 'x', 'ok')")
        yield connection


def add(conn, source_id, **fields):
    values = {"source": "farpost", "source_id": source_id, "rooms": 2, "district": "Центр", **KNOWN}
    values.update(fields)
    repo_offers.upsert_offer(conn, values, 1)
    conn.commit()


def ids(rows):
    return [r.offer.source_id for r in rows]


def test_budget_and_price_order(conn):
    add(conn, "a", price=60000)
    add(conn, "b", price=45000)
    add(conn, "c", price=40000)
    rows = search(conn, Profile(50000, None, (), (), False, None))
    assert ids(rows) == ["c", "b"]
    assert all(r.verdict.status == "match" for r in rows)


def test_unknown_after_match(conn):
    add(conn, "cheap-unknown", price=20000, deposit_raw=None)
    add(conn, "known", price=40000)
    rows = search(conn, Profile(None, 200000, (), (), False, None))
    assert ids(rows) == ["known", "cheap-unknown"]
    assert rows[1].verdict.status == "unknown"


def test_unknown_rent_last(conn):
    add(conn, "no-price")
    add(conn, "priced", price=90000)
    assert ids(search(conn, EMPTY)) == ["priced", "no-price"]


def test_skip_and_save(conn):
    add(conn, "s", price=40000)
    add(conn, "k", price=41000)
    repo_user.decide(conn, "farpost", "s", "save")
    repo_user.decide(conn, "farpost", "k", "skip")
    rows = search(conn, EMPTY)
    assert ids(rows) == ["s"] and rows[0].decision == "save"
    rows = search(conn, EMPTY, include_skipped=True)
    assert [(r.offer.source_id, r.decision) for r in rows] == [("s", "save"), ("k", "skip")]


def test_card_uses_first_non_rejected_offer(conn):
    add(conn, "cheap", group_id="g", price=30000, rooms=3)
    add(conn, "other", group_id="g", price=35000, rooms=2)
    [row] = search(conn, Profile(None, None, (2,), (), False, None))
    assert row.card.key == "g"
    assert row.offer.source_id == "other"
    assert row.card.best.source_id == "cheap"


def test_card_with_all_rejected_is_dropped(conn):
    add(conn, "x", group_id="g", price=30000, district="Чуркин")
    add(conn, "y", group_id="g", price=35000, district="Чуркин")
    assert search(conn, Profile(None, None, (), ("Центр",), False, None)) == []


def test_sample_without_visual_module(conn):
    add(conn, "a", price=40000)
    rows = search(conn, Profile(None, None, (), (), False, "https://x/1.jpg"))
    assert rows[0].similar is None


def test_similarity_orders_and_never_returns_rejected(conn, monkeypatch):
    add(conn, "a", price=40000)
    add(conn, "b", price=60000)
    add(conn, "rejected", price=90000)
    seen_keys = []

    def rank_by_photo(conn_, url, keys, model):
        seen_keys.extend(keys)
        return {"farpost:b": (0.9, "https://x/b.jpg"), "farpost:rejected": (0.99, "https://x/r.jpg")}

    fake = ModuleType("flats.similar")
    fake.MODEL = "m"
    fake.rank_by_photo = rank_by_photo
    monkeypatch.setitem(sys.modules, "flats.similar", fake)

    rows = search(conn, Profile(70000, None, (), (), False, "https://x/s.jpg"))
    assert ids(rows) == ["b", "a"]
    assert rows[0].similar == (0.9, "https://x/b.jpg")
    assert rows[1].similar is None
    assert "farpost:rejected" not in seen_keys
