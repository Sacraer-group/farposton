import json
from dataclasses import replace

from flask import Blueprint, current_app, jsonify, redirect, request

from flats import advisor, repo_user
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
@bp.post("/undo")
def browser_decisions():
    return _error(410, "decisions are stored in this browser; reload the page",
                  "Избранное теперь хранится в браузере. Обновите страницу.")


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


@bp.post("/advisor")
def advise():
    if not request.is_json:
        return jsonify({"error": "Ожидается JSON"}), 400
    limit = 16 * 1024
    if request.content_length is not None and request.content_length > limit:
        return jsonify({"error": "Запрос слишком большой"}), 413
    raw = request.stream.read(limit + 1)
    if len(raw) > limit:
        return jsonify({"error": "Запрос слишком большой"}), 413
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return jsonify({"error": "Неверный JSON"}), 400
    try:
        from flats import trips
        conn = get_conn()
        allow_model = (lambda: trips.reserve_model_request(conn, trips.owner_id())) if current_app.config['PUBLIC_DEMO'] else None
        result = advisor.answer(conn, data, allow_model=allow_model)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
