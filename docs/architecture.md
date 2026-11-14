# Architecture

## Implemented baseline

| Feature | What is built |
| --- | --- |
| Search by conditions | Catalog with limits on monthly cost, move-in cost, rooms, districts, pets requirement |
| Clear flat card | Photos, main parameters, terms of each offer, known costs, unknown terms, source link |
| Similar interior search | User picks a photo; app re-orders already-eligible flats by visual similarity |
| Favorites and comparison | Save flats, compare up to 5 by the same fields, questions for missing data |
| Swipe mode | Save / skip / details / undo over the same list, filters and favorites |

## House and route integration

The house importer and repository expose the collected, dated snapshot to offer and comparison views and to the advisor. Weekly trip calculations are explicitly requested, and completed results may inform the advisor. House and route coverage can be incomplete. The earlier [house proposal](house-passport/README.md) is historical; see the [dataset](../research/house-passports/README.md) and [implementation status](implementation-status.md).

## Product direction and data boundary

The decision journey is requirements and priority → three candidates → source-backed house check and comparison → viewing brief and questions. The goal is to understand cash, house evidence and route uncertainty before visiting. Swipe is secondary. See [decision-flow.md](decision-flow.md).

Offer data and separately imported house records have different provenance. Missing house information must be shown as unavailable, not inferred. Offer eligibility is separate from verification of the house address; house records are dated snapshots, not live provider data.

## Out of scope

Fair price prediction; own parsers; own duplicate model; unrestricted AI chat and long reports; owner/fraud/defect checks; notifications, accounts, shared choice, mobile app; scheduler, Redis, vector DB, SPA, model server; uploading external reference photos; learning from swipes.

## Advisor direction

The bounded advisor is implemented. A deterministic text parser proposes explicit Russian-language filters with evidence snippets and notes for user review; the editable form can also be used directly. It reuses eligibility and offer costs, retrieves at most five actual candidates lexically, and can call an optional OpenAI-compatible chat/completions provider. The model selects supplied candidate, fact and question IDs; server-side rendering rejects unknown and cross-offer IDs. Without a configured provider the response is explicitly retrieval-based. No vector database is required. Implementation and verification are tracked in [state.md](state.md).

## Data flow

```mermaid
flowchart TD
    A["Metrapi API"] --> B["Import + normalization (CLI)"]
    B --> C[("SQLite")]
    B --> D["Photo embeddings (CLI)"]
    D --> C
    E["Search profile + chosen photo"] --> F["Eligibility + visual ranking"]
    C --> F
    F --> G["List or swipes"]
    G --> H["Save / skip / undo"]
    H --> J[("Browser localStorage")]
    J --> I["Comparison + questions"]
    C --> I
```

Two modes:

1. **Prepare data** (manual CLI commands): `flats demo`, `flats import-house-passports`, optionally `flats embed`.
2. **Use the app** (Flask server): filter ready data, view, save, compare.

## Modules and file ownership

Package name: `flats` (directory `flats/`). Each module has one owner task per sprint.

| Module | File | Job |
| --- | --- | --- |
| Config | `flats/config.py` | Read env vars (`METRAPI_KEY`, `FLATS_DB`, `FLATS_PHOTO_DIR`) |
| DB | `flats/db.py`, `flats/schema.sql` | Connect, create schema |
| Source loader | `flats/metrapi.py`, `scripts/fetch_metrapi.py` | One official API call; Metrapi item -> normalized offer dict |
| Money parsing | `flats/money.py` | Parse deposit/commission/prepayment strings into typed terms |
| Costs | `flats/costs.py` | Monthly and move-in cost of one offer |
| Eligibility | `flats/eligibility.py` | `match` / `unknown` / `reject` per offer and profile |
| Questions | `flats/questions.py` | Rule-based questions from missing or conflicting data |
| Import | `flats/importer.py` | Fetch, validate, upsert offers, record import run |
| Repositories | `flats/repo_offers.py`, `flats/repo_user.py` | SQL reads/writes |
| Grouping | `flats/grouping.py` | Offers -> flat cards by `group_id` |
| Search service | `flats/search.py` | Profile -> ordered list of flat cards with reasons |
| Embeddings | `flats/embed.py` | Download photos, compute vectors |
| Similarity | `flats/similar.py` | Cosine similarity with NumPy |
| CLI | `flats/cli.py` | `init-db`, `import-json`, `demo`, `embed` |
| Web | `flats/web/*.py`, `flats/templates/*`, `flats/static/*` | Pages and JSON actions |

## Stack

Python 3.11+, Flask (Jinja), `sqlite3` from stdlib, `requests`, NumPy; Transformers + PyTorch + Pillow only for sprint 4. Plain HTML/CSS/JS. Maps use the official 2GIS MapGL JS API, loaded only on map-bearing pages, with native vector maps, controls and attribution. `flats/static/map-sdk.js` shares initialization and failure handling between the catalog/detail/comparison maps and journey point picker. The browser-visible `FLATS_2GIS_MAP_KEY` requires Map Tiles API access; server routing credentials remain separate. MapGL requires network access and WebGL; address links and manual coordinates remain available when it fails. `pytest` and Node's built-in test runner cover the application.

## Key rules

- Costs are computed from **one offer**, never mixed across offers of one flat.
- Unknown is not zero. Unknown pets is not "allowed".
- Raw string is stored next to the parsed value.
- Totals are computed on read, not stored.
- Web decisions reference the offer (`source`, `source_id`), not the group. Save/skip history is kept in `localStorage` key `farposton.decisions.v1`; legacy SQLite decisions remain intact and are not imported. Storage events and `pageshow` refresh views.
- Favorites reveals browser-local saves among hidden public offer candidates and disables hidden comparison checkboxes. Comparison accepts up to five explicitly requested existing offer IDs without adding them to local storage.
- Advisor and `/api/trips/start` requests carry compact local decisions validated by `repo_user.validate_browser_decisions`, without persistence or database fallback. Advisor saved scope uses the whole saved set; explicit chosen IDs retain the five-item limit. Trip jobs exclude skipped groups and skipped sibling offers before spending API budget. House related lists also apply local decisions. Filters and sample photo continue to use visitor sessions in PUBLIC_DEMO and the existing single-user behavior in local mode.
- Visual search never brings back a `reject` flat.
- Failed import keeps the previous working data.
- A listing missing from a new import is not marked "rented".
