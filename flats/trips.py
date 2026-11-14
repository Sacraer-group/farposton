"""Versioned weekly schedules and bounded in-memory commute calculations."""
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
import re
import secrets
import threading
import time
from zoneinfo import ZoneInfo

from flats import routing

TZ = ZoneInfo('Asia/Vladivostok')
BUILDING = ContextVar('trips_building', default=False)
_LOCK = threading.RLock()
_JOBS = {}
_CURRENT = {}
_USAGE = {}
TTL = 3600
MAX_PLACES = 100
MAX_LEGS = 200


def init_trips_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS trip_schedules (
        visitor_id TEXT PRIMARY KEY, version INTEGER NOT NULL, schedule_json TEXT NOT NULL,
        updated_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS trip_request_usage (
        day TEXT NOT NULL, visitor_id TEXT NOT NULL, requests INTEGER NOT NULL,
        PRIMARY KEY(day,visitor_id))''')


def owner_id(visitor_id=None):
    if visitor_id:
        return visitor_id
    from flask import g, has_request_context
    return getattr(g, 'visitor_id', 'local') if has_request_context() else 'local'


def validate_schedule(data):
    if not isinstance(data, dict) or not isinstance(data.get('places'), list) or not isinstance(data.get('legs'), list):
        raise ValueError('Нужны места и участки поездок')
    if len(data['places']) > MAX_PLACES or len(data['legs']) > MAX_LEGS:
        raise ValueError('Лимит: 100 мест и 200 участков расписания')
    places, seen = [], {'home'}
    for p in data['places']:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not re.fullmatch(r'[\w:-]{1,100}', p['id']) or p['id'] in seen:
            raise ValueError('Неверный или повторный идентификатор места')
        if not isinstance(p.get('name'), str) or not 1 <= len(p['name'].strip()) <= 200 or p.get('confirmed') is not True:
            raise ValueError('Подтвердите точное место на карте')
        places.append({'id': p['id'], 'name': p['name'].strip(), **routing.point(p), 'confirmed': True})
        seen.add(p['id'])
    legs, ids = [], set()
    for i, leg in enumerate(data['legs']):
        if not isinstance(leg, dict) or not isinstance(leg.get('from'),str) or not isinstance(leg.get('to'),str) or leg['from'] not in seen or leg['to'] not in seen or leg['from'] == leg['to']:
            raise ValueError('Укажите разные известные точки участка')
        days = leg.get('days')
        if not isinstance(days, list) or not days or any(type(d) is not int or d not in range(7) for d in days) or len(set(days)) != len(days):
            raise ValueError('Выберите дни недели: 0–6')
        departure = leg.get('departure')
        if not isinstance(departure, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', departure):
            raise ValueError('Время отправления должно быть ЧЧ:ММ')
        limits = {k: leg.get(k) for k in ('max_minutes', 'max_transfers')}
        if any(v is not None and (type(v) is not int or not 0 <= v <= (1440 if k == 'max_minutes' else 20)) for k,v in limits.items()):
            raise ValueError('Неверное ограничение длительности или пересадок')
        ident = leg.get('id', f'leg{i}')
        if not isinstance(ident, str) or not 1 <= len(ident) <= 100 or ident in ids:
            raise ValueError('Участки должны иметь разные идентификаторы')
        ids.add(ident)
        legs.append({'id': ident, 'from': leg['from'], 'to': leg['to'], 'days': sorted(days), 'departure': departure, **limits})
    for day in range(7):
        daily = sorted((x for x in legs if day in x['days']), key=lambda x:x['departure'])
        for previous, current in zip(daily, daily[1:]):
            if previous['departure'] == current['departure']:
                raise ValueError('Два участка одного дня не могут отправляться одновременно')
    return {'places': places, 'legs': legs}


def get_schedule(conn, visitor_id=None):
    row = conn.execute('SELECT version,schedule_json FROM trip_schedules WHERE visitor_id=?', (owner_id(visitor_id),)).fetchone()
    return {'version': row['version'], 'schedule': json.loads(row['schedule_json'])} if row else {'version':0,'schedule':{'places':[],'legs':[]}}


def save_schedule(conn, data, visitor_id=None):
    data = validate_schedule(data)
    owner = owner_id(visitor_id)
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True)
    with conn:
        old = get_schedule(conn, owner)
        if old['schedule'] == data:
            return old
        version = old['version'] + 1
        conn.execute('INSERT INTO trip_schedules VALUES (?,?,?,?) ON CONFLICT(visitor_id) DO UPDATE SET version=excluded.version,schedule_json=excluded.schedule_json,updated_at=excluded.updated_at',
                     (owner, version, encoded, datetime.now(TZ).isoformat()))
    with _LOCK:
        ident = _CURRENT.pop(owner, None)
        if ident in _JOBS:
            _JOBS[ident]['cancelled'] = True
    return {'version':version,'schedule':data}


def next_monday(now=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return now.date() + timedelta(days=7-now.weekday())


def _cleanup():
    threshold = time.time()-TTL
    for ident in [i for i,j in _JOBS.items() if j['created'] < threshold]:
        job = _JOBS.pop(ident)
        if _CURRENT.get(job['owner']) == ident:
            _CURRENT.pop(job['owner'], None)
    today = datetime.now(TZ).date().isoformat()
    for k in list(_USAGE):
        if k[0] != today:
            del _USAGE[k]


def reserve_request(owner, conn=None):
    today = datetime.now(TZ).date().isoformat()
    limits = [(owner, int(os.environ.get('FLATS_TRIP_SESSION_BUDGET','100'))),
              ('*global*', int(os.environ.get('FLATS_TRIP_GLOBAL_BUDGET','1000')))]
    with _LOCK:
        if conn is None:
            _cleanup()
            if any(_USAGE.get((today,key),0)>=limit for key,limit in limits):return False
            for key,_limit in limits:_USAGE[(today,key)]=_USAGE.get((today,key),0)+1
            return True
        own_transaction = not conn.in_transaction
        if own_transaction:conn.execute('BEGIN IMMEDIATE')
        try:
            conn.execute('DELETE FROM trip_request_usage WHERE day <> ?', (today,))
            for key,limit in limits:
                row=conn.execute('SELECT requests FROM trip_request_usage WHERE day=? AND visitor_id=?',(today,key)).fetchone()
                if row and row['requests']>=limit or not row and limit<=0:
                    if own_transaction:conn.commit()
                    return False
            for key,_limit in limits:
                conn.execute('INSERT INTO trip_request_usage VALUES (?,?,1) ON CONFLICT(day,visitor_id) DO UPDATE SET requests=requests+1',(today,key))
            if own_transaction:conn.commit()
            return True
        except Exception:
            if own_transaction:conn.rollback()
            raise



def prepare_job(conn, rows, visitor_id=None, now=None):
    owner = owner_id(visitor_id)
    stored = get_schedule(conn, owner)
    if not stored['schedule']['legs']:
        raise ValueError('Добавьте хотя бы один участок поездки')
    occurrences=sum(len(leg['days']) for leg in stored['schedule']['legs'])*len(rows)
    if occurrences>10000:
        raise ValueError('Не больше 10 000 участков расчёта за запуск: сузьте выбор квартир или график')
    places = {p['id']: p for p in stored['schedule']['places']}
    monday = next_monday(now)
    tasks, results = [], {}
    from flats.repo_houses import house_links_for_offers
    links = house_links_for_offers(conn, [(r.offer.source,r.offer.source_id) for r in rows])
    for row in rows:
        offer = row.offer
        key = f'{offer.source}:{offer.source_id}'
        link = links.get((offer.source,offer.source_id),{})
        coordinates = (link.get('summary') or {}).get('coordinates') if link.get('status') in ('exact','manual') else None
        home = coordinates or {'lat':offer.lat,'lon':offer.lon}
        result = {'status':'incomplete','weekly_seconds':None,'legs':[],
                  'home_precision':'Адресная точка дома; точность до подъезда не подтверждена' if coordinates else 'Координаты объявления; точность требует проверки'}
        results[key] = result
        for day in range(7):
            daily = sorted((l for l in stored['schedule']['legs'] if day in l['days']), key=lambda x:x['departure'])
            for leg in daily:
                date = monday + timedelta(days=day)
                departure = datetime.fromisoformat(f"{date.isoformat()}T{leg['departure']}").replace(tzinfo=TZ)
                item = {'id':leg['id'],'from':leg['from'],'to':leg['to'],'from_name':'Дом' if leg['from']=='home' else places[leg['from']]['name'],
                        'to_name':'Дом' if leg['to']=='home' else places[leg['to']]['name'], 'date':str(date),'departure':leg['departure'],
                        'start_time':int(departure.timestamp()),'status':'not_calculated','duration_seconds':None,'transfers':None,'pedestrian':None,'walkway':None,
                        'max_minutes':leg['max_minutes'],'max_transfers':leg['max_transfers']}
                result['legs'].append(item)
                source = home if leg['from']=='home' else places[leg['from']]
                target = home if leg['to']=='home' else places[leg['to']]
                try:
                    source,target = routing.point(source),routing.point(target)
                except ValueError:
                    item.update(status='coordinates_unknown',reason='Нет подтверждённых координат квартиры')
                    continue
                tasks.append((key,len(result['legs'])-1,source,target))
    return {'tasks':tasks,'results':results,'monday':monday,
            'version':stored['version'],'occurrences':occurrences}


def task_identity(task, results):
    key,index,source,target=task
    return (source['lat'],source['lon'],target['lat'],target['lon'],results[key]['legs'][index]['start_time'])


def remaining_budget(conn, visitor_id=None):
    owner=owner_id(visitor_id)
    today=datetime.now(TZ).date().isoformat()
    counts={r['visitor_id']:r['requests'] for r in conn.execute(
        'SELECT visitor_id,requests FROM trip_request_usage WHERE day=? AND visitor_id IN (?,?)',
        (today,owner,'*global*'))}
    return {'remaining_session':max(0,int(os.environ.get('FLATS_TRIP_SESSION_BUDGET','100'))-counts.get(owner,0)),
            'remaining_global':max(0,int(os.environ.get('FLATS_TRIP_GLOBAL_BUDGET','1000'))-counts.get('*global*',0))}


def estimate_job(conn, rows, visitor_id=None, now=None):
    prepared=prepare_job(conn,rows,visitor_id,now)
    queries=len({task_identity(t,prepared['results']) for t in prepared['tasks']})
    remaining=remaining_budget(conn,visitor_id)
    return {'version':prepared['version'],'candidates':len({r.card.key for r in rows}),
            'offers':len(rows),'total_tasks':prepared['occurrences'],'unique_queries':queries,
            'unresolved_tasks':prepared['occurrences']-len(prepared['tasks']),**remaining,
            'sufficient_budget':queries<=min(remaining.values())}


def start_job(conn, rows, visitor_id=None, now=None):
    owner=owner_id(visitor_id)
    prepared=prepare_job(conn,rows,owner,now)
    tasks,results,monday,occurrences=(prepared[k] for k in ('tasks','results','monday','occurrences'))
    with _LOCK:
        _cleanup()
        if len(_JOBS)>=100 or sum(sum(len(r['legs']) for r in j['results'].values()) for j in _JOBS.values())+occurrences>30000:
            raise ValueError('Сервер занят, повторите позже')
        previous = _CURRENT.get(owner)
        if previous in _JOBS:
            _JOBS[previous]['cancelled'] = True
        ident = secrets.token_urlsafe(18)
        _JOBS[ident] = {'id':ident,'owner':owner,'version':prepared['version'],'created':time.time(),
                       'status':'running' if tasks else 'complete','cancelled':False,'done':0,'total':len(tasks),'tasks':tasks,'results':results,
                       'cache':{},'lock':threading.RLock(),'week_start':str(monday),'week_end':str(monday+timedelta(days=6))}
        _CURRENT[owner]=ident
        _summarize(_JOBS[ident])
    return status_job(conn,ident,owner)


def _get_job(conn, ident, owner):
    with _LOCK:
        _cleanup()
        job = _JOBS.get(ident)
        if not job or job['owner'] != owner:
            raise KeyError('Расчёт не найден или срок его хранения истёк')
        if job['version'] != get_schedule(conn,owner)['version']:
            job['cancelled']=True
            job['status']='stale'
        return job


def _summarize(job):
    for result in job['results'].values():
        legs = result['legs']
        violations = any(l['status'] in ('violates','no_route','schedule_conflict') for l in legs)
        for previous,current in zip(legs,legs[1:]):
            if previous['date']==current['date'] and previous['to']==current['from'] and previous['duration_seconds'] is not None and previous['start_time']+previous['duration_seconds']>current['start_time']:
                current.update(status='schedule_conflict', reason='Не успеваете к отправлению следующего участка')
                violations=True
        complete = all(l['status']=='ok' for l in legs)
        result['status'] = 'violates' if violations else 'acceptable' if complete else 'incomplete'
        result['weekly_seconds'] = sum(l['duration_seconds'] for l in legs) if complete else None


def _public(job):
    with job['lock']:
        return deepcopy({k:job[k] for k in ('id','version','status','done','total','results','week_start','week_end')})


def status_job(conn, ident, visitor_id=None):
    job = _get_job(conn,ident,owner_id(visitor_id))
    if job['cancelled'] and job['status']=='running':
        job['status']='cancelled'
    return _public(job)


def cancel_job(conn, ident, visitor_id=None):
    job = _get_job(conn,ident,owner_id(visitor_id))
    job['cancelled']=True
    job['status']='cancelled'
    return _public(job)


def step_job(conn, ident, visitor_id=None, provider=None):
    owner = owner_id(visitor_id)
    job = _get_job(conn,ident,owner)
    provider = provider or routing.route
    if not job['lock'].acquire(blocking=False):
        return _public(job)
    try:
        calls=0
        while job['done']<job['total'] and job['status']=='running' and not job['cancelled']:
            key,index,source,target=job['tasks'][job['done']]
            item=job['results'][key]['legs'][index]
            identity=task_identity(job['tasks'][job['done']],job['results'])
            if identity not in job['cache']:
                if calls>=3:
                    break
                if not reserve_request(owner,conn):
                    job['status']='budget'
                    for result in job['results'].values():
                        for leg in result['legs']:
                            if leg['status']=='not_calculated':leg.update(status='budget',reason='Достигнут лимит запросов; расчёт неполный')
                    break
                calls+=1
                response=dict(provider(source,target,item['start_time']))
                if response['status'] in ('ok','no_route'):
                    response.update(provider='2ГИС',calculated_at=datetime.now(TZ).isoformat())
                job['cache'][identity]=response
            response=job['cache'][identity]
            if response['status']=='ok':
                variants=response['alternatives']
                valid=[v for v in variants if (item['max_minutes'] is None or v['duration_seconds']<=item['max_minutes']*60)
                       and (item['max_transfers'] is None or v['transfers']<=item['max_transfers'])]
                chosen=min(valid or variants,key=lambda v:(v['duration_seconds'],v['transfers']))
                item.update(chosen,status='ok' if valid else 'violates',
                            provider=response['provider'],calculated_at=response['calculated_at'])
                if not valid:item['reason']='Превышены ограничения времени или пересадок'
            else:
                item.update(response)
            job['done']+=1
        if job['cancelled'] and job['status']=='running':job['status']='cancelled'
        if job['done']==job['total'] and job['status']=='running':job['status']='complete'
        _summarize(job)
        return _public(job)
    finally:
        job['lock'].release()


def current_summary(conn, visitor_id=None):
    owner=owner_id(visitor_id)
    stored=get_schedule(conn,owner)
    with _LOCK:
        _cleanup()
        job=_JOBS.get(_CURRENT.get(owner))
    if not job or job['version']!=stored['version'] or job['week_start']!=str(next_monday()):
        return {'active':bool(stored['schedule']['legs']),'version':stored['version'],'status':'not_calculated','results':{}}
    return {'active':True,**_public(job)}


def trip_context(conn, offer_keys, visitor_id=None):
    summary=current_summary(conn,visitor_id)
    return {key:summary['results'].get(f'{key[0]}:{key[1]}',{'status':'not_calculated','weekly_seconds':None,'legs':[]}) for key in offer_keys}


def candidate_rows(conn, rows):
    """Calculate every eligible offer, retaining independent costs within a flat."""
    from flats import repo_user
    from flats.costs import compute_costs
    from flats.eligibility import check
    profile=repo_user.get_profile(conn)
    expanded=[]
    for row in rows:
        for offer in row.card.offers:
            costs=compute_costs(offer)
            verdict=check(offer,costs,profile)
            if verdict.status!='reject':
                expanded.append(replace(row,offer=offer,costs=costs,verdict=verdict))
    return expanded


def rank_rows(conn, rows, visitor_id=None):
    if BUILDING.get():return rows
    summary=current_summary(conn,visitor_id)
    if not summary['active']:return rows
    def context(row):return summary['results'].get(f'{row.offer.source}:{row.offer.source_id}',{})
    def order(row):
        result=context(row)
        return (result.get('status')!='acceptable',result.get('weekly_seconds') if result.get('weekly_seconds') is not None else float('inf'),
                row.costs.monthly_known is None,row.costs.monthly_known or 0,row.card.key)
    selected=[]
    # Use all source offers when known, but return one card and its selected offer's costs.
    candidates=candidate_rows(conn,rows) if rows and hasattr(rows[0].card,'offers') else rows
    by_card={}
    for candidate in candidates:
        if context(candidate).get('status')!='violates':
            by_card.setdefault(candidate.card.key,[]).append(candidate)
    for row in rows:
        variants=by_card.get(row.card.key,[])
        if variants:selected.append(min(variants,key=order))
    if any(r.similar for r in selected):
        return sorted(selected,key=lambda r:context(r).get('status')!='acceptable')
    return sorted(selected,key=order)
