from dataclasses import replace

from flask import Blueprint, abort, render_template, request, current_app

from flats import advisor, place_context, repo_offers, repo_user
from flats.costs import compute_costs
from flats.grouping import group_offers
from flats.questions import questions_for
from flats.search import search
from flats.web import get_conn

bp = Blueprint("pages", __name__)


def _int(name, errors):
    value = "".join(request.values.get(name, "").split())
    if not value:
        return None
    if value.isascii() and value.isdecimal() and len(value) <= 10 and 0 < int(value) <= 1_000_000_000:
        return int(value)
    errors.append(f"Неверное число: {value}. Укажите целую сумму от 1 до 1 000 000 000 ₽.")
    return None


@bp.route("/", methods=["GET", "POST"])
def index():
    conn = get_conn()
    profile = repo_user.get_profile(conn)
    districts = [r[0] for r in conn.execute(
        "SELECT DISTINCT district FROM offers WHERE district IS NOT NULL AND district != '' ORDER BY district")]
    errors = []
    if request.values.get("apply"):
        rooms = request.values.getlist("rooms")
        selected_districts = request.values.getlist("districts")
        if any(value not in {str(n) for n in range(11)} for value in rooms):
            errors.append("Выберите количество комнат из списка.")
        if any(value not in districts for value in selected_districts):
            errors.append("Выберите район из списка.")
        proposed = replace(
            profile,
            monthly_max=_int("monthly_max", errors),
            move_in_max=_int("move_in_max", errors),
            rooms=tuple(dict.fromkeys(int(r) for r in rooms if r in {str(n) for n in range(11)})),
            districts=tuple(dict.fromkeys(selected_districts)),
            pets_required=bool(request.values.get("pets_required")),
        )
        if not errors:
            profile = proposed
            if request.method == "POST" or not current_app.config["PUBLIC_DEMO"]:
                repo_user.save_profile(conn, profile)
    rows = search(conn, profile)
    total = len(group_offers(repo_offers.all_offers(conn)))
    from flats.repo_houses import house_links_for_offers
    links=house_links_for_offers(conn, [(r.offer.source,r.offer.source_id) for r in rows])
    groups={}
    unlocated=[]
    from urllib.parse import quote
    for row in rows:
        link=links[(row.offer.source,row.offer.source_id)]
        coords=(link.get('summary') or {}).get('coordinates')
        if link['status'] not in ('exact','manual') or not coords:
            unlocated.append(row)
            continue
        group=groups.setdefault(link['house_id'],{'id':link['house_id'],'address':link['summary']['address'],
            'lat':coords['lat'],'lon':coords['lon'],'offers':[]})
        if not any(o['key']==row.card.key for o in group['offers']):
            group['offers'].append({'key':row.card.key,'url':'/flat/'+quote(row.card.key,safe=''),
                                  'price':row.costs.monthly_known,'offer':row.offer})
    return render_template("search.html", rows=rows, total=total, profile=profile,
                           districts=districts, errors=errors, map_houses=list(groups.values()), map_unlocated=unlocated)


@bp.get("/flat/<path:key>")
def flat(key):
    conn = get_conn()
    offers = repo_offers.offers_by_key(conn, key)
    if not offers:
        abort(404)
    card = group_offers(offers)[0]
    blocks = [(o, compute_costs(o), questions_for(card, o)) for o in card.offers]
    house_facts = place_context.house_facts(card.best.address)
    house_sources = list({fact["url"]: fact for fact in house_facts}.values())
    from flats.repo_houses import get_house_passport, house_links_for_offers
    from flats.web.houses import passport_context
    best_key = (card.best.source, card.best.source_id)
    link = house_links_for_offers(conn, [best_key])[best_key]
    passport = get_house_passport(conn, link['house_id']) if link['status'] in ('exact', 'manual') and link['house_id'] else None
    house_context = passport_context(passport) if passport else {'passport': None}
    return render_template("flat.html", card=card, blocks=blocks,
                           house_facts=house_facts, house_sources=house_sources,
                           inline_house=True, flat_key=key, **house_context)


@bp.get("/advisor")
def advisor_page():
    conn = get_conn()
    query = request.args.get("q", "")
    priority = request.args.get("priority", "budget")
    flat_key = request.args.get("key")
    try:
        offer_ids = advisor.validate_offer_ids(request.values.getlist("o"))
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
    from flats.repo_houses import house_links_for_offers
    links = house_links_for_offers(conn, [(o.source, o.source_id) for o in offers])
    districts = sorted({o.district for o in offers if o.district})
    return render_template("advisor.html", profile=repo_user.get_profile(conn), offer_ids=offer_ids,
                           scope=scope, query=query, priority=priority, flat_key=flat_key,
                           llm_available=advisor.llm_available(), all_offer_count=len(offers), districts=districts,
                           house_count=len({link["house_id"] for link in links.values()
                                            if link["status"] in ("exact", "manual") and link["summary"]}))
