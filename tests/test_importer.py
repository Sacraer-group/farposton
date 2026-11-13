from contextlib import closing

import pytest

from flats import db
from flats.importer import load_json, run_import


@pytest.fixture
def conn(tmp_path):
    with closing(db.connect(tmp_path / "t.db")) as connection:
        db.init_db(connection)
        yield connection


def row(source_id, price=40000):
    return {"source": "farpost", "source_id": source_id, "price": price}


def runs(conn):
    return conn.execute("SELECT * FROM import_runs ORDER BY id").fetchall()


def prices(conn):
    return dict(conn.execute("SELECT source_id, price FROM offers").fetchall())


def test_valid_rows(conn):
    run_id = run_import(conn, [row("1"), row("2")])
    run = runs(conn)[-1]
    assert (run["id"], run["status"], run["fetched"]) == (run_id, "ok", 2)
    assert run["finished_at"] is not None
    assert prices(conn) == {"1": 40000, "2": 40000}


def test_invalid_rows_are_skipped(conn):
    run_import(conn, [row("1"), {"source": "farpost"}, "junk"])
    assert runs(conn)[-1]["fetched"] == 1
    assert list(prices(conn)) == ["1"]


def test_only_invalid_rows_fail(conn):
    with pytest.raises(ValueError, match="no valid offers"):
        run_import(conn, [{"source": "farpost"}, None])
    run = runs(conn)[-1]
    assert run["status"] == "failed" and run["error"] == "no valid offers"
    assert prices(conn) == {}


def test_failure_keeps_previous_data(conn):
    run_import(conn, [row("1"), row("2")])

    def broken():
        yield row("1", price=99999)
        yield row("3")
        raise RuntimeError("source dropped")

    with pytest.raises(RuntimeError):
        run_import(conn, broken())
    first, second = runs(conn)
    assert first["status"] == "ok"
    assert second["status"] == "failed" and "source dropped" in second["error"]
    assert prices(conn) == {"1": 40000, "2": 40000}


def test_reimport_updates_and_keeps_missing(conn):
    first = run_import(conn, [row("1"), row("2")])
    second = run_import(conn, [row("1", price=45000)])
    assert prices(conn) == {"1": 45000, "2": 40000}
    run_ids = dict(conn.execute("SELECT source_id, import_run_id FROM offers").fetchall())
    assert run_ids == {"1": second, "2": first}


def test_load_json(tmp_path):
    good = tmp_path / "ok.json"
    good.write_text('[{"source": "farpost", "source_id": "1"}]', encoding="utf-8")
    assert load_json(good) == [{"source": "farpost", "source_id": "1"}]
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON list"):
        load_json(bad)
