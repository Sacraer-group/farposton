"""Read-only preview of constraints extracted from the renter's brief."""

import json

from flask import Blueprint, jsonify, request

from flats.requirements import parse_requirements
from flats.web import get_conn

bp = Blueprint("requirements", __name__, url_prefix="/api")


@bp.post("/requirements")
def preview():
    if not request.is_json:
        return jsonify(error="Ожидается JSON"), 400
    limit = 8 * 1024
    if request.content_length is not None and request.content_length > limit:
        return jsonify(error="Запрос слишком большой"), 413
    raw = request.stream.read(limit + 1)
    if len(raw) > limit:
        return jsonify(error="Запрос слишком большой"), 413
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"text"}:
            raise ValueError("Ожидается только поле text")
        districts = [row[0] for row in get_conn().execute(
            "SELECT DISTINCT district FROM offers WHERE district IS NOT NULL ORDER BY district")]
        result = parse_requirements(data["text"], districts)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)) else "Неверный JSON"
        return jsonify(error=message), 400
    return jsonify(result)
