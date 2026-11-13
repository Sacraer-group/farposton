from importlib import import_module
from pathlib import Path
from sqlite3 import Connection

from flask import Flask, current_app, g, render_template

from flats import config, db
from flats.web.format import rub, status_label


def get_conn() -> Connection:
    if "conn" not in g:
        g.conn = db.connect(current_app.config["DB_PATH"])
        db.init_db(g.conn)
    return g.conn


def create_app(db_path: Path | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config["DB_PATH"] = db_path or config.db_path()
    app.jinja_env.filters.update(rub=rub, status_label=status_label)

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
        return {"last_import": row["finished_at"] if row is not None else None}

    for name in ("pages", "actions", "compare", "swipe"):
        try:
            module = import_module(f"flats.web.{name}")
        except ImportError:
            continue
        app.register_blueprint(module.bp)

    if "pages" not in app.blueprints:
        @app.get("/")
        def index():
            return render_template("base.html")

    return app
