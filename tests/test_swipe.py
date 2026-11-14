import re

import pytest

from flats import db, repo_user
from flats.cli import DEMO_FILES
from flats.importer import load_json, run_import
from flats.search import search
from flats.types import Profile
from flats.web import create_app


@pytest.fixture
def app(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    run_import(conn, load_json(DEMO_FILES["invented"]))
    repo_user.save_profile(conn, Profile(50000, None, (), (), False, None))
    repo_user.decide(conn, "avito", "demo-1", "save")
    repo_user.decide(conn, "cian", "demo-2", "skip")
    conn.close()
    app = create_app(tmp_path / "t.db")
    app.testing = True
    return app


def test_swipe_ignores_legacy_decisions_for_new_browsers(app, tmp_path):
    html = app.test_client().get("/swipe").get_data(as_text=True)
    assert app.test_client().get("/swipe").get_data(as_text=True) == html
    shown = re.findall(r'data-source-id="([^"]+)"', html)[::-1]  # stack is rendered bottom-up
    conn = db.connect(tmp_path / "t.db")
    expected = [r.offer.source_id for r in search(conn, repo_user.get_profile(conn))]
    conn.close()
    assert shown == expected
    assert "demo-1" in shown and "demo-2" in shown
    assert "swipe.js" in html
    assert html.count("data-reset-skips") == 2


def test_empty_stack_message(tmp_path):
    html = create_app(tmp_path / "e.db").test_client().get("/swipe").get_data(as_text=True)
    assert "Подборка закончилась" in html and 'id="empty" hidden' not in html


def test_static_script_served(app):
    assert app.test_client().get("/static/swipe.js").status_code == 200


def test_gallery_template_handles_photo_counts_and_featured_photo(app):
    from types import SimpleNamespace
    from flask import render_template_string

    with app.test_request_context('/'):
        template = '{% from "_gallery.html" import gallery %}{{ gallery(offer, featured=featured) }}'
        def render(photos, featured=None):
            return render_template_string(template, offer=SimpleNamespace(photos=photos, address='test'), featured=featured)
        assert 'Нет фотографий' in render([])
        assert 'data-gallery-prev' not in render(['one'])
        html = render(['one', 'two'], 'two')
        assert 'data-index="1"' in html and 'src="two"' in html
        assert 'data-gallery-prev' in html and 'data-gallery-next' in html
        escaped = render(["https://example.test/'><script>alert(1)</script>", 'two'])
        assert '<script>alert(1)</script>' not in escaped


def test_gallery_is_used_on_favorites_and_swipe(app):
    client = app.test_client()
    for path in ('/favorites', '/swipe'):
        html = client.get(path).get_data(as_text=True)
        assert '/static/gallery.js' in html and '/static/gallery.css' in html
        assert 'Нет фотографий' in html or 'data-gallery' in html
