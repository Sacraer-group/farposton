from importlib import import_module
import os
import secrets
from datetime import timedelta
from pathlib import Path
from sqlite3 import Connection

from flask import Flask, current_app, g, render_template, request, session, jsonify, has_request_context

from flats import config, db
from flats.web.format import part_label, room_label, rub, status_label


def get_conn() -> Connection:
    if "conn" not in g:
        g.conn = db.connect(current_app.config["DB_PATH"])
        db.init_db(g.conn)
    return g.conn


def create_app(db_path: Path | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config["DB_PATH"] = db_path or config.db_path()
    app.config["TWO_GIS_MAP_KEY"] = os.environ.get("FLATS_2GIS_MAP_KEY", "").strip()
    app.config['PUBLIC_DEMO'] = os.environ.get('FLATS_PUBLIC_DEMO', '0') == '1'
    app.secret_key = os.environ.get('FLATS_SECRET_KEY') or (None if app.config['PUBLIC_DEMO'] else secrets.token_hex(32))
    if not app.secret_key:
        raise RuntimeError('FLATS_SECRET_KEY is required for public demo')
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('FLATS_SECURE_COOKIE', '0') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30), MAX_CONTENT_LENGTH=128*1024)

    @app.before_request
    def identify_visitor():
        if request.endpoint in ('static', 'health'):
            return
        from flats.visitors import begin
        conn = get_conn()
        if app.config['PUBLIC_DEMO'] and 'visitor_id' not in session:
            from flats.trips import allow_new_visitor
            if not allow_new_visitor(conn):
                return jsonify(error='Слишком много запросов, попробуйте позже'), 429
        begin(conn)
        if app.config['PUBLIC_DEMO'] and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            token=request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
            if not isinstance(token,str) or not secrets.compare_digest(token,session['csrf_token']):
                return jsonify(error='Обновите страницу перед отправкой'),403

    @app.after_request
    def response_headers(response):
        if request.endpoint != 'static':
            response.headers['Cache-Control']='private, no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        return response

    @app.get('/health')
    def health():
        get_conn().execute('SELECT 1')
        return jsonify(status='ok')

    @app.get('/trips')
    def trip_page():
        return render_template('trips.html', trip_limits={'session_budget':int(os.environ.get('FLATS_TRIP_SESSION_BUDGET','100')),'global_budget':int(os.environ.get('FLATS_TRIP_GLOBAL_BUDGET','1000'))})
    app.jinja_env.filters.update(rub=rub, status_label=status_label, part_label=part_label)
    app.jinja_env.globals.update(room_label=room_label)

    @app.teardown_appcontext
    def close_conn(_error: BaseException | None) -> None:
        conn = g.pop("conn", None)
        if conn is not None:
            conn.close()

    @app.context_processor
    def last_import():
        row = get_conn().execute(
            "SELECT finished_at FROM import_runs WHERE status='ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        from flats import repo_offers
        from flats.repo_houses import house_links_for_offers
        from flats.trips import trip_context
        keys=[(o.source,o.source_id) for o in repo_offers.all_offers(get_conn())]
        return {"last_import": row["finished_at"] if row is not None else None,
                'two_gis_map_key':current_app.config["TWO_GIS_MAP_KEY"],
                'csrf_token':session.get('csrf_token','') if has_request_context() else '',
                'house_links_by_offer':house_links_for_offers(get_conn(),keys),
                'trips_by_offer':trip_context(get_conn(),keys)}

    for name in ("pages", "actions", "compare", "swipe", "houses", "trips"):
        try:
            module = import_module(f"flats.web.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"flats.web.{name}":
                raise
            continue
        app.register_blueprint(module.bp)

    if "pages" not in app.blueprints:
        @app.get("/")
        def index():
            return render_template("base.html")

    return app
