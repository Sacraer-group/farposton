from html.parser import HTMLParser

import pytest

from flats import db, repo_user
from flats.cli import DEMO_FILES
from flats.importer import load_json, run_import
from flats.web import create_app


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


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


def test_favorites_start_hidden_independently_of_legacy_saves(app):
    html = app.test_client().get("/favorites").get_data(as_text=True)
    assert 'value="avito:demo-1"' in html and 'value="farpost:demo-3"' in html
    elements = Elements(html).tags
    candidates = [attrs for _, attrs in elements if "data-saved-offer" in attrs]
    assert candidates and all("hidden" in attrs for attrs in candidates)
    checkboxes = [attrs for tag, attrs in elements if tag == "input" and attrs.get("name") == "o"]
    assert len(checkboxes) > 2
    assert all("disabled" in attrs and "checked" not in attrs for attrs in checkboxes)
    [compare_form] = [attrs for tag, attrs in elements if tag == "form" and attrs.get("id") == "compare-form"]
    assert "hidden" in compare_form
    [favorites_content] = [attrs for _, attrs in elements if attrs.get("id") == "favorites-content"]
    assert "hidden" in favorites_content

    conn = db.connect(app.config["DB_PATH"])
    repo_user.decide(conn, "farpost", "demo-6", "save")
    conn.close()
    assert app.test_client().get("/favorites").get_data(as_text=True) == html


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
    assert "Объявления не найдены: 1" in html


def test_explicit_selection_does_not_require_a_server_favorite(app):
    html = app.test_client().get("/compare?o=farpost:demo-6").get_data(as_text=True)
    assert 'href="https://example.com/demo/demo-6"' in html
    assert "32 000 ₽" in html
