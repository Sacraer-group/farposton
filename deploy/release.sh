#!/bin/bash
set -euo pipefail
revision=${1:?revision required}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || exit 2
exec 9>/home/tech/bereg/deploy.lock
flock 9
cd /home/tech/bereg
image="bereg:$revision"
sudo -n docker image inspect "$image" >/dev/null
current=$(cat active-slot 2>/dev/null || true)
slot=blue; port=5001
if [[ "$current" == blue ]]; then slot=green;port=5002;fi
name="bereg-$slot"
# Snapshot before additive migrations; retain database and previous running image.
sudo -n docker run --rm --user 0 -v bereg-data:/data -v /home/tech/bereg/backups:/backup --entrypoint python "$image" -c "import sqlite3; a=sqlite3.connect('/data/flats.db');b=sqlite3.connect('/backup/pre-$revision.db');a.backup(b)"
sudo -n chown tech:tech "/home/tech/bereg/backups/pre-$revision.db"
sudo -n chmod 600 "/home/tech/bereg/backups/pre-$revision.db"
sudo -n docker run --rm -v bereg-data:/data "$image" flats import-house-passports /app/research/house-passports/2026-11-14/farposton-house-data-2026-11-14.zip
sudo -n docker rm -f "$name" >/dev/null 2>&1 || true
sudo -n docker run -d --name "$name" --restart unless-stopped --init --env-file /home/tech/bereg/.env -e FLATS_DB=/data/flats.db -e FLATS_PUBLIC_DEMO=1 -e FLATS_SECURE_COOKIE=1 -e FLATS_TRUST_CADDY=1 -e FLATS_2GIS_KEY_FILE=/run/secrets/2gis_api_key -v bereg-data:/data -v /home/tech/.config/bereg/2gis_api_key:/run/secrets/2gis_api_key:ro -p "127.0.0.1:$port:5000" "$image" >/dev/null
ready=0
for attempt in {1..20}; do
 if curl -fsS "http://127.0.0.1:$port/health" >/dev/null;then ready=1;break;fi
 sleep 1
done
if [[ "$ready" != 1 ]];then sudo -n docker logs --tail 30 "$name";exit 1;fi
cp /etc/caddy/Caddyfile "/home/tech/bereg/backups/Caddyfile-pre-$revision"
sudo -n python3 - "$port" <<'PY'
import sys,re
from pathlib import Path
p=Path('/etc/caddy/Caddyfile');s=p.read_text()
s,n=re.subn(r'(reverse_proxy 127\.0\.0\.1:)(5000|5001|5002)',lambda m:m[1]+sys.argv[1],s)
if n!=1:raise SystemExit('Expected exactly one existing rental upstream')
p.write_text(s)
PY
if ! sudo -n caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 || ! sudo -n systemctl reload caddy;then
 sudo -n cp "/home/tech/bereg/backups/Caddyfile-pre-$revision" /etc/caddy/Caddyfile
 sudo -n systemctl reload caddy
 exit 1
fi
if ! curl -fsS https://farposton.renosaza.com/health >/dev/null;then
 sudo -n cp "/home/tech/bereg/backups/Caddyfile-pre-$revision" /etc/caddy/Caddyfile
 sudo -n systemctl reload caddy
 exit 1
fi
printf '%s\n' "$slot" > active-slot
printf '%s\n' "$revision" > deployed-commit
printf 'Released %s at https://farposton.renosaza.com\n' "$revision"
