import re

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


def test_each_grouped_offer_has_its_own_browser_save_payload(client):
    html = client.get("/flat/demo-g1").get_data(as_text=True)
    forms = re.findall(r'(<form[^>]*class="offer-save-form"[^>]*>)(.*?)</form>', html, re.S)
    pairs = set()
    for tag, form in forms:
        assert "data-decision-form" in tag
        payload = dict(re.findall(r'name="(source|source_id|action)" value="([^"]+)"', form))
        pairs.add((payload["source"], payload["source_id"]))
        assert payload["action"] == "save"
    assert pairs == {("farpost", "demo-12"), ("avito", "demo-13"), ("cian", "demo-14")}
    favorites = client.get("/favorites").get_data(as_text=True)
    for source, source_id in pairs:
        assert f'value="{source}:{source_id}"' in favorites


def test_house_data_is_distinguished_from_listing_data(client):
    html = client.get("/flat/demo-g1").get_data(as_text=True)
    assert "Дом требует проверки" in html
    assert "Этажность по объявлению" in html
    assert "Сведения не получены" in html
    assert "Это не означает, что отключений нет" in html
    assert 'href="https://dom.gosuslugi.ru/#!/houses"' in html


def test_map_does_not_promote_unverified_listing_coordinates(client):
    conn = db.connect(client.application.config["DB_PATH"])
    run_import(conn, [{"source": "farpost", "source_id": "map-xss", "lat": 43.125,
                      "lon": 131.875, "address": '<img src=x onerror="alert(1)">'}])
    conn.close()
    html = client.get("/").get_data(as_text=True)
    assert 'data-lat="43.125" data-lon="131.875"' not in html
    assert '&lt;img src=x onerror=&#34;alert(1)&#34;&gt;' in html
    assert '<img src=x onerror="alert(1)">' not in html


def test_sample_banner_and_similar_photo(tmp_path):
    import numpy as np
    from flats import repo_user
    from flats.similar import MODEL

    conn = db.connect(tmp_path / "s.db")
    db.init_db(conn)
    run_import(conn, [{"source": "farpost", "source_id": "1", "price": 30000, "photos": ["https://x/1.jpg", "https://x/2.jpg"]},
                      {"source": "farpost", "source_id": "2", "price": 20000, "photos": ["https://x/3.jpg"]}])
    for url, sid, vec in [("https://x/1.jpg", "1", [1, 0]), ("https://x/2.jpg", "1", [0, 1]), ("https://x/3.jpg", "2", [0.6, 0.8])]:
        conn.execute("INSERT INTO photo_features VALUES (?, 'farpost', ?, 'h', ?, ?)",
                     (url, sid, MODEL, np.array(vec, dtype=np.float32).tobytes()))
    conn.commit()
    app = create_app(tmp_path / "s.db")
    client = app.test_client()
    client.post("/api/sample", data={"photo_url": "https://x/2.jpg"})
    html = client.get("/").get_data(as_text=True)
    assert "Сбросить образец" in html and "Похожий интерьер" in html
    assert html.index("https://x/2.jpg\" alt=\"Похожий") < html.index("https://x/3.jpg")
    client.post("/api/sample/clear")
    assert "Сбросить образец" not in client.get("/").get_data(as_text=True)
    assert repo_user.get_profile(conn).sample_photo_url is None
    conn.close()


def test_move_in_includes_first_rent_and_filters_without_prepayment(client):
    conn = db.connect(client.application.config["DB_PATH"])
    run_import(conn, [{"source": "farpost", "source_id": "rent-first", "price": 40000,
                       "deposit_raw": "40 000 ₽", "commission_raw": "20 000 ₽",
                       "address": "Проверка первого месяца"}])
    conn.close()
    html = client.get("/flat/farpost:rent-first").get_data(as_text=True)
    assert "100 000 ₽" in html
    assert "Первый месяц аренды учтён" in html
    html = client.get("/?apply=1&move_in_max=90000").get_data(as_text=True)
    assert "Проверка первого месяца" not in html
