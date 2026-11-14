import copy
import json
from pathlib import Path

import pytest

from flats import db, importer
from flats.house_identity import normalize_house_address as address
from flats.house_import import import_house_passports, load_house_bundle
from flats.repo_houses import (ensure_house, get_house_passport, house_links_for_offers,
                               init_house_db, store_house_observation)

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = next((ROOT / 'research/house-passports/2026-11-14').glob('*.zip'))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / 'houses.db')
    db.init_db(c)
    init_house_db(c)
    importer.run_import(c, importer.load_json(ROOT / 'flats/demo/metrapi_vladivostok.json'))
    yield c
    c.close()


@pytest.fixture
def bundle():
    return load_house_bundle(ARCHIVE)


def legacy(conn):
    return {table: [tuple(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in ('offers', 'import_runs', 'photo_features', 'search_profile', 'decisions')}


def test_real_snapshot_counts_and_exact_replay(conn, bundle):
    before = legacy(conn)
    first = import_house_passports(conn, bundle)
    assert (first['houses'], first['links'], first['management_conflicts']) == (48, 50, 3)
    assert first['unlinked'] == []
    assert conn.execute('SELECT COUNT(*) FROM house_events WHERE kind="outage"').fetchone()[0] == 3005
    executions = conn.execute('SELECT completion_reported FROM house_events WHERE kind="repair_execution"').fetchall()
    assert len(executions) == 37
    assert sum(r[0] == 1 for r in executions) == 28
    assert sum(r[0] is None for r in executions) == 9
    counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
              for t in ('houses', 'house_sources', 'house_contacts', 'house_events')}
    second = import_house_passports(conn, bundle)
    assert second['observations_added'] == 0
    assert second['observations_replayed'] == first['observations_added']
    assert {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in counts} == counts
    assert legacy(conn) == before
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_addresses_preserve_suffix_corpus_slash_and_street_aliases(bundle):
    identities = {address(h['city'], h['address']) for h in bundle['houses']}
    assert None not in identities and len(identities) == 48
    for h in bundle['houses']:
        assert address(h['city'], h['address']) == address(h['city'], h['vl_match']['matched_address'])
    assert len({address('Владивосток', 'улица Ленина ' + n) for n in ('12', '12а', '12a', '12/1', '12 корпус 1', '12 корпус 2', '12 строение 1')}) == 7
    assert address('Владивосток', 'улица Поселковая 3-я 22 корпус 1') == address('Владивосток', 'ул. 3-я Поселковая 22 кор. 1')
    assert address('Владивосток', 'непонятный адрес') is None


def test_bundle_rejects_mismatched_nested_house_and_mapping_before_writes(conn, bundle):
    bad = copy.deepcopy(bundle)
    bad['houses'][1]['outages']['building_key'] = 'B999'
    with pytest.raises(ValueError):
        import_house_passports(conn, bad)
    assert conn.execute('SELECT COUNT(*) FROM houses').fetchone()[0] == 0
    bad = copy.deepcopy(bundle)
    bad['houses'][0]['vl_match']['matched_address'] = 'ул. Ленина 999'
    with pytest.raises(ValueError):
        import_house_passports(conn, bad)


def test_immutable_payload_rejects_edits_and_import_rolls_back(conn, bundle):
    import_house_passports(conn, bundle)
    count = conn.execute('SELECT COUNT(*) FROM house_sources').fetchone()[0]
    bad = copy.deepcopy(bundle)
    bad['houses'][-1]['management']['organizations'][0]['phone'] = 'edited fixture'
    with pytest.raises(ValueError, match='Immutable'):
        import_house_passports(conn, bad)
    assert conn.execute('SELECT COUNT(*) FROM house_sources').fetchone()[0] == count


def test_failed_observation_rolls_back_whole_import(conn, bundle, monkeypatch):
    from flats import repo_houses
    original = repo_houses.store_house_observation
    counter = [0]
    def fail(c, hid, obs):
        counter[0] += 1
        if counter[0] == 10:
            raise ValueError('injected failure')
        return original(c, hid, obs)
    monkeypatch.setattr(repo_houses, 'store_house_observation', fail)
    before = legacy(conn)
    with pytest.raises(ValueError, match='injected'):
        import_house_passports(conn, bundle)
    assert conn.execute('SELECT COUNT(*) FROM houses').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM house_sources').fetchone()[0] == 0
    assert legacy(conn) == before


def test_stale_links_hide_house_and_conflicts_are_visible(conn, bundle):
    import_house_passports(conn, bundle)
    key = ('farpost', '133892276')
    link = house_links_for_offers(conn, [key])[key]
    assert link['status'] == 'exact'
    assert link['summary']['management_status'] == 'conflict_unresolved'
    passport = get_house_passport(conn, link['house_id'])
    managers = [c for c in passport['contacts'] if c['role'] == 'management']
    assert len(managers) == 2
    assert all(c['relationship_status'] == 'unresolved_conflict' for c in managers)
    assert passport['coordinates']['source_url'].startswith('https://www.vl.ru/')
    conn.execute('UPDATE offers SET address=? WHERE source=? AND source_id=?', ('улица Ленина 1', *key))
    stale = house_links_for_offers(conn, [key])[key]
    assert stale['status'] == 'stale' and stale['house_id'] is None and stale['summary'] is None


def observation(**overrides):
    source = {'source_key':'fixture:facts', 'provider':'fixture', 'section':'facts',
              'source_url':'https://example.org/house/1', 'source_type':'fixture',
              'matched_address':'улица Ленина 1', 'match_status':'exact',
              'checked_at':'2026-11-13T12:00:00+00:00', 'fetched_at':'2026-11-13T12:00:00+00:00',
              'fetch_status':'ok', 'raw_json':{}, 'coverage_json':{}}
    source.update(overrides)
    return {'source':source, 'facts':{'commissioning_year':2000, 'raw_json':{}}, 'contacts':[], 'events':[]}


def test_latest_failure_preserves_success_and_writer_does_not_commit(conn):
    hid = ensure_house(conn, address('Владивосток','улица Ленина 1'), address_display='улица Ленина 1', created_at='2026-11-13T00:00:00+00:00')
    conn.commit()
    store_house_observation(conn, hid, observation())
    assert conn.in_transaction
    conn.commit()
    failed = observation(checked_at='2026-11-14T12:00:00+00:00', fetched_at=None, fetch_status='error', match_status='unmatched', error='HTTP 503')
    failed['facts'] = None
    store_house_observation(conn, hid, failed)
    conn.commit()
    passport = get_house_passport(conn,hid)
    assert passport['facts'][0]['commissioning_year'] == 2000
    assert passport['facts'][0]['source']['fetched_at'] == '2026-11-13T12:00:00+00:00'
    assert passport['sources']['latest_attempts'][0]['fetch_status'] == 'error'
    assert passport['sources']['last_successful'][0]['fetch_status'] == 'ok'


def test_coordinate_only_source_does_not_render_an_empty_facts_table(conn):
    from flats.web import create_app
    hid = ensure_house(conn, address('Владивосток', 'улица Ленина 1'),
                       address_display='улица Ленина 1', created_at='2026-11-13T00:00:00+00:00')
    coordinates = observation(section='coordinates')
    coordinates['facts'] = {'lat': 43.1, 'lon': 131.9, 'raw_json': {}}
    store_house_observation(conn, hid, coordinates)
    conn.commit()
    path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
    html = create_app(path).test_client().get(f'/house/{hid}').get_data(as_text=True)
    assert 'Характеристики пока не найдены в проверенных источниках.' in html
    assert '<table class="house-facts-table">' not in html
    assert '43.1, 131.9' in html


@pytest.mark.parametrize('status,label', [
    ('error', 'Не удалось получить сведения из источника; актуальность не проверена'),
    ('not_found', 'Запись по этому дому не найдена в источнике'),
])
def test_source_status_is_readable_without_changing_original_diagnostics(conn, status, label):
    from flats.web import create_app
    hid = ensure_house(conn, address('Владивосток', 'улица Ленина 1'),
                       address_display='улица Ленина 1', created_at='2026-11-13T00:00:00+00:00')
    failed = observation(fetch_status=status, error='Original provider diagnostic', fetched_at=None)
    failed['facts'] = None
    store_house_observation(conn, hid, failed)
    conn.commit()
    passport = get_house_passport(conn, hid)
    assert passport['issues'][0]['message'] == 'Характеристики дома: ' + label + '.'
    source = passport['sources']['latest_attempts'][0]
    assert source['fetch_status'] == status and source['fetch_status_label'] == label
    assert source['error'] == 'Original provider diagnostic'
    assert conn.execute('SELECT error FROM house_sources').fetchone()[0] == 'Original provider diagnostic'
    path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
    html = create_app(path).test_client().get(f'/house/{hid}').get_data(as_text=True)
    assert 'fixture · Характеристики дома · ' + label in html
    assert ' · facts · ' not in html and f' · {status} · ' not in html
    assert 'Подробности ответа источника' in html and 'Original provider diagnostic' in html


def test_no_guessed_outage_years_or_completion_flags(conn,bundle):
    import_house_passports(conn,bundle)
    assert conn.execute('SELECT COUNT(*) FROM house_events WHERE kind="outage" AND (starts_at IS NOT NULL OR ends_at IS NOT NULL OR year_from IS NOT NULL)').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM house_events WHERE kind<>"repair_execution" AND completion_reported IS NOT NULL').fetchone()[0] == 0


def test_unknown_schema_and_bad_json(conn,tmp_path):
    with pytest.raises(ValueError):
        import_house_passports(conn,{'schema_version':2,'houses':[]})
    p=tmp_path/'bad.json';p.write_text('{')
    with pytest.raises(ValueError):
        load_house_bundle(p)


def test_invalid_child_is_atomic_and_unsafe_source_url_rejected(conn):
    hid=ensure_house(conn,address('Владивосток','улица Ленина 1'),address_display='улица Ленина 1',created_at='2026-11-13T00:00:00+00:00')
    conn.commit()
    bad=observation();bad['facts']['lat']=100;bad['facts']['lon']=132
    with pytest.raises(Exception):
        store_house_observation(conn,hid,bad)
    assert conn.execute('SELECT COUNT(*) FROM house_sources').fetchone()[0] == 0
    bad=observation(source_url='javascript:alert(1)')
    with pytest.raises(ValueError):
        store_house_observation(conn,hid,bad)


def test_house_web_and_pagination(conn,bundle):
    import_house_passports(conn,bundle)
    from flats.web import create_app
    path=Path(conn.execute('PRAGMA database_list').fetchone()[2])
    app=create_app(path);app.config.update(TESTING=True)
    if 'houses' not in app.blueprints:
        from flats.web.houses import bp
        app.register_blueprint(bp)
    client=app.test_client()
    response=client.get('/house/for-offer/farpost/133892276')
    assert response.status_code == 302
    page=client.get(response.location)
    assert page.status_code == 200
    text=page.get_data(as_text=True)
    assert 'ТЕХНОЛОГИЯ КОМФОРТА' in text and 'Технология управления' in text
    assert 'Это не число отключений за год' in text
    assert client.get(response.location+'?outage_page=2').status_code == 200
    assert client.get(response.location+'?outage_page=0').status_code == 400
    assert client.get('/house/999999').status_code == 404


def test_flat_embeds_only_confirmed_house_passport(conn,bundle):
    from html.parser import HTMLParser
    from urllib.parse import quote
    from flats.web import create_app
    import_house_passports(conn,bundle)
    offer=conn.execute("SELECT group_id FROM offers WHERE source='farpost' AND source_id='133892276'").fetchone()
    key=offer['group_id'] or 'farpost:133892276'
    url='/flat/'+quote(key,safe='')
    app=create_app(Path(conn.execute('PRAGMA database_list').fetchone()[2]));app.testing=True
    client=app.test_client()
    html=client.get(url).get_data(as_text=True)
    class IDs(HTMLParser):
        def __init__(self):
            super().__init__(); self.ids=[]
        def handle_starttag(self, tag, attrs):
            self.ids.extend(value for name,value in attrs if name=='id')
    parser=IDs();parser.feed(html)
    assert len(parser.ids)==len(set(parser.ids))
    assert 'ТЕХНОЛОГИЯ КОМФОРТА' in html and 'Технология управления' in html
    assert 'Характеристики дома' in html and 'Источники и даты проверок' in html
    assert 'Паспорт дома →</a>' in html and 'href="#house"' in html
    assert f'{url}?outage_page=2#outages' in html
    second=client.get(url+'?outage_page=2')
    assert second.status_code==200
    assert '<details open><summary>Публикации об отключениях' in second.get_data(as_text=True)
    first=client.get(url+'?outage_page=1')
    assert first.status_code==200
    assert '<details open><summary>Публикации об отключениях' in first.get_data(as_text=True)
    assert client.get(url+'?outage_page=0').status_code==400
    conn.execute("UPDATE offers SET address='ул. Другой адрес 999' WHERE source='farpost' AND source_id='133892276'")
    conn.commit()
    stale=client.get(url).get_data(as_text=True)
    assert 'ТЕХНОЛОГИЯ КОМФОРТА' not in stale
    assert 'повторная привязка' in stale or 'не подтверждено' in stale


def test_map_uses_same_eligible_rows_and_shared_house(conn, bundle):
    import re
    from flats.web import create_app
    import_house_passports(conn, bundle)
    app=create_app(Path(conn.execute('PRAGMA database_list').fetchone()['file']))
    html=app.test_client().get('/').get_data(as_text=True)
    pins=re.findall(r'data-house-id="(\d+)"',html)
    assert len(pins)==50 and len(set(pins))==48
    filtered=app.test_client().get('/?apply=1&monthly_max=40000').get_data(as_text=True)
    assert len(re.findall(r'data-house-id="(\d+)"',filtered)) < len(pins)


def test_nested_wrong_address_cannot_attach_evidence(conn,bundle):
    bad=copy.deepcopy(bundle)
    bad['houses'][0]['management']['matched_address']='ул. Ленина 99'
    with pytest.raises(ValueError,match='Nested source address mismatch'):
        import_house_passports(conn,bad)
    assert conn.execute('SELECT COUNT(*) FROM houses').fetchone()[0] == 0


def test_cli_imports_supplied_archive(tmp_path,monkeypatch,capsys):
    from flats.cli import main
    monkeypatch.setenv('FLATS_DB',str(tmp_path/'cli.db'))
    assert main(['demo']) == 0
    capsys.readouterr()
    assert main(['import-house-passports',str(ARCHIVE)]) == 0
    report=json.loads(capsys.readouterr().out)
    assert report['houses']==48 and report['links']==50
    assert main(['import-house-passports',str(ARCHIVE)]) == 0
    assert json.loads(capsys.readouterr().out)['observations_added']==0


def test_duplicate_offer_cannot_replace_an_exact_house_link(conn,bundle):
    bad=copy.deepcopy(bundle)
    bad['houses'][1]['offers'].append(copy.deepcopy(bad['houses'][0]['offers'][0]))
    with pytest.raises(ValueError,match='duplicate offer'):
        import_house_passports(conn,bad)
    assert conn.execute('SELECT COUNT(*) FROM houses').fetchone()[0]==0


def test_map_preview_data_stays_with_its_offer(conn, bundle):
    from html.parser import HTMLParser
    from flats.web import create_app

    import_house_passports(conn, bundle)
    row = conn.execute("SELECT o.source, o.source_id, o.group_id FROM offer_houses l JOIN offers o ON o.source=l.source AND o.source_id=l.source_id WHERE l.match_status IN ('exact', 'manual') LIMIT 1").fetchone()
    conn.execute("UPDATE offers SET price=42000, rooms=2, area_total=51, floor=3, floors_total=9, photos_json=? WHERE source=? AND source_id=?",
                 (json.dumps(['https://images.example/cover.jpg']), row['source'], row['source_id']))
    conn.commit()
    class Points(HTMLParser):
        def __init__(self):
            super().__init__()
            self.points = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'data-map-point' in attrs:
                self.points.append(attrs)
    parser = Points()
    app = create_app(Path(conn.execute('PRAGMA database_list').fetchone()['file']))
    parser.feed(app.test_client().get('/').get_data(as_text=True))
    point = next(p for p in parser.points if p['data-key'] == (row['group_id'] or f"{row['source']}:{row['source_id']}"))
    assert point['data-photo'] == 'https://images.example/cover.jpg'
    assert point['data-price'] == '42 000 ₽'
    assert point['data-rooms'] == '2-комн.'
    assert point['data-area'] == '51.0 м²'
    assert point['data-floor'] == '3/9 эт.'
