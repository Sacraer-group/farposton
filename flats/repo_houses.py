"""House evidence storage. Writers participate in the caller's transaction."""
import hashlib
import json
from dataclasses import astuple
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .house_identity import normalize_house_address

_SOURCE = ('source_key provider section source_url source_type external_house_id matched_address match_status '
           'checked_at fetched_at published_at source_as_of document_date fetch_status error artifact_path '
           'content_sha256 coverage_json raw_json').split()
_FACTS = ('postal_code construction_year commissioning_year floors_text entrances wall_material '
          'building_area_text premises_area_text central_heating_text hot_water_text cold_water_text '
          'lat lon coordinates_precision raw_json').split()
_CONTACT = ('contact_key name role scope phone_raw dispatch_phone_raw email office_address website inn ogrn '
            'relationship_source_url contract_start_reported relationship_status conflict_key conflict_note raw_json').split()
_EVENT = ('event_key kind source_event_id title resource organization_name status_reported period_text starts_at '
          'ends_at year_from year_to completion_reported source_url source_locator published_at raw_json').split()
_EVIDENCE = 'source_url source_type provider checked_at fetched_at source_as_of document_date fetch_status'.split()


def init_house_db(conn):
    conn.executescript(Path(__file__).with_name('house_schema.sql').read_text(encoding='utf-8'))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _url(value):
    if not isinstance(value, str):
        raise ValueError('A source URL is required')
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('Invalid source URL')


def _time(value):
    if not isinstance(value, str):
        raise ValueError('A source timestamp is required')
    if len(value) == 10:
        return date.fromisoformat(value).isoformat()
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Source timestamp must include timezone')
    return parsed.astimezone(timezone.utc).isoformat()


def _row(data, columns):
    if not isinstance(data, dict) or set(data) - set(columns):
        raise ValueError('Unknown evidence fields')
    out = {key: data.get(key) for key in columns}
    for key in columns:
        if key.endswith('_json'):
            value = out[key]
            if isinstance(value, str):
                value = json.loads(value)
            if value is None:
                value = {}
            if not isinstance(value, dict):
                raise ValueError('Evidence JSON must be an object')
            out[key] = _json(value)
    return out


def _insert(conn, table, data):
    # All table/column names originate in the fixed lists above.
    keys = list(data)
    return conn.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
                        [data[key] for key in keys]).lastrowid


def ensure_house(conn, address, *, address_display, created_at):
    values = astuple(address)
    created_at = _time(created_at)
    conn.execute('INSERT OR IGNORE INTO houses (city,street_type,street_name,house_number,corpus,structure,address_display,created_at) '
                 'VALUES (?,?,?,?,?,?,?,?)', (*values, address_display, created_at))
    return conn.execute('SELECT id FROM houses WHERE city=? AND street_type=? AND street_name=? AND house_number=? '
                        'AND corpus=? AND structure=?', values).fetchone()['id']


def link_offer_house(conn, source, source_id, house_id, *, match_status, offer_address_raw,
                     match_basis, evidence_url, checked_at, note=None, candidates=None):
    if match_status not in ('exact', 'manual', 'unmatched', 'ambiguous', 'stale'):
        raise ValueError('Invalid house link status')
    if (match_status in ('exact', 'manual')) != (house_id is not None):
        raise ValueError('House link status and identity disagree')
    conn.execute('INSERT INTO offer_houses (source,source_id,house_id,match_status,offer_address_raw,match_basis,'
                 'evidence_url,checked_at,note,candidates_json) VALUES (?,?,?,?,?,?,?,?,?,?) '
                 'ON CONFLICT(source,source_id) DO UPDATE SET house_id=excluded.house_id,match_status=excluded.match_status,'
                 'offer_address_raw=excluded.offer_address_raw,match_basis=excluded.match_basis,evidence_url=excluded.evidence_url,'
                 'checked_at=excluded.checked_at,note=excluded.note,candidates_json=excluded.candidates_json',
                 (source, source_id, house_id, match_status, offer_address_raw, match_basis, evidence_url,
                  _time(checked_at), note, _json(candidates or [])))


def store_house_observation(conn, house_id, observation):
    if not isinstance(observation, dict) or set(observation) - {'source', 'facts', 'contacts', 'events'}:
        raise ValueError('Invalid observation')
    source = _row(observation.get('source'), _SOURCE)
    for field in ('source_key', 'provider', 'section', 'source_type', 'match_status', 'fetch_status'):
        if not isinstance(source[field], str) or not source[field]:
            raise ValueError(f'Missing {field}')
    _url(source['source_url'])
    source['checked_at'] = _time(source['checked_at'])
    if source['fetched_at'] is not None:
        source['fetched_at'] = _time(source['fetched_at'])
    facts = _row(observation['facts'], _FACTS) if observation.get('facts') is not None else None
    contacts = [_row(row, _CONTACT) for row in observation.get('contacts', [])]
    events = [_row(row, _EVENT) for row in observation.get('events', [])]
    if facts or contacts or events:
        if source['fetch_status'] != 'ok' or source['match_status'] not in ('exact', 'manual'):
            raise ValueError('Only successful matched observations may assert facts')
    if facts and source['section'] not in ('facts', 'coordinates', 'repairs'):
        raise ValueError('Facts do not belong to this source section')
    if contacts and source['section'] != 'contacts':
        raise ValueError('Contacts do not belong to this source section')
    if any(e['kind'] != ('outage' if source['section'] == 'outages' else e['kind']) for e in events) or (
            events and source['section'] not in ('outages', 'repairs')):
        raise ValueError('Events do not belong to this source section')
    if source['section'] == 'repairs' and any(e['kind'] not in ('repair_plan', 'repair_execution') for e in events):
        raise ValueError('Repair source cannot assert outages')
    for rows, field in ((contacts, 'contact_key'), (events, 'event_key')):
        if len({r[field] for r in rows}) != len(rows):
            raise ValueError('Duplicate evidence key')
    for rows, fields in ((contacts, ('contact_key', 'name')), (events, ('event_key', 'title'))):
        if any(not isinstance(row[field], str) or not row[field] for row in rows for field in fields):
            raise ValueError('Evidence keys and titles must be nonempty strings')
    for contact in contacts:
        if contact['website']:
            _url(contact['website'])
        if contact['relationship_source_url']:
            _url(contact['relationship_source_url'])
    for event in events:
        _url(event['source_url'])
    digest = hashlib.sha256(_json({'source': source, 'facts': facts, 'contacts': contacts, 'events': events}).encode()).hexdigest()
    raw = json.loads(source['raw_json'])
    if '_import_payload_sha256' in raw:
        raise ValueError('Reserved evidence field')
    raw['_import_payload_sha256'] = digest
    source['raw_json'] = _json(raw)
    old = conn.execute('SELECT id,raw_json FROM house_sources WHERE house_id=? AND source_key=? AND checked_at=?',
                       (house_id, source['source_key'], source['checked_at'])).fetchone()
    if old:
        if json.loads(old['raw_json']).get('_import_payload_sha256') != digest:
            raise ValueError('Immutable source observation changed')
        return old['id']
    if not conn.in_transaction:
        conn.execute('BEGIN')
    conn.execute('SAVEPOINT house_observation')
    try:
        sid = _insert(conn, 'house_sources', {'house_id': house_id, **source,
                      'recorded_at': datetime.now(timezone.utc).isoformat()})
        if facts:
            _insert(conn, 'house_facts', {'source_id': sid, **facts})
        for row in contacts:
            _insert(conn, 'house_contacts', {'source_id': sid, **row})
        for row in events:
            _insert(conn, 'house_events', {'source_id': sid, **row})
    except Exception:
        conn.execute('ROLLBACK TO house_observation')
        conn.execute('RELEASE house_observation')
        raise
    conn.execute('RELEASE house_observation')
    return sid


def _source(row):
    data = dict(row)
    data['coverage'] = json.loads(data.pop('coverage_json'))
    data['raw'] = json.loads(data.pop('raw_json'))
    return data


def get_house_passport(conn, house_id):
    house = conn.execute('SELECT * FROM houses WHERE id=?', (house_id,)).fetchone()
    if house is None:
        return None
    sources = [_source(r) for r in conn.execute('SELECT * FROM house_sources WHERE house_id=? ORDER BY checked_at,id', (house_id,))]
    by_id = {r['id']: r for r in sources}
    latest, successful = {}, {}
    for src in sources:
        latest[src['source_key']] = src
        if src['fetch_status'] in ('ok', 'empty'):
            successful[src['source_key']] = src
    active = {s['id'] for s in successful.values()}
    facts, contacts, events = [], [], {}
    for table, target in [('house_facts', facts), ('house_contacts', contacts), ('house_events', None)]:
        for row in conn.execute(f'SELECT t.* FROM {table} t JOIN house_sources s ON s.id=t.source_id WHERE s.house_id=? ORDER BY s.checked_at,s.id,t.rowid', (house_id,)):
            item = dict(row)
            src = by_id[item['source_id']]
            item['source'] = {k: src[k] for k in _EVIDENCE}
            item['raw'] = json.loads(item.pop('raw_json'))
            if table == 'house_events':
                if item['kind'] == 'outage' or item['source_id'] in active:
                    events[(src['provider'], item['event_key'])] = item
            elif item['source_id'] in active or (table == 'house_contacts' and item['relationship_status'] == 'unresolved_conflict'):
                target.append(item)
    contacts = list({(by_id[c['source_id']]['provider'], c['contact_key'], c['relationship_status'] == 'unresolved_conflict'): c for c in contacts}.values())
    coordinate_facts = [f for f in facts if f['lat'] is not None and f['lon'] is not None]
    coordinates = None
    if coordinate_facts:
        f = max(coordinate_facts, key=lambda r: r['source']['checked_at'])
        coordinates = {k: f[k] for k in ('lat', 'lon')}
        coordinates.update(precision=f['coordinates_precision'], source_url=f['source']['source_url'], fetched_at=f['source']['fetched_at'])
    issues = []
    if any(c['relationship_status'] == 'unresolved_conflict' for c in contacts):
        issues.append({'code': 'management_conflict', 'message': 'Источники называют разные управляющие организации. Действующую УК нужно уточнить.',
                       'source_urls': sorted({c['source']['source_url'] for c in contacts if c['role'] == 'management'})})
    listing_floors = {r[0] for r in conn.execute(
        'SELECT DISTINCT o.floors_total FROM offers o JOIN offer_houses l ON o.source=l.source AND o.source_id=l.source_id '
        'WHERE l.house_id=? AND l.match_status IN (\'exact\',\'manual\') AND o.address=l.offer_address_raw AND o.floors_total IS NOT NULL', (house_id,))}
    if any(f['floors_text'] and str(f['floors_text']).isdigit() and listing_floors and int(f['floors_text']) not in listing_floors for f in facts):
        issues.append({'code': 'floor_count_conflict', 'message': 'Этажность в объявлении и источнике о доме различается. Уточните корпус и этажность на просмотре.',
                       'source_urls': sorted({f['source']['source_url'] for f in facts if f['floors_text']})})
    for s in latest.values():
        if s['fetch_status'] not in ('ok', 'empty'):
            issues.append({'code': 'source_unavailable', 'message': 'Проверка источника: ' + s['fetch_status'], 'source_urls': [s['source_url']]})
        if s['section'] == 'outages' and (s['coverage'].get('coverage_uncertain') or not s['coverage'].get('all_time_history_complete')):
            issues.append({'code': 'partial_history', 'message': 'Архив публикаций не подтверждает полноту реальных отключений.', 'source_urls': [s['source_url']]})
    ev = list(events.values())
    outages = [e for e in ev if e['kind'] == 'outage']
    return {'house': {'id': house_id, 'address': house['address_display'], 'city': house['city']},
            'coordinates': coordinates, 'facts': facts, 'contacts': contacts,
            'outages': {'events': outages, 'coverage': [s for s in latest.values() if s['section'] == 'outages'], 'published_records_count': len(outages)},
            'repairs': {'plans': [e for e in ev if e['kind'] == 'repair_plan'], 'executions': [e for e in ev if e['kind'] == 'repair_execution'],
                        'coverage': [s for s in latest.values() if s['section'] == 'repairs']},
            'sources': {'latest_attempts': list(latest.values()), 'last_successful': list(successful.values())}, 'issues': issues}


def house_links_for_offers(conn, offer_keys):
    result = {key: {'status': 'unmatched', 'house_id': None, 'candidates': [], 'summary': None} for key in offer_keys}
    if not result:
        return result
    passports = {}
    # One bounded catalog read; individual passports are loaded once per distinct house.
    for row in conn.execute('SELECT l.*,o.address FROM offer_houses l JOIN offers o ON o.source=l.source AND o.source_id=l.source_id'):
        key = (row['source'], row['source_id'])
        if key not in result:
            continue
        status = row['match_status'] if row['offer_address_raw'] == row['address'] else 'stale'
        hid = row['house_id'] if status in ('exact', 'manual') else None
        summary = None
        if hid is not None:
            if hid not in passports:
                passports[hid] = get_house_passport(conn, hid)
            p = passports[hid]
            management = [c for c in p['contacts'] if c['role'] == 'management' and c['scope'] == 'house']
            summary = {'address': p['house']['address'], 'coordinates': p['coordinates'],
                       'management_status': 'conflict_unresolved' if any(c['relationship_status'] == 'unresolved_conflict' for c in management)
                       else 'published_claim' if management else 'not_found_in_checked_sources'}
        result[key] = {'status': status, 'house_id': hid, 'candidates': json.loads(row['candidates_json']), 'summary': summary}
    return result
