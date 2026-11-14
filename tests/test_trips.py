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
        job=client.post('/api/trips/start').json
        assert job['total']==0 and job['status']=='complete'
        assert client.get('/api/trips/'+job['id']).status_code==200
        assert client.post('/api/trips/'+job['id']+'/cancel').json['status']=='cancelled'
        assert client.get('/api/trips/missing').status_code==404


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


def test_saved_scope_obeys_current_owner_exact_offer_and_money_filters(public_trips_app):
    a,ah=_public_client(public_trips_app);b,bh=_public_client(public_trips_app)
    for ident in ('saved','expensive'):
        assert a.post('/api/decide',json={'source':'s','source_id':ident,'action':'save'},headers=ah).status_code==200
    b.post('/api/decide',json={'source':'s','source_id':'foreign','action':'save'},headers=bh)
    a.post('/',data={'apply':'1','monthly_max':'35000'},headers=ah)
    a.post('/api/trips/schedule',json=schedule(),headers=ah)
    estimate=a.get('/api/trips/estimate?scope=saved')
    assert estimate.status_code==200
    assert estimate.json['offers']==1 and estimate.json['candidates']==1
    job=a.post('/api/trips/start',json={'scope':'saved','version':estimate.json['version']},headers=ah)
    assert job.status_code==200
    assert set(job.json['results'])=={'s:saved'}
    assert a.get('/api/trips/estimate?scope=invalid').status_code==400
    assert a.post('/api/trips/start',json={'scope':'saved','version':999},headers=ah).status_code==400
    assert a.post('/api/trips/start',json={'scope':[]},headers=ah).status_code==400


def test_saved_scope_rejects_empty_and_more_than_five_flats(public_trips_app):
    from flats import repo_offers
    from flats.web import get_conn
    c,ch=_public_client(public_trips_app)
    c.post('/api/trips/schedule',json=schedule(),headers=ch)
    assert c.get('/api/trips/estimate?scope=saved').status_code==400
    with public_trips_app.app_context():
        conn=get_conn()
        for i in range(6):repo_offers.upsert_offer(conn,dict(source='s',source_id=f'saved{i}',price=30000,lat=43.2,lon=131.2),1)
        conn.commit()
    for i in range(6):c.post('/api/decide',json={'source':'s','source_id':f'saved{i}','action':'save'},headers=ch)
    assert c.get('/api/trips/estimate?scope=saved').status_code==400
    assert c.post('/api/trips/start',json={'scope':'saved'},headers=ch).status_code==400
    assert c.get('/api/trips/estimate?scope=all').status_code==200
