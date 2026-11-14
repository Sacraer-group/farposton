"""Nearby places from the bundled OpenStreetMap extract."""
import json
import math
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _snapshot():
    return json.loads(Path(__file__).with_name('static').joinpath('nearby.json').read_text(encoding='utf-8'))


def nearby_places(coordinates):
    if not coordinates:
        return []
    lat, lon = coordinates['lat'], coordinates['lon']
    groups = {}
    for place in _snapshot()['places']:
        dlat = math.radians(place['lat'] - lat)
        dlon = math.radians(place['lon'] - lon)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat)) * math.cos(math.radians(place['lat'])) * math.sin(dlon / 2) ** 2
        distance = round(12742000 * math.asin(min(1, math.sqrt(a))))
        if distance <= 800:
            groups.setdefault(place['category'], []).append({**place, 'distance': distance})
    result = []
    for category in ('Остановки', 'Продукты', 'Аптеки', 'Медицина', 'Образование', 'Кафе'):
        candidates = sorted(groups.get(category, []), key=lambda item: item['distance'])
        selected = []
        for item in candidates:
            if any(item['name'] == other['name'] and abs(item['distance'] - other['distance']) < 40 for other in selected):
                continue
            selected.append(item)
            if len(selected) == 3:
                break
        if selected:
            result.append({'name': category, 'places': selected})
    return result
