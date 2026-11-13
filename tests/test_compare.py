import pytest

from flats import db, repo_user
from flats.cli import DEMO_FILES
from flats.importer import load_json, run_import
from flats.web import create_app


@pytest.fixture
def app(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    run_import(conn, load_json(DEMO_FILES["invented"]))
    for sid in ("demo-1", "demo-3"):
        source = "avito" if sid == "demo-1" else "farpost"
        repo_user.decide(conn, source, sid, "save")
    conn.close()
    app = create_app(tmp_path / "t.db")
    app.testing = True
    return app


def test_favorites_lists_saved(app):
    html = app.test_client().get("/favorites").get_data(as_text=True)
    assert 'value="avito:demo-1"' in html and 'value="farpost:demo-3"' in html
    assert html.count('name="o"') == 2


def test_empty_favorites(tmp_path):
    app = create_app(tmp_path / "e.db")
    assert "Пока ничего не сохранено" in app.test_client().get("/favorites").get_data(as_text=True)


def test_compare_two_offers(app):
    html = app.test_client().get("/compare?o=avito:demo-1&o=farpost:demo-3").get_data(as_text=True)
    assert "100 000 ₽" in html  # reference offer move-in
    assert "Какой залог" in html  # demo-3 has unknown deposit
    assert "https://example.com/demo/demo-1" in html


def test_compare_limits_and_unknown(app):
    many = "&".join(["o=avito:demo-1"] * 6)
    html = app.test_client().get(f"/compare?{many}&o=cian:nope").get_data(as_text=True)
    assert "не больше 5" in html
    html = app.test_client().get("/compare?o=cian:nope").get_data(as_text=True)
    assert "Не найдены в избранном: 1" in html


def test_unsaved_offer_not_compared(app):
    html = app.test_client().get("/compare?o=farpost:demo-6").get_data(as_text=True)
    assert "Не найдены в избранном: 1" in html
