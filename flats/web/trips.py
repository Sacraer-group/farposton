from flask import Blueprint, g, jsonify, request

from flats import repo_user, routing, trips
from flats.web import get_conn

bp=Blueprint('trips',__name__,url_prefix='/api/trips')


@bp.get('/schedule')
def schedule_get():
    return jsonify(trips.get_schedule(get_conn()))


@bp.post('/schedule')
def schedule_save():
    try:
        return jsonify(trips.save_schedule(get_conn(),request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify(error=str(exc)),400


@bp.get('/places')
def places():
    owner=trips.owner_id()
    if not trips.reserve_request(owner,get_conn()):
        return jsonify(error='Достигнут лимит запросов 2ГИС'),429
    try:
        return jsonify(places=routing.places(request.args.get('q','')))
    except ValueError as exc:
        return jsonify(error=str(exc)),400
    except RuntimeError as exc:
        return jsonify(error=str(exc)),503


def _candidates(conn, scope, decisions):
    from flats.search import search
    if scope not in ('all','saved'):
        raise ValueError('Выберите избранные или все подходящие квартиры')
    token=trips.BUILDING.set(True)
    try:
        rows=trips.candidate_rows(conn,search(conn,repo_user.get_profile(conn),decisions=decisions))
    finally:
        trips.BUILDING.reset(token)
    rows=[r for r in rows if decisions.get((r.offer.source,r.offer.source_id))!='skip']
    if scope=='saved':
        rows=[r for r in rows if decisions.get((r.offer.source,r.offer.source_id))=='save']
        count=len({r.card.key for r in rows})
        if not count:
            raise ValueError('Нет избранных квартир, подходящих под условия. Сохраните 3–5 вариантов или измените фильтры.')
        if count>5:
            raise ValueError('В избранном больше пяти подходящих квартир. Оставьте до пяти вариантов для расчёта или выберите весь каталог.')
    return rows


def _input():
    data=request.get_json(silent=True) if request.get_data() else {}
    if not isinstance(data,dict):
        raise ValueError('Ожидается JSON-объект')
    decisions=repo_user.validate_browser_decisions(data.get('decisions',[]))
    scope=data.get('scope',request.args.get('scope','all'))
    return data,scope,decisions


@bp.route('/estimate',methods=['GET','POST'])
def estimate():
    try:
        _data,scope,decisions=_input()
        conn=get_conn()
        return jsonify(scope=scope,**trips.estimate_job(conn,_candidates(conn,scope,decisions)))
    except ValueError as exc:
        return jsonify(error=str(exc)),400


@bp.post('/start')
def start():
    try:
        data,scope,decisions=_input()
        conn=get_conn()
        if 'version' in data and data['version']!=trips.get_schedule(conn)['version']:
            raise ValueError('График изменился. Повторите оценку расчёта.')
        rows=_candidates(conn,scope,decisions)
        if not rows:
            raise ValueError('Нет квартир, подходящих под условия аренды. Измените условия и повторите поиск.')
        return jsonify(trips.start_job(conn,rows))
    except ValueError as exc:
        return jsonify(error=str(exc)),400


def _action(ident, action):
    try:
        return jsonify(action(get_conn(),ident))
    except KeyError:
        return jsonify(error='Расчёт не найден или срок его хранения истёк'),404


@bp.get('/<ident>')
def status(ident):return _action(ident,trips.status_job)


@bp.post('/<ident>/step')
def step(ident):return _action(ident,trips.step_job)


@bp.post('/<ident>/cancel')
def cancel(ident):return _action(ident,trips.cancel_job)
