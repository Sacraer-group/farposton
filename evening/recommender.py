from __future__ import annotations

from datetime import date

from .realdata import haversine_m, schedule_open_state
from .timeutil import in_window_full, parse_hhmm


def _feature_info(place: dict, required: list[str], preferred: list[str]):
    feats = place.get("features", {})
    confirmed = []
    unknown = []
    matched_preferred = []
    for f in required + preferred:
        val = feats.get(f)
        if val is True:
            confirmed.append(f)
        elif val == "unknown":
            unknown.append(f)
    for f in preferred:
        if feats.get(f) is True:
            matched_preferred.append(f)

    def uniq(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return uniq(confirmed), uniq(unknown), uniq(matched_preferred)


def _place_open(place: dict, request: dict, meal_date: date | None) -> bool:
    # demo format
    if "hours" in place and isinstance(place.get("hours"), dict):
        hours = place.get("hours", {})
        try:
            open_t = parse_hhmm(hours.get("open", "00:00"))
            close_t = parse_hhmm(hours.get("close", "00:00"))
        except Exception:
            return False
        return in_window_full(parse_hhmm(request["meal_start"]), parse_hhmm(request["meal_end"]), open_t, close_t)
    # real format
    if meal_date is None:
        return False
    state = schedule_open_state(place.get("schedule"), meal_date, request["meal_start"], request["meal_end"])
    return state == "open"


def _distance_m(place: dict, event: dict | None) -> int | None:
    if "distance_m" in place and isinstance(place.get("distance_m"), int):
        return int(place["distance_m"])
    if event is None:
        return None
    vlat = event.get("venue_lat")
    vlon = event.get("venue_lon")
    plat = place.get("lat")
    plon = place.get("lon")
    if not all(isinstance(x, (int, float)) for x in (vlat, vlon, plat, plon)):
        return None
    return haversine_m(float(vlat), float(vlon), float(plat), float(plon))


def recommend(places: list[dict], request: dict, event: dict, meal_date: date | None = None) -> tuple[dict, dict]:
    required = request.get("required_features", [])
    preferred = request.get("preferred_features", [])
    max_distance = request["max_distance_m"]

    candidates = []
    for place in places:
        if not _place_open(place, request, meal_date):
            continue
        dist = _distance_m(place, event)
        if dist is None or dist > max_distance:
            continue
        feats = place.get("features", {})
        ok_req = True
        for f in required:
            if feats.get(f) is not True:
                ok_req = False
                break
        if not ok_req:
            continue
        pref_score = sum(1 for f in preferred if feats.get(f) is True)
        candidates.append((-pref_score, int(dist), str(place.get("id")), place, int(dist)))

    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    if len(candidates) < 2:
        raise ValueError("not_enough_places")

    primary = candidates[0]
    primary_cat = primary[3].get("category")
    alt = None
    for c in candidates[1:]:
        if c[3].get("category") != primary_cat:
            alt = c
            break
    if alt is None:
        alt = candidates[1]

    def pack(item):
        _, _, _, place, dist = item
        confirmed, unknown, matched_preferred = _feature_info(place, required, preferred)
        out = {
            "place_id": place.get("id"),
            "name": place.get("name"),
            "category": place.get("category"),
            "distance_m": dist,
            "confirmed": confirmed,
            "unknown": unknown,
            "matched_preferred": matched_preferred,
        }
        # preserve useful real fields in snapshot
        if "address" in place:
            out["address"] = place.get("address")
        if "rating" in place:
            out["rating"] = place.get("rating")
        if "review_count" in place:
            out["review_count"] = place.get("review_count")
        if "updated_at" in place:
            out["updated_at"] = place.get("updated_at")
        if "synthetic" in place:
            out["synthetic"] = bool(place.get("synthetic"))
        return out

    return pack(primary), pack(alt)
