# Flats — Vladivostok long-term rental shortlist

Local study app: filter long-term rentals in Vladivostok, see clear monthly and move-in costs per offer, and the questions to ask before a viewing.

## Run (Python 3.11+)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
flats init-db
flats demo                      # 50 real offers (Metrapi snapshot, 2026-11-13)
flats demo --set invented       # optional: 26 invented edge-case offers
flask --app flats.web:create_app run --host 127.0.0.1
pytest -q
```

Open http://127.0.0.1:5000. The database file is `data/flats.db` (override with `FLATS_DB`).

## Refresh real data

Needs a Metrapi API key. Use the official API only: their terms forbid scraping the website.

```bash
METRAPI_KEY=... python scripts/fetch_metrapi.py --limit 50
flats demo
```

## Limitations

Favorites/compare pages, visual interior search and swipes are not built yet. Photos load from the source site and disappear when a listing is removed. Plan and contracts: `docs/`.
