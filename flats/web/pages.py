from dataclasses import replace

from flask import Blueprint, abort, render_template, request

from flats import advisor, place_context, repo_offers, repo_user
from flats.costs import compute_costs
from flats.grouping import group_offers
from flats.questions import questions_for
from flats.search import search
from flats.web import get_conn

bp = Blueprint("pages", __name__)


def _int(name, errors):
    value = request.args.get(name, "").replace(" ", "")
    if not value:
        return None
    if value.isdigit():
        return int(value)
    errors.append(f"Неверное число: {value}")
    return None


@bp.get("/")
def index():
    conn = get_conn()
    profile = repo_user.get_profile(conn)
    errors = []
    if request.args.get("apply"):
        profile = replace(
            profile,
            monthly_max=_int("monthly_max", errors),
            move_in_max=_int("move_in_max", errors),
            rooms=tuple(int(r) for r in request.args.getlist("rooms") if r.isdigit()),
            districts=tuple(request.args.getlist("districts")),
            pets_required=bool(request.args.get("pets_required")),
        )
        repo_user.save_profile(conn, profile)
    rows = search(conn, profile)
    total = len(group_offers(repo_offers.all_offers(conn)))
    districts = [r[0] for r in conn.execute(
        "SELECT DISTINCT district FROM offers WHERE district IS NOT NULL ORDER BY district")]
    return render_template("search.html", rows=rows, total=total, profile=profile,
                           districts=districts, errors=errors)


@bp.get("/flat/<path:key>")
def flat(key):
    offers = repo_offers.offers_by_key(get_conn(), key)
    if not offers:
        abort(404)
    card = group_offers(offers)[0]
    blocks = [(o, compute_costs(o), questions_for(card, o)) for o in card.offers]
    house_facts = place_context.house_facts(card.best.address)
    house_sources = list({fact["url"]: fact for fact in house_facts}.values())
    return render_template("flat.html", card=card, blocks=blocks,
                           house_facts=house_facts, house_sources=house_sources)


@bp.get("/advisor")
def advisor_page():
    conn = get_conn()
    query = request.args.get("q", "")
    priority = request.args.get("priority", "budget")
    flat_key = request.args.get("key")
    try:
        offer_ids = advisor.validate_offer_ids(request.args.getlist("o"))
    except ValueError as exc:
        abort(400, str(exc))
    scope = request.args.get("scope", "offers" if offer_ids or flat_key else "search")
    if len(query) > 1200 or priority not in advisor.PRIORITIES or scope not in advisor.SCOPES:
        abort(400, "Неверный вопрос, приоритет или область поиска")
    offers = repo_offers.all_offers(conn)
    by_id = {f"{o.source}:{o.source_id}": o for o in offers}
    if any(key not in by_id for key in offer_ids):
        abort(400, "Одно из предложений не найдено")
    if flat_key:
        related = repo_offers.offers_by_key(conn, flat_key)
        if not related:
            abort(404)
        related_ids = {f"{o.source}:{o.source_id}" for o in related}
        if any(key not in related_ids for key in offer_ids):
            abort(400, "Предложение не относится к выбранной квартире")
        if not offer_ids:
            offer_ids = [f"{o.source}:{o.source_id}" for o in group_offers(related)[0].offers[:5]]
    if scope == "offers" and not offer_ids:
        abort(400, "Выберите предложения для разбора")
    if scope == "saved":
        decisions = repo_user.current_decisions(conn)
        if any(decisions.get((by_id[key].source, by_id[key].source_id)) != "save" for key in offer_ids):
            abort(400, "Выбранное предложение не находится в избранном")
    return render_template("advisor.html", profile=repo_user.get_profile(conn), offer_ids=offer_ids,
                           scope=scope, query=query, priority=priority, flat_key=flat_key,
                           llm_available=advisor.llm_available(), all_offer_count=len(offers),
                           house_count=sum(bool(place_context.house_facts(address))
                                           for address in {o.address for o in offers if o.address}))
