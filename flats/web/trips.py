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


@bp.post('/start')
def start():
    from flats.search import search
    token=trips.BUILDING.set(True)
    try:
        conn=get_conn()
        rows=trips.candidate_rows(conn,search(conn,repo_user.get_profile(conn)))
        return jsonify(trips.start_job(conn,rows))
    except ValueError as exc:
        return jsonify(error=str(exc)),400
    finally:
        trips.BUILDING.reset(token)


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
