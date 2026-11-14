import sqlite3
from contextlib import closing

import numpy as np
import pytest

from flats import db, embed, repo_offers
from flats.similar import cosine_scores, rank_by_photo


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FLATS_PHOTO_DIR", str(tmp_path / "photos"))
    with closing(db.connect(tmp_path / "t.db")) as connection:
        db.init_db(connection)
        connection.execute("INSERT INTO import_runs (id, started_at, status) VALUES (1, 'x', 'ok')")
        for sid, gid, photos in [("a", None, ["https://static.baza.farpost.ru/u/a1", "https://static.baza.farpost.ru/u/a2"]), ("b", "g", ["https://static.baza.farpost.ru/u/b1"]), ("c", None, ["https://static.baza.farpost.ru/u/bad"])]:
            repo_offers.upsert_offer(connection, {"source": "farpost", "source_id": sid, "group_id": gid, "photos": photos}, 1)
        connection.commit()
        yield connection


def fake_vectors(monkeypatch):
    table = {"a1": [1, 0, 0], "a2": [0, 1, 0], "b1": [0.8, 0.6, 0]}
    monkeypatch.setattr(embed, "_download", lambda url: (_ for _ in ()).throw(OSError("404")) if "bad" in url else url.encode())

    class Img:
        def __init__(self, data):
            self.name = data.decode().split("/")[-1]

        def convert(self, mode):
            return self

    monkeypatch.setattr("PIL.Image.open", lambda buf: Img(buf.getvalue()))
    monkeypatch.setattr(embed, "_encode", lambda images, model: np.array([table[i.name] for i in images], dtype=np.float32))


def test_embed_missing_skips_broken_and_is_incremental(conn, monkeypatch):
    pytest.importorskip("PIL")
    fake_vectors(monkeypatch)
    assert embed.embed_missing(conn, model_name="m") == 3
    assert embed.embed_missing(conn, model_name="m") == 0
    assert conn.execute("SELECT COUNT(*) FROM photo_features").fetchone()[0] == 3


def test_rank_by_photo(conn, monkeypatch):
    pytest.importorskip("PIL")
    fake_vectors(monkeypatch)
    embed.embed_missing(conn, model_name="m")
    scores = rank_by_photo(conn, "https://static.baza.farpost.ru/u/a1", ["farpost:a", "g", "farpost:c"], "m")
    assert scores["farpost:a"] == (1.0, "https://static.baza.farpost.ru/u/a1")
    assert scores["g"][1] == "https://static.baza.farpost.ru/u/b1" and scores["g"][0] == pytest.approx(0.8)
    assert "farpost:c" not in scores
    assert rank_by_photo(conn, "https://static.baza.farpost.ru/u/unknown", ["g"], "m") == {}
    assert rank_by_photo(conn, "https://static.baza.farpost.ru/u/a1", ["g"], "other-model") == {}


def test_cosine_scores():
    m = np.array([[1, 0], [0, 1]], dtype=np.float32)
    assert cosine_scores(np.array([1, 0], dtype=np.float32), m).tolist() == [1, 0]
