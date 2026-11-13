from dataclasses import replace

from flask import Blueprint, jsonify, redirect, request

from flats import repo_user
from flats.web import get_conn

bp = Blueprint("actions", __name__, url_prefix="/api")


def _input():
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form


def _ok(body):
    if request.is_json:
        return jsonify(body)
    referrer = request.referrer
    target = referrer if referrer and referrer.startswith(request.host_url) else "/"
    return redirect(target, code=303)


def _error(status, json_message, text_message):
    if request.is_json:
        return jsonify({"error": json_message}), status
    return text_message, status, {"Content-Type": "text/plain; charset=utf-8"}


@bp.post("/decide")
def decide():
    data = _input()
    source, source_id, action = (data.get(k) for k in ("source", "source_id", "action"))
    if not all(isinstance(v, str) and v for v in (source, source_id, action)):
        return _error(400, "missing field", "Не хватает данных")
    if action not in ("save", "skip"):
        return _error(400, "bad action", "Неверное действие")
    conn = get_conn()
    found = conn.execute(
        "SELECT 1 FROM offers WHERE source = ? AND source_id = ?", (source, source_id)
    ).fetchone()
    if found is None:
        return _error(404, "offer not found", "Объявление не найдено")
    return _ok({"id": repo_user.decide(conn, source, source_id, action)})


@bp.post("/undo")
def undo():
    return _ok({"undone": repo_user.undo_last(get_conn())})


@bp.post("/sample")
def sample():
    url = _input().get("photo_url")
    conn = get_conn()
    found = isinstance(url, str) and url and conn.execute(
        "SELECT 1 FROM offers, json_each(offers.photos_json) WHERE json_each.value = ?",
        (url,),
    ).fetchone()
    if not found:
        return _error(400, "unknown photo", "Фото не найдено")
    repo_user.save_profile(conn, replace(repo_user.get_profile(conn), sample_photo_url=url))
    return _ok({"sample_photo_url": url})


@bp.post("/sample/clear")
def sample_clear():
    conn = get_conn()
    repo_user.save_profile(conn, replace(repo_user.get_profile(conn), sample_photo_url=None))
    return _ok({"sample_photo_url": None})
