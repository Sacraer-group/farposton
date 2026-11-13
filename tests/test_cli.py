import json
import sqlite3

from flats.cli import main


def count(db_file):
    with sqlite3.connect(db_file) as conn:
        return conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0]


def test_init_db(tmp_path, monkeypatch):
    db_file = tmp_path / "x" / "t.db"
    monkeypatch.setenv("FLATS_DB", str(db_file))
    assert main(["init-db"]) == 0
    assert db_file.is_file()


def test_demo_sets(tmp_path, monkeypatch, capsys):
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("FLATS_DB", str(db_file))
    assert main(["demo"]) == 0
    assert count(db_file) == 50
    assert main(["demo", "--set", "invented"]) == 0
    assert count(db_file) == 76
    assert "imported 26 offers" in capsys.readouterr().out


def test_import_json_and_failure(tmp_path, monkeypatch):
    db_file = tmp_path / "t.db"
    monkeypatch.setenv("FLATS_DB", str(db_file))
    good = tmp_path / "ok.json"
    good.write_text(json.dumps([{"source": "farpost", "source_id": "1", "price": 1}]), "utf-8")
    assert main(["import-json", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{}", "utf-8")
    assert main(["import-json", str(bad)]) == 1
    assert count(db_file) == 1
