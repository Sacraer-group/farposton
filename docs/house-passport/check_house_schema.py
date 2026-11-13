"""Offline check of proposed DDL, never an application or production migration."""
import hashlib
import json
import platform
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ARCH = Path(__file__).resolve().parent
BASE = ARCH / 'reference_existing_schema.sql'
ADDITION = ARCH / 'house_schema.sql'
LEGACY = ('import_runs', 'offers', 'photo_features', 'search_profile', 'decisions')
NEW = {'houses', 'offer_houses', 'house_sources', 'house_facts', 'house_contacts', 'house_events'}

def legacy_snapshot(conn):
    return {table: {
        'ddl': conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0],
        'columns': conn.execute(f'PRAGMA table_info({table})').fetchall(),
        'rows': conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall(),
    } for table in LEGACY}

def rejects(conn, sql, args=()):
    try:
        conn.execute(sql, args)
    except sqlite3.IntegrityError:
        return
    raise AssertionError('Constraint did not reject invalid row')

with tempfile.TemporaryDirectory(prefix='sqlite_check_', dir=ARCH) as directory:
    conn = sqlite3.connect(Path(directory) / 'check.sqlite3')
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript(BASE.read_text())
    conn.execute("INSERT INTO import_runs VALUES (1,'2026-11-13T00:00:00Z','2026-11-13T00:01:00Z','ok',3,NULL)")
    # Same flat group can span offers. A second flat can be in the same house.
    for sid, group, address, price, deposit in [
        ('fixture-1', 'flat-a', 'улица Майора Филипова 11 кор. 1', 40000, '40 000 ₽'),
        ('fixture-2', 'flat-a', 'улица Майора Филипова 11 кор. 1', 41000, None),
        ('fixture-3', 'flat-b', 'улица Майора Филипова 11 кор. 1', 42000, 'Без залога'),
    ]:
        conn.execute('INSERT INTO offers (source,source_id,group_id,address,price,deposit_raw,raw_json,import_run_id) VALUES (?,?,?,?,?,?,?,1)', ('fixture',sid,group,address,price,deposit,'{}'))
    conn.executemany('INSERT INTO decisions (source,source_id,action,created_at,undone) VALUES (?,?,?,?,?)', [
        ('fixture','fixture-1','save','2026-11-13T00:02:00Z',0),
        ('fixture','fixture-2','skip','2026-11-13T00:03:00Z',1),
    ])
    conn.execute("INSERT INTO photo_features VALUES ('https://example.invalid/photo','fixture','fixture-1','test-hash','test-model',?)", (b'\x00\x01\x02',))
    conn.execute("INSERT INTO search_profile (id,monthly_max,rooms_json) VALUES (1,50000,'[1,2]')")
    conn.commit()
    before = legacy_snapshot(conn)
    conn.executescript(ADDITION.read_text())
    conn.executescript(ADDITION.read_text())
    assert legacy_snapshot(conn) == before
    tables = {x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables - set(LEGACY) == NEW

    for number, corpus in [('11','1'), ('11','2'), ('11/1','')]:
        conn.execute('INSERT INTO houses (city,street_type,street_name,house_number,corpus,address_display,created_at) VALUES (?,?,?,?,?,?,?)', ('владивосток','улица','майора филипова',number,corpus,f'Майора Филипова {number} корпус {corpus}','2026-11-13T00:05:00Z'))
    assert conn.execute('SELECT count(*) FROM houses').fetchone()[0] == 3
    conn.execute("INSERT INTO offer_houses (source,source_id,house_id,offer_address_raw,match_status,match_basis,checked_at) VALUES ('fixture','fixture-1',1,'улица Майора Филипова 11 кор. 1','exact','reviewed_exact_address','2026-11-13T00:05:00Z')")
    rejects(conn, "INSERT INTO offer_houses (source,source_id,house_id,match_status,match_basis,checked_at) VALUES ('fixture','absent-offer',1,'exact','fixture','2026-11-13T00:05:00Z')")
    rejects(conn, "INSERT INTO offer_houses (source,source_id,house_id,match_status,match_basis,checked_at) VALUES ('fixture','fixture-2',NULL,'exact','fixture','2026-11-13T00:05:00Z')")

    def receipt(key, provider, when, status, error=None, section='contacts'):
        return conn.execute('INSERT INTO house_sources (house_id,source_key,provider,section,source_url,source_type,matched_address,match_status,checked_at,fetched_at,recorded_at,fetch_status,error) VALUES (1,?,?,?,?,\'secondary_directory\',\'Майора Филипова 11 корпус 1\',\'exact\',?,?,?,?,?)', (key,provider,section,'https://example.invalid/'+key,when,when,when,status,error)).lastrowid

    vl = receipt('vl:contacts:52243','vl','2026-11-13T00:10:00Z','ok')
    dp = receipt('dompassport:contacts:11k1','dompassport','2026-11-13T00:11:00Z','ok')
    for sid, name in [(vl,'АО «Технология управления»'),(dp,'ООО УК «Технология комфорта»')]:
        conn.execute('INSERT INTO house_contacts (source_id,contact_key,name,role,scope,relationship_source_url,relationship_status,conflict_key) VALUES (?,?,?,\'management\',\'house\',\'https://example.invalid/house\',\'unresolved_conflict\',\'management:house-1\')', (sid,'manager',name))
    facts_source = receipt('vl:facts:52243','vl','2026-11-13T00:11:30Z','ok',section='facts')
    conn.execute('INSERT INTO house_facts (source_id,floors_text,central_heating_text) VALUES (?,NULL,NULL)', (facts_source,))
    failed = receipt('vl:contacts:52243','vl','2026-11-13T00:12:00Z','error','HTTP 503')
    assert failed != vl
    assert conn.execute('SELECT count(*) FROM house_contacts').fetchone()[0] == 2
    assert conn.execute('SELECT floors_text FROM house_facts WHERE source_id=?', (facts_source,)).fetchone()[0] is None
    rejects(conn, 'INSERT INTO house_facts (source_id,lat,lon) VALUES (?,43.1,NULL)', (dp,))
    rejects(conn, "INSERT INTO house_contacts (source_id,contact_key,name,role,scope,relationship_status) VALUES (?, 'bad','Bad','management','house','published_claim')", (failed,))
    outage_source = receipt('vl:outages:52243','vl','2026-11-13T00:13:00Z','ok',section='outages')
    conn.execute('INSERT INTO house_events (source_id,event_key,kind,title,period_text,source_url,raw_json) VALUES (?,\'blackout:169288\',\'outage\',\'Электричество\',\'с 2 ноя. 19:31 по 3 ноя. 00:00\',\'https://www.vl.ru/off/blackout/169288.html\',\'{}\')', (outage_source,))
    assert conn.execute('SELECT starts_at,ends_at,year_from,year_to,completion_reported FROM house_events').fetchone() == (None,None,None,None,None)
    conn.commit()
    added_before = {table: conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in NEW}
    conn.executescript(ADDITION.read_text())
    assert {table: conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in NEW} == added_before
    assert legacy_snapshot(conn) == before
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    counts = {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in LEGACY + tuple(sorted(NEW))}
    conn.close()

result = {
    'status':'passed', 'checked_at':datetime.now(timezone.utc).isoformat(),
    'python':platform.python_version(), 'sqlite':sqlite3.sqlite_version,
    'base_schema':str(BASE), 'base_schema_sha256':hashlib.sha256(BASE.read_bytes()).hexdigest(),
    'addition_sha256':hashlib.sha256(ADDITION.read_bytes()).hexdigest(),
    'migration_applications':3, 'added_tables':sorted(NEW), 'fixture_row_counts':counts,
    'checks':[
        'Original table DDL, columns and every seeded row unchanged after all applications',
        'Three offers, two decisions (including undone), one photo vector and search profile preserved',
        'Repeated migration preserved both legacy and new rows',
        'Different corpus and slash identities stored as different houses',
        'Foreign key rejects a link to a missing offer; exact match rejects NULL house',
        'Two conflicting management claims remain; later HTTP 503 receipt removes neither',
        'Unknown facts and omitted event years remain NULL',
        'Incomplete coordinate pairs and house contacts without relationship evidence rejected',
        'foreign_key_check empty; integrity_check ok',
    ],
    'not_implemented_or_tested':['Address parser','JSON importer','repository read helpers','HTTP fetchers','application routes'],
    'production_modified':False, 'temporary_database_removed':True,
}
(ARCH/'schema_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False,indent=2))
