from flask import Blueprint, render_template

from flats import repo_user
from flats.search import search
from flats.web import get_conn

bp = Blueprint("swipe", __name__)


@bp.get("/swipe")
def swipe():
    conn = get_conn()
    rows = [r for r in search(conn, repo_user.get_profile(conn)) if r.decision != "save"]
    return render_template("swipe.html", rows=rows)
