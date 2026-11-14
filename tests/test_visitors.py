import re

import pytest

from flats import repo_user
from flats.web import create_app, get_conn

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('FLATS_PUBLIC_DEMO','1')
    monkeypatch.setenv('FLATS_SECRET_KEY','test-session-secret-not-production')
    app=create_app(tmp_path/'t.db');app.testing=True
    with app.app_context():
        conn=get_conn()
        conn.execute("INSERT INTO import_runs(id,started_at,status) VALUES (1,'2026-11-14','ok')")
        conn.execute("INSERT INTO offers(source,source_id,address,price,raw_json,import_run_id) VALUES ('demo','1','улица Луговая 56',30000,'{}',1)")
        conn.commit()
    return app

def csrf(client):
    client.get('/')
    with client.session_transaction() as s:return {'X-CSRF-Token':s['csrf_token']}

def test_public_isolation_and_csrf(app):
    a,b=app.test_client(),app.test_client()
    ah,bh=csrf(a),csrf(b)
    with a.session_transaction() as s:
        visitor_id = s['visitor_id']
    with app.app_context():
        conn = get_conn()
        repo_user.decide(conn, 'demo', '1', 'skip')
        conn.execute("INSERT INTO visitor_decisions(visitor_id,source,source_id,action,created_at) VALUES (?,'demo','1','save','2026-11-14')", (visitor_id,))
        conn.commit()
        before = {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
                  for table in ('decisions', 'visitor_decisions')}
    for path in ('/api/decide', '/api/undo'):
        payload = {'source':'demo','source_id':'1','action':'save'}
        assert a.post(path,json=payload).status_code==403
        assert a.post(path,json=payload,headers=bh).status_code==403
        assert a.post(path,json=payload,headers=ah).status_code==410
        assert b.post(path,json=payload,headers=bh).status_code==410
    for client in (a, b):
        html = client.get('/favorites').get_data(as_text=True)
        assert '30 000' in html  # Candidates are shared; visibility is browser-local.
        assert re.search(r'id="favorites-content"[^>]*\bhidden\b', html)
        assert 'Пока ничего не сохранено' in html
    with app.app_context():
        conn = get_conn()
        assert {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
                for table in ('decisions', 'visitor_decisions')} == before
    a.post('/',data={'apply':'1','monthly_max':'12345'},headers=ah)
    assert 'value="12345"' in a.get('/').get_data(as_text=True)
    assert 'value="12345"' not in b.get('/').get_data(as_text=True)

def test_public_requires_stable_secret(monkeypatch,tmp_path):
    monkeypatch.setenv('FLATS_PUBLIC_DEMO','1');monkeypatch.delenv('FLATS_SECRET_KEY',raising=False)
    with pytest.raises(RuntimeError):create_app(tmp_path/'test.db')

def test_health_does_not_create_visitor(app):
    client = app.test_client()
    assert client.get('/health').status_code == 200
    with app.app_context():
        conn = get_conn()
        assert conn.execute('SELECT COUNT(*) FROM visitors').fetchone()[0] == 0
        assert any(row['name'] == 'visitors_seen_at' for row in conn.execute('PRAGMA index_list(visitors)'))


def test_invalid_place_query_does_not_consume_quota(app):
    client = app.test_client()
    assert client.get('/api/trips/places?q=').status_code == 400
    with app.app_context():
        assert get_conn().execute('SELECT COUNT(*) FROM trip_request_usage').fetchone()[0] == 0


def test_new_sessions_are_limited_by_client_not_cookie(app):
    for _ in range(30):
        assert app.test_client().get('/api/trips/schedule').status_code == 200
    assert app.test_client().get('/api/trips/schedule').status_code == 429
    assert app.test_client().get('/health').status_code == 200

def test_place_budget_is_shared_across_sessions_from_same_client(app, monkeypatch):
    from flats import routing
    monkeypatch.setenv('FLATS_TRIP_IP_BUDGET', '1')
    monkeypatch.setattr(routing, 'places', lambda query: [])
    first, second = app.test_client(), app.test_client()
    assert first.get('/api/trips/places?q=школа').status_code == 200
    assert second.get('/api/trips/places?q=школа', headers={'X-Forwarded-For': '203.0.113.25'}).status_code == 429
