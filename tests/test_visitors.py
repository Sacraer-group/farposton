import pytest
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
    assert a.post('/api/decide',json={'source':'demo','source_id':'1','action':'save'}).status_code==403
    assert a.post('/api/decide',json={'source':'demo','source_id':'1','action':'save'},headers=bh).status_code==403
    assert a.post('/api/decide',json={'source':'demo','source_id':'1','action':'save'},headers=ah).status_code==200
    assert '30 000' in a.get('/favorites').get_data(as_text=True)
    assert '30 000' not in b.get('/favorites').get_data(as_text=True)
    assert b.post('/api/undo',json={},headers=bh).get_json()['undone'] is None
    assert '30 000' in a.get('/favorites').get_data(as_text=True)
    a.post('/',data={'apply':'1','monthly_max':'12345'},headers=ah)
    assert 'value="12345"' in a.get('/').get_data(as_text=True)
    assert 'value="12345"' not in b.get('/').get_data(as_text=True)

def test_public_requires_stable_secret(monkeypatch,tmp_path):
    monkeypatch.setenv('FLATS_PUBLIC_DEMO','1');monkeypatch.delenv('FLATS_SECRET_KEY',raising=False)
    with pytest.raises(RuntimeError):create_app(tmp_path/'test.db')
