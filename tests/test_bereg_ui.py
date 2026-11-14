"""UI contracts for the weekly routine, safe evidence and mutation forms."""
from pathlib import Path

import pytest
from flask import render_template
from flats.web import create_app


@pytest.mark.parametrize(
    ("path", "active"),
    [("/", "/"), ("/trips", "/trips"), ("/advisor", "/advisor"),
     ("/favorites", "/favorites"), ("/compare", "/favorites"), ("/swipe", "/swipe")],
)
def test_page_navigation_and_keyboard_entry(tmp_path, path, active):
    client = create_app(tmp_path / "navigation.db").test_client()
    response = client.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'href="{active}" aria-current="page"' in html
    assert 'href="#main"' in html
    assert '<main id="main" class="page-shell" tabindex="-1">' in html
    assert 'name="csrf-token"' in html
    assert 'class="swipe-nav" aria-label="Свайпы" title="Свайпы"' in html
    assert '/static/decisions.js' in html
    assert 'id="theme-toggle"' in html
    assert html.index('/static/theme.js') < html.index('/static/app.css')
    if path == "/advisor":
        assert 'id="advisor-results" aria-labelledby="results-heading" aria-busy="false" tabindex="-1"' in html


def test_map_key_is_separate_from_server_routing_key(tmp_path, monkeypatch):
    key_file = tmp_path / "routing-key"
    key_file.write_text("private-routing-key")
    monkeypatch.setenv("FLATS_2GIS_KEY_FILE", str(key_file))
    monkeypatch.setenv("FLATS_2GIS_MAP_KEY", "browser-map-key")
    client = create_app(tmp_path / "map.db").test_client()
    for path in ("/", "/trips"):
        html = client.get(path).get_data(as_text=True)
        assert '<meta name="2gis-map-key" content="browser-map-key">' in html
        assert "private-routing-key" not in html
        assert "/static/map-sdk.js" in html
        assert html.index("https://mapgl.2gis.com/api/js/v1") < html.index("/static/map-sdk.js")
        assert "leaflet" not in html.lower()
    monkeypatch.delenv("FLATS_2GIS_MAP_KEY")
    html = create_app(tmp_path / "no-map.db").test_client().get("/").get_data(as_text=True)
    assert '<meta name="2gis-map-key" content="">' in html


def test_local_ruble_font_is_served_with_its_license(tmp_path):
    client = create_app(tmp_path / "fonts.db").test_client()
    font = client.get("/static/fonts/InterGlyphs.woff2")
    assert font.status_code == 200
    assert font.data.startswith(b"wOF2")
    license = client.get("/static/fonts/LICENSE.txt").get_data(as_text=True)
    assert "SIL OPEN FONT LICENSE Version 1.1" in license
    css = client.get("/static/app.css").get_data(as_text=True)
    assert 'url("fonts/InterGlyphs.woff2")' in css
    assert "unicode-range: U+20BD" in css
    assert '"InterGlyphs", Arial, sans-serif' in css


def test_trip_editor_and_csrf_forms(tmp_path):
    app = create_app(tmp_path / 'ui.db')
    app.testing = True
    client = app.test_client()
    response = client.get('/trips')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Дом, из которого удобно добираться' in html
    for control in ('place-search', 'manual-place', 'trip-places', 'trip-legs', 'add-leg',
                    'start-trips', 'partial-trips', 'quick-out', 'quick-back', 'cancel-trips', 'trip-progress'):
        assert f'id="{control}"' in html
    assert 'trip-scope' not in html
    assert 'Подобрать квартиры' in html
    assert 'Рассчитать доступную часть' in html
    assert 'name="csrf-token"' in html
    assert '/static/trips.js' in html
    assert 'name="csrf_token"' in client.get('/').get_data(as_text=True)


def test_evidence_escapes_names_and_labels_pedestrian(tmp_path):
    from types import SimpleNamespace
    app = create_app(tmp_path / 'evidence.db')
    with app.test_request_context('/'):
        from flask import render_template_string
        html = render_template_string(
            '{% from "_evidence.html" import evidence with context %}{{ evidence(o, true) }}',
            o=SimpleNamespace(source='metrapi',source_id='1'),
            trips_by_offer={('metrapi','1'):{'status':'acceptable','weekly_seconds':1800,
                 'legs':[{'date':'2026-11-16','departure':'08:30','from_name':'<script>x</script>',
                          'to_name':'ДВФУ','duration_seconds':1800,'pedestrian':True}]}},
            house_links_by_offer={})
        assert '&lt;script&gt;' in html
        assert '<script>x</script>' not in html
        assert '30 мин · пешком' in html
        assert 'пересадок' not in html
