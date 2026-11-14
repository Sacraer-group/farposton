"""Offline adapter for the collected house-passport version 1 snapshot."""
import hashlib
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from .house_identity import normalize_house_address
from . import repo_houses as repo

_FIELDS = {'postal_code': 'postal_code', 'construction_year': 'construction_year',
           'commissioning_year': 'commissioning_year', 'year_commissioned': 'commissioning_year',
           'floors': 'floors_text', 'floors_total': 'floors_text', 'entrances': 'entrances',
           'wall_material': 'wall_material', 'building_area': 'building_area_text', 'building_area_m2': 'building_area_text',
           'premises_area': 'premises_area_text', 'premises_area_m2': 'premises_area_text',
           'central_heating': 'central_heating_text', 'hot_water_supply': 'hot_water_text', 'cold_water_supply': 'cold_water_text'}


def load_house_bundle(path):
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.rsplit('/', 1)[-1] == 'house_passports.json']
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 30_000_000:
                raise ValueError('Expected one bounded house_passports.json in archive')
            return json.loads(archive.read(names[0]))
    return json.loads(path.read_text(encoding='utf-8'))


def _fingerprint(value):
    return hashlib.sha256(' '.join(str(value).casefold().split()).encode()).hexdigest()[:20]


def _status(check):
    value = check.get('status')
    if check.get('http_status') in (401, 403, 429) or value in (401, 403, 429) or any(str(code) in str(check.get('error', '')) for code in (401, 403, 429)):
        return 'blocked'
    if check.get('error') or value in ('fetch_error', 'error', 'unavailable'):
        return 'error'
    if value in ('not_found', 'not_listed_in_year', 'street_not_listed_in_year', 'house_not_listed_in_year', 'address_not_found'):
        return 'not_found'
    if value in ('ambiguous', 'parse_error', 'blocked', 'empty'):
        return value
    return 'ok'


def _observations(house):
    observations = {}
    checks = []
    for block in ('vl_match', 'management', 'outages', 'repairs'):
        checks += house.get(block, {}).get('source_checks', [])
    checks += house.get('repairs', {}).get('execution_checks', [])
    def receipt(url, section, item=None, *, key=None, check=None, raw=None):
        item = item or {}
        check = check or next((c for c in reversed(checks) if c.get('url', c.get('source_url')) == url), {})
        checked = check.get('checked_at') or item.get('fetched_at') or item.get('checked_at')
        if not checked:
            raise ValueError(f'Missing observation timestamp: {url}')
        provider = 'fkr' if urlsplit(url).hostname == 'fkr25.ru' else 'vl' if urlsplit(url).hostname in ('www.vl.ru', 'vl.ru') else urlsplit(url).hostname
        if not key:
            key = provider + ':' + section + ':' + _fingerprint(url)
        ident = (key, repo._time(checked))
        if ident not in observations:
            fetched = check.get('fetched_at') or item.get('fetched_at')
            if not fetched and check.get('status') in ('fetched', 'retrieved', 'retrieved_via_web'):
                fetched = check.get('checked_at')
            status = _status(check)
            observations[ident] = {'source': {'source_key': key, 'provider': provider, 'section': section,
                'source_url': url, 'source_type': item.get('source_type') or check.get('source_type') or ('official_regional_programme' if provider == 'fkr' else 'secondary_address_directory'),
                'external_house_id': str(item.get('source_house_id') or check.get('source_house_id') or (house.get('vl_match', {}).get('vl_house_id') if provider == 'vl' else '') or '') or None,
                'matched_address': check.get('matched_address') or (house.get('repairs', {}).get('matched_address') if provider == 'fkr' else house.get('vl_match', {}).get('matched_address')),
                'match_status': 'exact' if status in ('ok', 'empty') else 'unmatched',
                'checked_at': checked, 'fetched_at': fetched,
                'document_date': item.get('document_date') or check.get('document_date'),
                'source_as_of': item.get('source_as_of_claimed'), 'fetch_status': status,
                'error': check.get('error'), 'artifact_path': check.get('raw_file'),
                'content_sha256': check.get('sha256'), 'coverage_json': {},
                'raw_json': {'building_key': house['building_key'], 'check': check, **(raw or {})}},
                'facts': None, 'contacts': [], 'events': []}
        return observations[ident]

    mapping = house['vl_match']
    for check in mapping.get('source_checks', []):
        receipt(check['url'], 'identity', check=check)
    for block_name in ('management', 'repairs'):
        block = house.get(block_name, {})
        for field, item in block.get('building_fields', {}).items():
            url = item.get('source_url')
            if not url:
                continue
            obs = receipt(url, 'repairs' if block_name == 'repairs' else 'facts', item,
                          key='fkr:regional-programme' if block_name == 'repairs' else None)
            if obs['source']['fetch_status'] != 'ok':
                continue
            obs['source']['raw_json'].setdefault('building_fields', {})[field] = item
            if field not in _FIELDS or item.get('value') is None:
                continue
            target, value = _FIELDS[field], item['value']
            if target in ('construction_year', 'commissioning_year', 'entrances'):
                value = int(value) if str(value).isdigit() else None
            else:
                value = str(value)
            if value is not None:
                if obs['facts'] is None:
                    obs['facts'] = {'raw_json': {}}
                obs['facts'][target] = value
    management = house.get('management', {})
    for city_scope, organizations in ((False, management.get('organizations', [])), (True, management.get('city_service_contacts', []))):
        for org in organizations:
            url = org.get('relationship_source') or org.get('source_url')
            obs = receipt(url, 'contacts', org)
            if obs['source']['fetch_status'] != 'ok':
                continue
            conflict = bool(management.get('conflicts')) and not city_scope and 'управ' in org.get('role', '').casefold()
            role = 'city_service' if city_scope else 'management' if 'управ' in org.get('role', '').casefold() else 'lift' if 'лифт' in org.get('role', '').casefold() else 'emergency' if 'аварийн' in org.get('role', '').casefold() else 'maintenance'
            name = org.get('name') or org.get('phone') or org.get('role')
            obs['contacts'].append({'contact_key': _fingerprint((org.get('inn') or name) + role), 'name': name,
                'role': role, 'scope': 'city_directory' if city_scope else 'house', 'phone_raw': org.get('phone'),
                'dispatch_phone_raw': org.get('dispatch_phone'), 'email': org.get('email'), 'office_address': org.get('address'),
                'website': org.get('website'), 'inn': org.get('inn'), 'ogrn': org.get('ogrn'),
                'relationship_source_url': None if city_scope else url, 'contract_start_reported': org.get('contract_start_claimed'),
                'relationship_status': 'not_house_specific' if city_scope else 'unresolved_conflict' if conflict else 'published_claim',
                'conflict_key': 'management' if conflict else None,
                'conflict_note': 'Источники называют разные организации; действующий договор не подтверждён.' if conflict else None,
                'raw_json': {'organization': org, 'conflicts': management.get('conflicts', []) if conflict else []}})
    for check in management.get('source_checks', []):
        # Failed primary verification remains visible alongside secondary claims.
        if _status(check) != 'ok':
            receipt(check['url'], 'contacts', check=check)
    outages = house.get('outages', {})
    for check in outages.get('source_checks', []):
        obs = receipt(check['url'], 'outages', check=check)
        obs['source']['coverage_json'] = {k: v for k, v in outages.items() if k not in ('events', 'source_checks')}
    for event in outages.get('events', []):
        obs = receipt(event.get('history_page_url') or outages['source_url'], 'outages')
        obs['events'].append({'event_key': 'vl:blackout:' + str(event['source_event_id']), 'kind': 'outage',
            'source_event_id': str(event['source_event_id']), 'title': event.get('reason') or event.get('resource') or 'Публикация об отключении',
            'resource': event.get('resource'), 'organization_name': event.get('organization_as_published'),
            'status_reported': event.get('status_as_published'), 'period_text': event.get('period_as_published'),
            'source_url': event['source_url'], 'published_at': event.get('published_at'), 'raw_json': event})
    repairs = house.get('repairs', {})
    for work in repairs.get('works', []):
        execution = work.get('source_type') == 'execution_page'
        short = work.get('status_reported') == 'scheduled_in_short_term_plan'
        family = 'fkr:execution:' + str(work.get('planned_period')) if execution else 'fkr:short:2026-2028:form2' if short else 'fkr:regional-programme'
        obs = receipt(work['source_url'], 'repairs', work, key=family)
        if obs['source']['fetch_status'] != 'ok':
            continue
        period = str(work.get('planned_period') or '')
        years = re.findall(r'\b(?:19|20|21)\d{2}\b', period) if re.fullmatch(r'\s*\d{4}(?:\s*-\s*\d{4})?\s*', period) else []
        status = work.get('status_reported')
        complete = 1 if execution and isinstance(status, str) and status.startswith('Работы выполнены') else None
        event_key = family + ':' + (str(work.get('source_house_id')) + ':' if execution else (period + ':' if short else '')) + _fingerprint(work['kind'])
        obs['events'].append({'event_key': event_key, 'kind': 'repair_execution' if execution else 'repair_plan',
            'title': work['kind'], 'status_reported': status, 'period_text': period or None,
            'year_from': int(years[0]) if years else None, 'year_to': int(years[-1]) if years else None,
            'completion_reported': complete, 'source_url': work['source_url'],
            'source_locator': 'row=' + str(work.get('source_row')) + '; column=' + str(work.get('source_column')),
            'raw_json': work})
    for check in repairs.get('execution_checks', []):
        for attempt in ([check] if check.get('source_url') else check.get('source_checks', [])):
            obs = receipt(attempt.get('source_url') or attempt['url'], 'repairs', check=attempt, key='fkr:execution:' + str(check['year']))
            obs['source']['coverage_json'] = {'collection_notes': repairs.get('coverage_note')}
    coords = house.get('map_coordinates', {})
    if coords.get('source_url') and coords.get('fetched_at'):
        obs = receipt(coords['source_url'], 'coordinates', coords, key='vl:coordinates', check={})
        obs['source']['raw_json']['coordinates'] = coords
        pair = coords.get('coordinates')
        if coords.get('status') == 'found' and isinstance(pair, dict):
            obs['facts'] = {'lat': pair.get('lat'), 'lon': pair.get('lon'), 'coordinates_precision': coords.get('coordinates_precision'), 'raw_json': coords}
        else:
            obs['source']['fetch_status'] = 'not_found'
            obs['source']['match_status'] = 'unmatched'
    return list(observations.values())


def import_house_passports(conn, bundle):
    if not isinstance(bundle, dict) or bundle.get('schema_version') != 1 or not isinstance(bundle.get('houses'), list):
        raise ValueError('Expected house snapshot schema_version=1')
    keys, identities, offer_keys, normalized = set(), set(), set(), []
    for h in bundle['houses']:
        key = h.get('building_key')
        identity = normalize_house_address(h.get('city'), h.get('address'))
        if not isinstance(key, str) or not key or key in keys or identity is None or identity in identities:
            raise ValueError('Invalid, duplicate or unsupported house identity')
        keys.add(key); identities.add(identity)
        if not isinstance(h.get('offers'), list):
            raise ValueError('Expected offer references')
        for offer in h['offers']:
            offer_key = (offer.get('source'), offer.get('source_id'))
            if any(not isinstance(part, str) or not part for part in offer_key) or offer_key in offer_keys:
                raise ValueError('Invalid or duplicate offer reference')
            offer_keys.add(offer_key)
        mapping = h.get('vl_match', {})
        if mapping.get('match_status') != 'matched' or normalize_house_address(h['city'], mapping.get('matched_address')) != identity:
            raise ValueError('House address is not an exact source match')
        for block_name in ('management', 'outages', 'repairs', 'map_coordinates'):
            block = h.get(block_name, {})
            if block.get('building_key', key) != key:
                raise ValueError('Nested building key mismatch')
            for field in ('requested_address', 'input_address', 'matched_address'):
                if block.get(field) and normalize_house_address(h['city'], block[field]) != identity:
                    raise ValueError('Nested source address mismatch')
        normalized.append((h, identity, _observations(h)))
    report = {'schema_version': 1, 'bundle_sha256': hashlib.sha256(repo._json(bundle).encode()).hexdigest(),
              'houses': 0, 'links': 0, 'unlinked': [], 'observations_added': 0, 'observations_replayed': 0,
              'event_observations': 0, 'source_gaps': 0, 'management_conflicts': 0}
    with conn:
        for house, identity, observations in normalized:
            # Use the actual source observation clock, never the import clock as freshness.
            timestamp = min(o['source']['checked_at'] for o in observations)
            hid = repo.ensure_house(conn, identity, address_display=house['address'], created_at=timestamp)
            report['houses'] += 1
            for offer in house.get('offers', []):
                key = (offer.get('source'), offer.get('source_id'))
                existing = conn.execute('SELECT address FROM offers WHERE source=? AND source_id=?', key).fetchone()
                if existing is None:
                    report['unlinked'].append({'source': key[0], 'source_id': key[1], 'reason': 'offer_missing'})
                    continue
                exact = normalize_house_address(house['city'], existing['address']) == identity
                repo.link_offer_house(conn, *key, hid if exact else None, match_status='exact' if exact else 'unmatched',
                    offer_address_raw=existing['address'], match_basis='exact_normalized_street_number_suffix_corpus',
                    evidence_url=house['vl_match'].get('building_url'), checked_at=timestamp,
                    note=None if exact else 'Адрес объявления не совпадает со снимком дома')
                report['links'] += int(exact)
                if not exact:
                    report['unlinked'].append({'source': key[0], 'source_id': key[1], 'reason': 'address_mismatch'})
            for obs in observations:
                prior = conn.execute('SELECT 1 FROM house_sources WHERE house_id=? AND source_key=? AND checked_at=?',
                                     (hid, obs['source']['source_key'], repo._time(obs['source']['checked_at']))).fetchone()
                repo.store_house_observation(conn, hid, obs)
                report['observations_replayed' if prior else 'observations_added'] += 1
                report['event_observations'] += len(obs['events'])
                report['source_gaps'] += obs['source']['fetch_status'] not in ('ok', 'empty')
            report['management_conflicts'] += bool(house.get('management', {}).get('conflicts'))
    return report
