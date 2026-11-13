from __future__ import annotations

from datetime import datetime

from .timeutil import in_window_full, parse_hhmm


def _feature_info(place: dict, required: list[str], preferred: list[str]):
    feats = place.get("features", {})
    confirmed = []
    unknown = []
    matched_preferred = []
    # confirmed/unknown relevant to request context
    for f in required + preferred:
        val = feats.get(f)
        if val is True:
            confirmed.append(f)
        elif val == "unknown":
            unknown.append(f)
    for f in preferred:
        if feats.get(f) is True:
            matched_preferred.append(f)
    # de-dup while preserving order
    def uniq(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out
    return uniq(confirmed), uniq(unknown), uniq(matched_preferred)


def recommend(seed: dict, request: dict, event: dict) -> tuple[dict, dict]:
    places = list(seed.get("places", []))
    meal_start = parse_hhmm(request["meal_start"])
    meal_end = parse_hhmm(request["meal_end"])
    required = request.get("required_features", [])
    preferred = request.get("preferred_features", [])
    max_distance = request["max_distance_m"]

    candidates = []
    for place in places:
        hours = place.get("hours", {})
        try:
            open_t = parse_hhmm(hours.get("open", "00:00"))
            close_t = parse_hhmm(hours.get("close", "00:00"))
        except Exception:
            continue
        if not in_window_full(meal_start, meal_end, open_t, close_t):
            continue
        if int(place.get("distance_m", 10**9)) > max_distance:
            continue
        feats = place.get("features", {})
        # unknown required does not pass
        ok_req = True
        for f in required:
            if feats.get(f) is not True:
                ok_req = False
                break
        if not ok_req:
            continue
        pref_score = sum(1 for f in preferred if feats.get(f) is True)
        candidates.append(( -pref_score, int(place.get("distance_m", 0)), str(place.get("id")), place))

    candidates.sort(key=lambda x: (x[0], x[1], x[2]))
    if len(candidates) < 2:
        raise ValueError("not_enough_places")

    primary_place = candidates[0][3]
    alt_place = None
    primary_cat = primary_place.get("category")
    # prefer different category if exists
    for _, _, _, p in candidates[1:]:
        if p.get("category") != primary_cat:
            alt_place = p
            break
    if alt_place is None:
        alt_place = candidates[1][3]

    def pack(place: dict):
        confirmed, unknown, matched_preferred = _feature_info(place, required, preferred)
        return {
            "place_id": place.get("id"),
            "name": place.get("name"),
            "category": place.get("category"),
            "distance_m": int(place.get("distance_m", 0)),
            "confirmed": confirmed,
            "unknown": unknown,
            "matched_preferred": matched_preferred,
        }

    return pack(primary_place), pack(alt_place)
