import sqlite3
import sys
from types import ModuleType

import pytest
from flask import Blueprint, render_template_string

from flats import web
from flats.web.format import rub, status_label


@pytest.fixture(autouse=True)
def missing_blueprints(monkeypatch):
    for name in ("pages", "actions", "compare", "swipe"):
        monkeypatch.setitem(sys.modules, f"flats.web.{name}", None)
        monkeypatch.delattr(web, name, raising=False)


@pytest.fixture
def app(tmp_path):
    app = web.create_app(tmp_path / "t.db")
    app.testing = True
    return app


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(40000, "40 000 ₽"), (0, "0 ₽"), (None, "не указано")],
)
def test_rub(amount, expected):
    assert rub(amount) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [("match", "Подходит"), ("unknown", "Нужно уточнить"), ("reject", "Не подходит")],
)
def test_status_label(status, expected):
    assert status_label(status) == expected


def test_shell_and_styles(app):
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Данные не загружены" in html
    assert '<html lang="ru">' in html
    assert '<meta charset="utf-8">' in html
    assert 'name="viewport"' in html
    assert "<title>Квартиры</title>" in html
    for url, label in [("/", "Поиск"), ("/favorites", "Избранное"), ("/swipe", "Свайпы")]:
        assert f'href="{url}"' in html
        assert label in html
    assert 'href="/static/app.css"' in html
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert css.mimetype == "text/css"


def test_latest_successful_import_by_id(app):
    client = app.test_client()
    with app.app_context():
        conn = web.get_conn()
        conn.execute(
            "INSERT INTO import_runs (started_at, finished_at, status) VALUES (?, ?, ?)",
            ("2026-11-13", "2026-11-13T15:00:00", "failed"),
        )
        conn.commit()
    assert "Данные не загружены" in client.get("/").get_data(as_text=True)
    with app.app_context():
        conn = web.get_conn()
        conn.executemany(
            "INSERT INTO import_runs (started_at, finished_at, status) VALUES (?, ?, ?)",
            [
                ("2026-11-13", "2026-11-13T14:00:00", "ok"),
                ("2026-11-13", "2026-11-13T13:00:00", "ok"),
                ("2026-11-13", "2026-11-13T16:00:00", "failed"),
                ("2026-11-13", None, "running"),
            ],
        )
        conn.commit()
    html = client.get("/").get_data(as_text=True)
    assert "Данные от 2026-11-13T13:00:00" in html
    assert "2026-11-13T14:00:00" not in html
    assert "2026-11-13T16:00:00" not in html


def test_template_filters_and_autoescape(app):
    with app.app_context():
        html = render_template_string(
            '{{ amount|rub }} / {{ status|status_label }} / {{ text }}',
            amount=40000, status="unknown", text="<script>alert(1)</script>",
        )
    assert html == "40 000 ₽ / Нужно уточнить / &lt;script&gt;alert(1)&lt;/script&gt;"


def test_database_path_uses_config_unless_explicit(tmp_path, monkeypatch):
    configured = tmp_path / "configured.db"
    monkeypatch.setenv("FLATS_DB", str(configured))
    assert web.create_app().config["DB_PATH"] == configured
    explicit = tmp_path / "explicit.db"
    assert web.create_app(explicit).config["DB_PATH"] == explicit


@pytest.mark.parametrize("fail", [False, True])
def test_connection_reused_per_request_and_closed(app, fail):
    connections = []

    @app.get("/connection")
    def connection():
        conn = web.get_conn()
        connections.append(conn)
        assert web.get_conn() is conn
        assert conn.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0] == 0
        if fail:
            raise RuntimeError("request failed")
        return "ok"

    client = app.test_client()
    for _ in range(2):
        if fail:
            with pytest.raises(RuntimeError, match="request failed"):
                client.get("/connection")
        else:
            assert client.get("/connection").status_code == 200
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connections[-1].execute("SELECT 1")
    assert connections[0] is not connections[1]


@pytest.mark.parametrize("name", ["pages", "actions", "compare", "swipe"])
def test_available_blueprint_registered_without_other_modules(tmp_path, monkeypatch, name):
    module = ModuleType(f"flats.web.{name}")
    module.bp = Blueprint(name, __name__)
    route = "/" if name == "pages" else f"/{name}"
    module.bp.add_url_rule(route, view_func=lambda: name)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(web, name, module, raising=False)
    app = web.create_app(tmp_path / "t.db")
    assert name in app.blueprints
    assert app.test_client().get(route).get_data(as_text=True) == name
    assert sum(rule.rule == "/" for rule in app.url_map.iter_rules()) == 1
    if name != "pages":
        assert "Данные не загружены" in app.test_client().get("/").get_data(as_text=True)
