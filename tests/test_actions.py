import pytest

from flats import repo_user
from flats.web import create_app, get_conn

PHOTO = "https://x/1.jpg"


@pytest.fixture
def app(tmp_path):
    app = create_app(tmp_path / "t.db")
    app.testing = True
    with app.app_context():
        conn = get_conn()
        conn.execute(
            "INSERT INTO import_runs (id, started_at, status) VALUES (1, '2026-11-13', 'ok')"
        )
        conn.execute(
            "INSERT INTO offers (source, source_id, photos_json, raw_json, import_run_id)"
            " VALUES ('cian', '1', ?, '{}', 1)",
            (f'["{PHOTO}"]',),
        )
        conn.commit()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def db(app, fn):
    with app.app_context():
        return fn(get_conn())


OFFER = {"source": "cian", "source_id": "1"}


@pytest.mark.parametrize("path", ["/api/decide", "/api/undo"])
@pytest.mark.parametrize("encoding", ["json", "data"])
def test_global_decision_endpoints_are_retired(app, client, path, encoding):
    db(app, lambda conn: repo_user.decide(conn, "cian", "1", "save"))
    before = db(app, lambda conn: [tuple(row) for row in conn.execute("SELECT * FROM decisions")])
    r = client.post(path, **{encoding: {**OFFER, "action": "skip"}})
    assert r.status_code == 410
    if encoding == "json":
        assert "error" in r.get_json()
    else:
        assert r.mimetype == "text/plain"
    assert "Location" not in r.headers
    assert db(app, lambda conn: [tuple(row) for row in conn.execute("SELECT * FROM decisions")]) == before


def test_sample_form_redirects(client):
    r = client.post("/api/sample", data={"photo_url": PHOTO})
    assert r.status_code == 303
    assert r.headers["Location"] == "/"
    r = client.post(
        "/api/sample", data={"photo_url": PHOTO},
        headers={"Referer": "http://localhost/favorites"},
    )
    assert r.status_code == 303
    assert r.headers["Location"] == "http://localhost/favorites"
    r = client.post(
        "/api/sample", data={"photo_url": PHOTO},
        headers={"Referer": "https://evil.test/"},
    )
    assert r.status_code == 303
    assert r.headers["Location"] == "/"


def test_sample(app, client):
    r = client.post("/api/sample", json={"photo_url": PHOTO})
    assert r.get_json() == {"sample_photo_url": PHOTO}
    assert db(app, repo_user.get_profile).sample_photo_url == PHOTO
    r = client.post("/api/sample", json={"photo_url": "https://x/2.jpg"})
    assert r.status_code == 400
    assert client.post("/api/sample", data={"photo_url": ""}).status_code == 400
    assert db(app, repo_user.get_profile).sample_photo_url == PHOTO
    r = client.post("/api/sample/clear", json={})
    assert r.get_json() == {"sample_photo_url": None}
    assert db(app, repo_user.get_profile).sample_photo_url is None


@pytest.mark.parametrize("path", ["/api/decide", "/api/undo"])
def test_get_not_allowed(client, path):
    assert client.get(path).status_code == 405
