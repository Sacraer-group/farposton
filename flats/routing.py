"""Small server-only 2GIS adapter; never persist provider responses or expose keys."""
import math
import os
from pathlib import Path

import requests


def api_key():
    path = os.environ.get('FLATS_2GIS_KEY_FILE')
    if not path:
        raise RuntimeError('Доступ к 2ГИС не настроен')
    try:
        key = Path(path).read_text().strip()
    except OSError:
        raise RuntimeError('Доступ к 2ГИС не настроен') from None
    if not key:
        raise RuntimeError('Доступ к 2ГИС не настроен')
    return key


def point(value):
    if not isinstance(value, dict):
        raise ValueError('Нужна подтверждённая точка')
    lat, lon = value.get('lat'), value.get('lon')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (lat, lon)) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError('Неверные координаты')
    return {'lat': float(lat), 'lon': float(lon)}


def route(source, target, start_time, *, http=requests):
    try:
        key = api_key()
    except RuntimeError:
        return {'status': 'api_error', 'reason': 'Доступ к Routing API 2ГИС не настроен'}
    try:
        response = http.post('https://routing.api.2gis.com/public_transport/2.0',
                             params={'key': key}, json={'source': {'point': point(source)},
                             'target': {'point': point(target)}, 'start_time': start_time,
                             'transport': ['bus', 'trolleybus', 'tram', 'shuttle_bus', 'funicular_railway', 'pedestrian'],
                             'enable_schedule': True, 'locale': 'ru', 'max_result_count': 5}, timeout=8)
        if response.status_code == 204:
            return {'status': 'no_route', 'reason': 'Маршрут не найден'}
        if response.status_code != 200:
            return {'status': 'api_error', 'reason': '2ГИС временно недоступен или нет доступа к API'}
        data = response.json()
        if not isinstance(data, list):
            raise ValueError('Invalid response')
        alternatives = []
        for item in data:
            duration = item.get('total_duration')
            transfers, crossings = item.get('transfer_count'), item.get('crossing_count')
            if any(type(v) is not int or v < 0 for v in (duration, transfers, crossings)) or type(item.get('pedestrian')) is not bool:
                continue
            alternatives.append({'duration_seconds': duration, 'transfers': transfers + crossings,
                                 'pedestrian': item['pedestrian'], 'walkway': str(item.get('total_walkway_distance', ''))[:300],
                                 'route_names': [str(n)[:100] for w in item.get('waypoints', []) if isinstance(w, dict)
                                                 for n in w.get('routes_names', [])][:30]})
        return {'status': 'ok', 'alternatives': alternatives} if alternatives else {'status': 'no_route', 'reason': 'Нет пригодных вариантов маршрута'}
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        return {'status': 'api_error', 'reason': 'Не удалось получить маршрут 2ГИС'}


def places(query, *, http=requests):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        raise ValueError('Введите название или адрес от 2 до 200 символов')
    try:
        response = http.get('https://catalog.api.2gis.com/3.0/items', params={
            'key': api_key(), 'q': 'Владивосток ' + query.strip(), 'fields': 'items.point', 'page_size': 10}, timeout=8)
        if response.status_code != 200:
            raise RuntimeError('Поиск мест 2ГИС временно недоступен')
        data = response.json()
        if data.get('meta', {}).get('code', 200) != 200:
            raise RuntimeError('Ключ 2ГИС не имеет доступа к поиску мест; выберите точную точку вручную')
        result = []
        for item in data.get('result', {}).get('items', []):
            try:
                location = point(item.get('point'))
            except ValueError:
                continue
            result.append({'id': str(item['id']), 'name': str(item['name'])[:200],
                           'address': str(item.get('address_name', ''))[:300], **location})
        return result
    except (requests.RequestException, ValueError, KeyError, AttributeError):
        raise RuntimeError('Не удалось получить места 2ГИС') from None
