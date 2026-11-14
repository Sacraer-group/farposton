from contextlib import closing
from datetime import datetime
from types import SimpleNamespace

import pytest

from flats import db, trips


@pytest.fixture
def conn(tmp_path):
    with closing(db.connect(tmp_path/'trips.db')) as c:
        db.init_db(c);trips.init_trips_db(c)
        trips._JOBS.clear();trips._CURRENT.clear();trips._USAGE.clear()
        yield c


def schedule():
    return {'places':[{'id':'a','name':'ДВФУ корпус','lat':43,'lon':131,'confirmed':True},
                      {'id':'b','name':'Луговая','lat':43.1,'lon':131.1,'confirmed':True}],
            'legs':[{'id':'l1','from':'home','to':'a','days':[0,2],'departure':'08:30','max_minutes':60,'max_transfers':1},
                    {'id':'l2','from':'a','to':'b','days':[0],'departure':'16:30'},
                    {'id':'l3','from':'b','to':'home','days':[0],'departure':'19:30'}]}


def row(ident='a',price=100,lat=43.2):
    return SimpleNamespace(offer=SimpleNamespace(source='s',source_id=ident,lat=lat,lon=131.2),
                           card=SimpleNamespace(key=ident),costs=SimpleNamespace(monthly_known=price),similar=None)


def provider(source,target,start_time):
    return {'status':'ok','alternatives':[{'duration_seconds':1800,'transfers':1,'pedestrian':False,'walkway':'5 мин'}]}


def finish(conn,job,owner='one',get_route=provider):
    for _ in range(20):
        job=trips.step_job(conn,job['id'],owner,provider=get_route)
        if job['status']!='running':return job
    assert False,'job failed to finish'


def test_validate_many_places_and_independent_trips():
    data=schedule()
    for i in range(12):data['places'].append({'id':f'p{i}','name':f'place{i}','lat':43,'lon':131,'confirmed':True})
    data['legs'].append({'id':'ind','from':'home','to':'p0','days':[1],'departure':'09:00'})
    assert len(trips.validate_schedule(data)['places'])==14


@pytest.mark.parametrize('mutate',[lambda s:s['places'][0].update(confirmed=False),lambda s:s['legs'][0].update(days=[7]),
                                   lambda s:s['legs'][0].update(departure='25:00'),lambda s:s['legs'][0].update(to='missing'),
                                   lambda s:s['legs'][0].update(max_minutes=True)])
def test_invalid_schedule_rejected(mutate):
    data=schedule();mutate(data)
    with pytest.raises(ValueError):trips.validate_schedule(data)


def test_version_and_owner_isolation(conn):
    first=trips.save_schedule(conn,schedule(),'one')
    assert first['version']==1
    assert trips.save_schedule(conn,schedule(),'one')['version']==1
    assert trips.get_schedule(conn,'two')['version']==0
    data=schedule();data['legs'][0]['departure']='09:00'
    assert trips.save_schedule(conn,data,'one')['version']==2


def test_weekly_count_dates_dedup_and_chunks(conn):
    trips.save_schedule(conn,schedule(),'one')
    job=trips.start_job(conn,[row('a'),row('b')],'one')
    calls=[]
    def counted(*args):calls.append(args);return provider(*args)
    first=trips.step_job(conn,job['id'],'one',provider=counted)
    assert len(calls)==3
    final=finish(conn,first,get_route=counted)
    assert final['status']=='complete'
    assert len(calls)==4  # same locations and legs across flats reused
    assert final['results']['s:a']['weekly_seconds']==4*1800
    assert len(final['results']['s:a']['legs'])==4
    assert datetime.fromisoformat(final['week_start']).weekday()==0


def test_sort_complete_price_and_reject_constraints(conn):
    trips.save_schedule(conn,schedule(),'one')
    rows=[row('expensive',200),row('cheap',100)]
    job=trips.start_job(conn,rows,'one');finish(conn,job)
    assert [r.card.key for r in trips.rank_rows(conn,rows,'one')]==['cheap','expensive']
    assert trips.trip_context(conn,[('s','cheap')],'one')[('s','cheap')]['status']=='acceptable'
    data=schedule();data['legs'][0]['max_minutes']=10
    trips.save_schedule(conn,data,'one');job=trips.start_job(conn,rows,'one');finish(conn,job)
    assert trips.rank_rows(conn,rows,'one')==[]


def test_budget_and_cancellation_do_not_claim_complete(conn,monkeypatch):
    trips.save_schedule(conn,schedule(),'one')
    monkeypatch.setenv('FLATS_TRIP_SESSION_BUDGET','1')
    job=trips.start_job(conn,[row()],'one');job=finish(conn,job)
    assert job['status']=='budget'
    assert job['results']['s:a']['weekly_seconds'] is None
    assert any(l['status']=='budget' for l in job['results']['s:a']['legs'])
    job=trips.start_job(conn,[row()],'one')
    trips.cancel_job(conn,job['id'],'one')
    assert trips.step_job(conn,job['id'],'one',provider=provider)['done']==0


def test_overlap_computed_arrival_and_alternative_choice(conn):
    data=schedule();data['legs'][1]['departure']='08:40'
    trips.save_schedule(conn,data,'one');job=trips.start_job(conn,[row()],'one');job=finish(conn,job)
    assert job['results']['s:a']['status']=='violates'
    assert 'schedule_conflict' in [l['status'] for l in job['results']['s:a']['legs']]


def test_unknown_coordinates_and_no_route_differ(conn):
    trips.save_schedule(conn,schedule(),'one')
    job=trips.start_job(conn,[row('unknown',lat=None),row('unreachable')],'one')
    job=finish(conn,job,get_route=lambda *args:{'status':'no_route','reason':'Маршрут не найден'})
    assert job['results']['s:unknown']['legs'][0]['status']=='coordinates_unknown'
    assert job['results']['s:unreachable']['status']=='violates'


def test_stale_and_foreign_jobs(conn):
    trips.save_schedule(conn,schedule(),'one');job=trips.start_job(conn,[row()],'one')
    with pytest.raises(KeyError):trips.status_job(conn,job['id'],'two')
    data=schedule();data['legs'][0]['departure']='09:30';trips.save_schedule(conn,data,'one')
    assert trips.status_job(conn,job['id'],'one')['status']=='stale'
    assert trips.current_summary(conn,'one')['status']=='not_calculated'


def test_next_week_respects_timezone():
    assert str(trips.next_monday(datetime(2026,11,15,23,0,tzinfo=trips.TZ)))=='2026-11-16'


def test_request_budget_survives_memory_reset(conn,monkeypatch):
    monkeypatch.setenv('FLATS_TRIP_SESSION_BUDGET','1')
    assert trips.reserve_request('one',conn)
    trips._USAGE.clear()
    assert not trips.reserve_request('one',conn)
    assert trips.reserve_request('two',conn)


def test_global_budget_is_shared_by_visitors(conn,monkeypatch):
    monkeypatch.setenv('FLATS_TRIP_GLOBAL_BUDGET','1')
    assert trips.reserve_request('one',conn)
    assert not trips.reserve_request('two',conn)


def test_json_types_at_boundary_do_not_raise_type_error():
    data=schedule();data['legs'][0]['from']=[]
    with pytest.raises(ValueError):trips.validate_schedule(data)


def test_api_contract_ownership_start_step_cancel(conn,tmp_path):
    from flask import Flask,g
    from flats.web.trips import bp
    import flats.web
    from unittest.mock import patch
    app=Flask(__name__);app.register_blueprint(bp)
    app.before_request(lambda:setattr(g,'visitor_id','visitor'))
    with patch.object(flats.web,'get_conn',return_value=conn), patch('flats.web.trips.get_conn',return_value=conn):
        client=app.test_client()
        assert client.post('/api/trips/schedule',json={'bad':1}).status_code==400
        assert client.post('/api/trips/schedule',json=schedule()).json['version']==1
        from flats import repo_offers
        conn.execute("INSERT INTO import_runs(id,started_at,status) VALUES (1,'test','ok')")
        repo_offers.upsert_offer(conn,dict(source='s',source_id='a',price=30000,lat=43.2,lon=131.2),1)
        conn.commit()
        job=client.post('/api/trips/start').json
        assert job['total']==4 and job['status']=='running'
        assert client.get('/api/trips/'+job['id']).status_code==200
        assert client.post('/api/trips/'+job['id']+'/cancel').json['status']=='cancelled'
        assert client.get('/api/trips/missing').status_code==404


@pytest.fixture
def trip_browser(conn, tmp_path, monkeypatch):
    from flats import repo_user
    from flats.importer import run_import
    from flats.web import create_app

    monkeypatch.setenv('FLATS_PUBLIC_DEMO', '1')
    monkeypatch.setenv('FLATS_SECRET_KEY', 'test-trip-browser-decisions')
    run_import(conn, [dict(source='s', source_id=ident, group_id=group, price=30000,
                          lat=lat, lon=131.2, deposit_raw='Без залога',
                          commission_raw='Без комиссии', prepayment_raw='за 1 месяц')
                      for ident, group, lat in [('first', 'shared', 43.2),
                                                ('second', 'shared', 43.3), ('kept', None, 43.4)]])
    client = create_app(tmp_path/'trips.db').test_client()
    client.get('/')
    with client.session_transaction() as session:
        headers = {'X-CSRF-Token': session['csrf_token']}
        visitor = session['visitor_id']
    trips.save_schedule(conn, schedule(), visitor)
    for ident in ('first', 'second', 'kept'):
        repo_user.decide(conn, 's', ident, 'skip')
        conn.execute("INSERT INTO visitor_decisions(visitor_id,source,source_id,action,created_at)"
                     " VALUES (?, 's', ?, 'skip', '2026-11-14')", (visitor, ident))
    conn.commit()
    return client, headers, visitor


@pytest.mark.parametrize('decisions, expected', [
    (None, {'first', 'second', 'kept'}),
    ([['s', 'first', 'skip']], {'kept'}),
    ([['s', 'first', 'skip'], ['s', 'second', 'save']], {'second', 'kept'}),
    ([['s', 'kept', 'skip']], {'first', 'second'}),
])
def test_start_applies_browser_decisions_before_spending_route_budget(conn, trip_browser, decisions, expected):
    client, headers, visitor = trip_browser
    before = {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
              for table in ('decisions', 'visitor_decisions')}
    body = {} if decisions is None else {'json': {'version': 1, 'decisions': decisions}}
    if decisions is None:
        assert client.post('/api/trips/start').status_code == 403
        assert client.post('/api/trips/start', headers={'X-CSRF-Token': 'wrong'}).status_code == 403
        assert not trips._JOBS
    response = client.post('/api/trips/start', headers=headers, **body)
    assert response.status_code == 200
    job = response.get_json()
    assert set(job['results']) == {f's:{ident}' for ident in expected}
    assert job['total'] == 4 * len(expected)
    assert not trips.BUILDING.get()
    calls = []

    def counted(source, target, start_time):
        calls.append((source, target))
        return provider(source, target, start_time)

    assert finish(conn, job, owner=visitor, get_route=counted)['status'] == 'complete'
    home_lats = {point['lat'] for call in calls for point in call} & {43.2, 43.3, 43.4}
    lat_by_id = {'first': 43.2, 'second': 43.3, 'kept': 43.4}
    assert home_lats == {lat_by_id[ident] for ident in expected}
    assert {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
            for table in ('decisions', 'visitor_decisions')} == before


@pytest.mark.parametrize('body', [
    '[]', 'null', '{broken', '{"decisions": null}',
    '{"decisions": [["s", "first", "skip"], ["s", "first", "save"]]}',
])
def test_start_rejects_malformed_browser_state_before_creating_a_job(conn, trip_browser, body):
    client, headers, _ = trip_browser
    response = client.post('/api/trips/start', data=body, content_type='application/json', headers=headers)
    assert response.status_code == 400 and response.get_json()['error']
    assert not trips._JOBS and not trips.BUILDING.get()
    assert conn.execute('SELECT COUNT(*) FROM trip_request_usage').fetchone()[0] == 0


def test_group_chooses_eligible_offer_with_own_costs(conn):
    from flats import repo_offers,repo_user
    from flats.search import search
    conn.execute("INSERT INTO import_runs(id,started_at,status) VALUES (1,'test','ok')")
    for ident,price,lat in [('first',30000,43.2),('second',40000,43.3)]:
        repo_offers.upsert_offer(conn,dict(source='s',source_id=ident,group_id='shared',price=price,lat=lat,lon=131.2,
                                         deposit_raw='Без залога',commission_raw='Без комиссии',prepayment_raw='за 1 месяц'),1)
    conn.commit()
    trips.save_schedule(conn,schedule())
    base=search(conn,repo_user.get_profile(conn))
    candidates=trips.candidate_rows(conn,base)
    assert len(candidates)==2
    job=trips.start_job(conn,candidates)
    def get_route(source,target,start_time):
        if source['lat']==43.2 or target['lat']==43.2:return {'status':'no_route','reason':'Маршрут отсутствует'}
        return provider(source,target,start_time)
    final=finish(conn,job,owner='local',get_route=get_route)
    assert final['status']=='complete'
    result=trips.rank_rows(conn,base)
    assert len(result)==1
    assert result[0].offer.source_id=='second'
    assert result[0].costs.monthly_known==40000
    assert result[0].costs.move_in_known==40000


def test_provider_freshness_is_preserved_for_reused_routes(conn):
    trips.save_schedule(conn,schedule(),'one')
    job=trips.start_job(conn,[row('a'),row('b')],'one')
    final=finish(conn,job)
    a=final['results']['s:a']['legs'][0]
    b=final['results']['s:b']['legs'][0]
    assert a['provider']=='2ГИС'
    assert datetime.fromisoformat(a['calculated_at']).utcoffset().total_seconds()==36000
    assert a['calculated_at']==b['calculated_at']
    job=trips.start_job(conn,[row('unknown',lat=None),row('error')],'one')
    failed=finish(conn,job,get_route=lambda *args:{'status':'api_error','reason':'Нет доступа'})
    assert all('calculated_at' not in leg for result in failed['results'].values() for leg in result['legs'])


def test_estimate_uses_shared_tasks_without_spending_or_replacing_job(conn):
    trips.save_schedule(conn,schedule(),'one')
    old=trips.start_job(conn,[row('old')],'one')
    assert trips.reserve_request('one',conn)
    before=[tuple(r) for r in conn.execute('SELECT * FROM trip_request_usage ORDER BY visitor_id')]
    estimate=trips.estimate_job(conn,[row('a'),row('b',lat=43.3)],'one')
    assert estimate['total_tasks']==8
    assert estimate['unique_queries']==7  # constant a->b is reused between homes
    assert estimate['candidates']==2 and estimate['remaining_session']==99
    assert estimate['sufficient_budget']
    assert trips._CURRENT['one']==old['id']
    assert trips.status_job(conn,old['id'],'one')['status']=='running'
    assert [tuple(r) for r in conn.execute('SELECT * FROM trip_request_usage ORDER BY visitor_id')]==before
    job=trips.start_job(conn,[row('a'),row('b',lat=43.3)],'one')
    calls=[]
    def counted(*args):calls.append(args);return provider(*args)
    assert finish(conn,job,get_route=counted)['status']=='complete'
    assert len(calls)==estimate['unique_queries']


def test_estimate_reports_both_remaining_quotas_and_missing_coordinates(conn,monkeypatch):
    trips.save_schedule(conn,schedule(),'one')
    monkeypatch.setenv('FLATS_TRIP_SESSION_BUDGET','2')
    monkeypatch.setenv('FLATS_TRIP_GLOBAL_BUDGET','3')
    assert trips.reserve_request('one',conn)
    estimate=trips.estimate_job(conn,[row(),row('unknown',lat=None)],'one')
    assert estimate['remaining_session']==1 and estimate['remaining_global']==2
    assert not estimate['sufficient_budget']
    assert estimate['unresolved_tasks']==3  # constant a->b still has valid coordinates
    assert trips._CURRENT=={}


@pytest.fixture
def public_trips_app(tmp_path,monkeypatch):
    from flats import repo_offers
    from flats.web import create_app,get_conn
    monkeypatch.setenv('FLATS_PUBLIC_DEMO','1')
    monkeypatch.setenv('FLATS_SECRET_KEY','test-public-scope-secret')
    app=create_app(tmp_path/'public-trips.db');app.testing=True
    with app.app_context():
        c=get_conn()
        c.execute("INSERT INTO import_runs(id,started_at,status) VALUES (1,'test','ok')")
        for ident,price,group in [('saved',30000,'shared'),('alternate',32000,'shared'),('expensive',50000,'other'),('foreign',31000,'foreign')]:
            repo_offers.upsert_offer(c,dict(source='s',source_id=ident,group_id=group,price=price,lat=43.2,lon=131.2),1)
        c.commit()
    return app


def _public_client(app):
    client=app.test_client();client.get('/trips')
    with client.session_transaction() as state:
        headers={'X-CSRF-Token':state['csrf_token']}
    return client,headers


def test_saved_scope_uses_explicit_browser_ids_and_money_filters(public_trips_app):
    from flats import repo_user
    from flats.web import get_conn
    a,ah=_public_client(public_trips_app);b,bh=_public_client(public_trips_app)
    # Legacy persisted choices are not a browser snapshot or a fallback.
    with public_trips_app.app_context():
        conn=get_conn();repo_user.decide(conn,'s','foreign','save')
        before=[tuple(r) for r in conn.execute('SELECT * FROM decisions')]
    decisions=[['s','saved','save'],['s','expensive','save']]
    a.post('/',data={'apply':'1','monthly_max':'35000'},headers=ah)
    a.post('/api/trips/schedule',json=schedule(),headers=ah)
    b.post('/api/trips/schedule',json=schedule(),headers=bh)
    estimate=a.post('/api/trips/estimate',json={'scope':'saved','decisions':decisions},headers=ah)
    assert estimate.status_code==200
    assert estimate.json['offers']==1 and estimate.json['candidates']==1
    assert b.post('/api/trips/estimate',json={'scope':'saved','decisions':[]},headers=bh).status_code==400
    assert a.post('/api/trips/estimate',json={'scope':'saved'},headers=ah).status_code==400
    job=a.post('/api/trips/start',json={'scope':'saved','version':estimate.json['version'],'decisions':decisions},headers=ah)
    assert job.status_code==200
    assert set(job.json['results'])=={'s:saved'}
    assert a.get('/api/trips/estimate?scope=invalid').status_code==400
    assert a.post('/api/trips/start',json={'scope':'saved','version':999,'decisions':decisions},headers=ah).status_code==400
    assert a.post('/api/trips/start',json={'scope':[]},headers=ah).status_code==400
    with public_trips_app.app_context():
        assert [tuple(r) for r in get_conn().execute('SELECT * FROM decisions')]==before
        assert get_conn().execute('SELECT COUNT(*) FROM visitor_decisions').fetchone()[0]==0


def test_saved_scope_rejects_empty_invalid_and_more_than_five_flats(public_trips_app):
    from flats import repo_offers
    from flats.web import get_conn
    c,ch=_public_client(public_trips_app)
    c.post('/api/trips/schedule',json=schedule(),headers=ch)
    assert c.get('/api/trips/estimate?scope=saved').status_code==400
    with public_trips_app.app_context():
        conn=get_conn()
        for i in range(6):repo_offers.upsert_offer(conn,dict(source='s',source_id=f'saved{i}',price=30000,lat=43.2,lon=131.2),1)
        conn.commit()
    decisions=[['s',f'saved{i}','save'] for i in range(6)]
    assert c.post('/api/trips/estimate',json={'scope':'saved','decisions':decisions},headers=ch).status_code==400
    assert c.post('/api/trips/start',json={'scope':'saved','decisions':decisions},headers=ch).status_code==400
    assert c.get('/api/trips/estimate?scope=all').status_code==200
    for invalid in (None,[['s','saved0','like']],[['s','saved0','save'],['s','saved0','save']]):
        assert c.post('/api/trips/estimate',json={'scope':'saved','decisions':invalid},headers=ch).status_code==400
    assert c.post('/api/trips/estimate',json={'scope':'saved','decisions':[['s','unknown','save']]},headers=ch).status_code==400


def test_saved_scope_does_not_charge_skipped_siblings(public_trips_app):
    c,ch=_public_client(public_trips_app)
    c.post('/api/trips/schedule',json=schedule(),headers=ch)
    decisions=[['s','saved','skip'],['s','alternate','save'],['s','foreign','skip']]
    result=c.post('/api/trips/start',json={'scope':'saved','decisions':decisions},headers=ch)
    assert result.status_code==200
    assert set(result.json['results'])=={'s:alternate'}


def test_all_scope_uses_catalog_filters_and_reports_budget(conn,public_trips_app,monkeypatch):
    from flats import repo_offers,repo_user
    from flats.web import get_conn
    monkeypatch.setenv('FLATS_TRIP_SESSION_BUDGET','1')
    client,headers=_public_client(public_trips_app)
    with public_trips_app.app_context():
        dbconn=get_conn()
        dbconn.execute('UPDATE offers SET rooms=2')
        for i in range(8):
            repo_offers.upsert_offer(dbconn,dict(source='s',source_id=f'flat{i}',price=30000,
                rooms=1,lat=43.2+i/100,lon=131.2,prepayment_raw='за 1 месяц',
                deposit_raw='Без залога',commission_raw='Без комиссии'),1)
        repo_offers.upsert_offer(dbconn,dict(source='s',source_id='costly',price=30000,rooms=1,
            lat=43.5,lon=131.2,prepayment_raw='за 1 месяц',deposit_raw='50 000 ₽',
            commission_raw='Без комиссии'),1)
        repo_user.decide(dbconn,'s','flat0','skip')
        dbconn.commit()
    client.post('/',data={'apply':'1','monthly_max':'35000','move_in_max':'50000','rooms':'1'},headers=headers)
    client.post('/api/trips/schedule',json=schedule(),headers=headers)
    decisions=[['s','flat7','skip']]
    estimate=client.post('/api/trips/estimate',json={'decisions':decisions},headers=headers)
    assert estimate.status_code==200
    assert estimate.json['scope']=='all' and estimate.json['candidates']==7
    assert not estimate.json['sufficient_budget']
    with public_trips_app.app_context():
        assert get_conn().execute('SELECT COUNT(*) FROM trip_request_usage').fetchone()[0]==0
    response=client.post('/api/trips/start',json={'decisions':decisions,'version':estimate.json['version']},headers=headers)
    assert response.status_code==200
    assert set(response.json['results'])=={f's:flat{i}' for i in range(7)}
    with client.session_transaction() as state:
        owner=state['visitor_id']
    with public_trips_app.app_context():
        final=finish(get_conn(),response.json,owner=owner)
    assert final['status']=='budget'
    assert all(result['status']=='incomplete' and result['weekly_seconds'] is None
               for result in final['results'].values())


def test_empty_all_scope_estimate_and_start_do_not_create_job(conn,trip_browser):
    client,headers,visitor=trip_browser
    decisions=[['s','first','skip'],['s','second','skip'],['s','kept','skip']]
    estimate=client.post('/api/trips/estimate',json={'decisions':decisions},headers=headers)
    assert estimate.status_code==200 and estimate.json['candidates']==0
    response=client.post('/api/trips/start',json={'decisions':decisions},headers=headers)
    assert response.status_code==400 and 'условия аренды' in response.json['error']
    assert visitor not in trips._CURRENT and not trips._JOBS
    assert conn.execute('SELECT COUNT(*) FROM trip_request_usage').fetchone()[0]==0
