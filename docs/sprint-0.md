# Sprint 0 — skeleton, schema, source probe

Goal: an installable empty package, a database schema, and a real sample of the source.

Tasks run in parallel. No shared files.

## S0-T1 Project skeleton

- **Owns:** `pyproject.toml`, `.gitignore`, `flats/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`
- **Steps:**
  1. `pyproject.toml` (setuptools): name `flats`, Python `>=3.11`, deps `flask`, `requests`, `numpy`; extra `dev = ["pytest"]`; extra `ml = ["torch", "transformers", "pillow"]`; script `flats = "flats.cli:main"`.
  2. `.gitignore`: `.venv/`, `__pycache__/`, `*.egg-info/`, `data/`, `.env`, `.pytest_cache/`.
  3. `flats/__init__.py` empty. `tests/test_smoke.py`: `import flats`.
- **Accept:** `pip install -e '.[dev]' && pytest -q` -> 1 passed.

## S0-T2 Config, types, DB

- **Owns:** `flats/config.py`, `flats/types.py`, `flats/db.py`, `flats/schema.sql`, `tests/test_db.py`
- **Steps:**
  1. Copy schema from contracts §2 into `flats/schema.sql`.
  2. Copy dataclasses from contracts §3 into `flats/types.py` (include `SearchRow`).
  3. `config.py` and `db.py` per contracts §4. `init_db` reads `schema.sql` via `importlib.resources` or `Path(__file__).with_name`. `connect` creates parent dir.
  4. Test: `init_db` on `tmp_path` DB creates all 5 tables; calling twice does not fail; `metrapi_key()` raises without env var.
- **Accept:** `pytest -q tests/test_db.py` passes. (Needs S0-T1 package; if not merged yet, run with `PYTHONPATH=.`.)

## S0-T3 Source probe (needs a real `METRAPI_KEY`)

- **Owns:** `scripts/probe_source.py`, `docs/source-sample.md`
- **Steps:**
  1. Script (stdlib `urllib` or `requests`, no package import): one call `GET https://api.metrapi.ru/v1/items?city=vladivostok&deal_type=rent&rent_period=long&realty_type=flat&limit=50` with header `X-Api-Key` from env. Print `count`, `next_cursor` presence, and for each of these fields the fill rate and up to 10 distinct values: `price, rooms, district, deposit, commission, prepayment, rent_period, utilities_payer, pets_allowed, group_id, group_spread_m, photos (count), floor, floors_total, area_total, source`.
  2. Write `docs/source-sample.md`: date, command, the printed table, distinct raw strings of `deposit`/`commission`/`prepayment`/`utilities_payer`/`pets_allowed`, how many offers share a `group_id`, and whether photo URLs open (try 3 with HEAD).
  3. Never write the key or full raw JSON with phones into the doc.
- **Accept:** `docs/source-sample.md` exists with real observed values and the date. If no key is available: write "blocked: no METRAPI_KEY" in the doc and stop.

## Sprint check

`pytest -q` passes; `docs/source-sample.md` reviewed. If its raw strings contradict contracts §5 money table, the planner updates `docs/contracts.md` before Sprint 1.
