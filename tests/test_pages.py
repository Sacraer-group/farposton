import pytest

from flats import db
from flats.cli import DEMO_FILES
from flats.importer import load_json, run_import
from flats.web import create_app


@pytest.fixture
def client(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    run_import(conn, load_json(DEMO_FILES["invented"]))
    conn.close()
    app = create_app(tmp_path / "t.db")
    app.testing = True
    return app.test_client()


def test_search_page_lists_offers(client):
    html = client.get("/").get_data(as_text=True)
    assert "40 000 ₽" in html and "100 000 ₽" in html
    assert "Данные от" in html


def test_filters_are_saved_and_applied(client):
    html = client.get("/?apply=1&monthly_max=36000&rooms=1&pets_required=1").get_data(as_text=True)
    assert "48 000 ₽" not in html
    assert 'value="36000"' in html
    assert client.get("/").get_data(as_text=True).count('value="36000"') == 1


def test_bad_number_shows_message(client):
    assert "Неверное число" in client.get("/?apply=1&monthly_max=abc").get_data(as_text=True)


def test_flat_page_shows_each_offer_and_raw_terms(client):
    html = client.get("/flat/demo-g1").get_data(as_text=True)
    assert html.count("Предложение ") == 3
    assert "Условия на площадках различаются" in html
    html = client.get("/flat/farpost:demo-3").get_data(as_text=True)
    assert "по договорённости" in html
    assert "Какой залог" in html


def test_flat_page_escapes_description_and_404(client):
    html = client.get("/flat/cian:demo-11").get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html
    assert client.get("/flat/nope").status_code == 404
