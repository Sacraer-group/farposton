"""UI contracts for the weekly routine, safe evidence and mutation forms."""
from pathlib import Path

from flask import render_template
from flats.web import create_app


def test_trip_editor_and_csrf_forms(tmp_path):
    app = create_app(tmp_path / 'ui.db')
    app.testing = True
    client = app.test_client()
    response = client.get('/trips')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Дом, из которого удобно добираться' in html
    for control in ('place-search', 'manual-place', 'trip-places', 'trip-legs', 'add-leg',
                    'start-trips', 'cancel-trips', 'trip-progress'):
        assert f'id="{control}"' in html
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
