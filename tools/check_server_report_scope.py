"""Read-only acceptance of installed report routes; never authenticates or mutates users."""
import asyncio
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'src')]
from dotenv import load_dotenv
load_dotenv(root / '.env')
os.environ['PROGNOZ_1C_WRITE'] = '0'
os.environ['WEB_1C_WRITE'] = '0'
os.environ['PROGNOZ_1C_INGEST'] = '0'
from starlette.requests import Request
from web import routes_reports

users = json.loads(os.environ['WEB_USERS_JSON'])
if isinstance(users, dict):
    entries = [(key, value) for key, value in users.items()]
else:
    entries = [(value['username'], value) for value in users]
counts = []
for username, user in entries:
    role = user.get('role')
    if role not in {'admin', 'manager'}:
        continue
    request = Request({'type':'http', 'method':'GET', 'path':'/api/reports/latest',
                       'headers':[], 'query_string':b'', 'session':{
                           'username':username,'role':role,'point_key':user.get('point_key')}})
    response = asyncio.run(routes_reports.api_reports_latest(request))
    payload = json.loads(response.body)
    points = payload['json']['points']
    if role == 'manager':
        assert len(points) == 1, 'Manager has more than one point'
        assert user['point_key'] == points[0]['point_key'] or user['point_key'] in points[0].get('account_point_keys', [])
    else:
        assert len(points) == 6, 'Administrator must have six points'
        assert any('бульвар' in p['name'].casefold() for p in points), 'ZB missing'
    counts.append({'role':role,'point_count':len(points)})
assert any(row['role']=='admin' for row in counts)
assert sum(row['role']=='manager' for row in counts) >= 6
print(json.dumps({'scope_checks':counts,'status':'passed'},ensure_ascii=False))
