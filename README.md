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

Visual interior search (optional, CPU is enough, ~12 min for 800 photos):

```bash
pip install -e '.[ml]'
flats embed            # downloads photos to data/photos and the SigLIP 2 model
```

Then open a flat, click «Найти похожие интерьеры» under a photo: the list is re-ordered by visual similarity among flats that pass the filters.

Open http://127.0.0.1:5000: search, flat page, «Избранное» (compare up to 5), «Свайпы». The database file is `data/flats.db` (override with `FLATS_DB`).

## Refresh real data

Needs a Metrapi API key. Use the official API only: their terms forbid scraping the website.

```bash
METRAPI_KEY=... python scripts/fetch_metrapi.py --limit 50
flats demo
```

## Limitations

Visual similarity matches room type well, style matching is not verified (`docs/ml-check.md`). Photos load from the source site and disappear when a listing is removed. Plan and contracts: `docs/`.
