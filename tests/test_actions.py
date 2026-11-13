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


def test_decide_json_save(app, client):
    r = client.post("/api/decide", json={**OFFER, "action": "save"})
    assert r.status_code == 200
    assert isinstance(r.get_json()["id"], int)
    assert db(app, repo_user.current_decisions) == {("cian", "1"): "save"}


def test_decide_form_redirects(app, client):
    r = client.post("/api/decide", data={**OFFER, "action": "skip"})
    assert r.status_code == 303
    assert r.headers["Location"] == "/"
    r = client.post(
        "/api/decide", data={**OFFER, "action": "save"},
        headers={"Referer": "http://localhost/favorites"},
    )
    assert r.status_code == 303
    assert r.headers["Location"] == "http://localhost/favorites"
    r = client.post(
        "/api/decide", data={**OFFER, "action": "save"},
        headers={"Referer": "https://evil.test/"},
    )
    assert r.status_code == 303
    assert r.headers["Location"] == "/"


def test_decide_errors(app, client):
    r = client.post("/api/decide", json={**OFFER, "action": "like"})
    assert r.status_code == 400
    assert "error" in r.get_json()
    r = client.post("/api/decide", json={"source": "cian", "source_id": "2", "action": "save"})
    assert r.status_code == 404
    assert client.post("/api/decide", json={"source": "cian", "action": "save"}).status_code == 400
    r = client.post("/api/decide", data={**OFFER, "action": "like"})
    assert r.status_code == 400
    assert r.mimetype == "text/plain"
    assert "Location" not in r.headers
    assert db(app, repo_user.current_decisions) == {}


def test_undo(app, client):
    assert client.post("/api/undo", json={}).get_json() == {"undone": None}
    client.post("/api/decide", json={**OFFER, "action": "save"})
    last = client.post("/api/decide", json={**OFFER, "action": "skip"}).get_json()["id"]
    assert client.post("/api/undo", json={}).get_json() == {"undone": last}
    assert db(app, repo_user.current_decisions) == {("cian", "1"): "save"}


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


def test_get_not_allowed(client):
    assert client.get("/api/undo").status_code == 405
