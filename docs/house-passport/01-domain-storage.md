# House identity and additive storage

**Status:** reviewable design, not an application implementation or a production migration. The proposed SQL has been executed only against a temporary SQLite database. House passports are now P0 by the user's instruction; that supersedes their exclusion in the old architecture document.

## Scope and ownership

Provide one source-backed house passport for each matched rental address and a separate link from an existing offer to that house. Store published building facts, management/service contacts, outages, repair plans and reported repair execution. Preserve disagreement, missing information, source failures and the dates actually available.

This design owns six new tables, conservative address identity, the house repository and its read contract. It does not change `Offer`, `FlatCard`, `SearchRow`, `group_id`, existing money calculations, decisions, photo features, the Metrapi importer or offer retention rules. No global company registry, ORM, EAV attribute store, event bus, scheduler or automatic complaint submission is needed. Utility bills are absent from this collection, so there is no bill table or fabricated monthly utility estimate.

Current deliverables owned here:

- `architecture/01-domain-storage.md`
- `architecture/house_schema.sql`
- `architecture/reference_existing_schema.sql`
- `architecture/check_house_schema.py`
- `architecture/schema_verification.json`

Future implementation ownership is divided into three non-overlapping tasks below. Ingestion owns its adapter and CLI wiring; web owns routes/templates/map rendering. Neither writes its own SQL repository or address matcher.

## Evidence from the existing application

The inspected source snapshot is `Sacraer-group/farposton` commit `a697ee5d3805af89bb162705dc5cdb4b933f7cef`. Relevant files are `docs/agent-rules.md`, `docs/contracts.md`, `docs/constraints.md`, `docs/architecture.md`, `flats/schema.sql`, `flats/types.py`, `flats/grouping.py`, `flats/repo_offers.py`, `flats/importer.py`, `flats/db.py` and `flats/web/__init__.py` in the adjacent `farposton_source` directory.

The actual schema keys an offer by `(source, source_id)`. `grouping.py` groups offers into a **flat**, using `group_id` or the offer key. `repo_offers.py` upserts the existing offer fields and reads them into the frozen `Offer` dataclass. `db.connect()` enables foreign keys; `db.init_db()` executes the packaged schema. These existing paths are sufficient: append the six tables to the schema during implementation and add a separate repository. No change to the existing dataclass or offer upsert is required.

The collected input has 50 offers, 48 exact VL address matches, 46 houses with a published management claim, three management conflicts and two missing management organizations. Official regional-programme characteristics match 44 houses. These are coverage figures from `summary.json` in the extracted bundle, not a claim of 48 complete or verified passports. The 3,005 published outage-to-house relations include older records and the same publication affecting several houses; they are not a last-year incident count.

## Inputs and outputs

### Input

The ingestion entry point accepts the frozen `house_passports.json` in the extracted bundle bundle:

```python
{
    "schema_version": 1,
    "manifest": {...},
    "summary": {...},
    "houses": [{
        "building_key": "B029", "city": "Владивосток", "address": "...",
        "address_raw_variants": [...], "offers": [...],
        "coordinates_from_listings": [...], "vl_match": {...},
        "management": {...}, "outages": {...}, "repairs": {...},
        "map_coordinates": {...}, "quality_flags": [...],
        "utility_bill_evidence": {...}
    }]
}
```

`parts/management.json`, `parts/outages.json`, `parts/repairs.json`, `parts/coordinates.json` and `parts/vl_house_mapping.json` provide the original per-section observations. Their source URLs, observation dates, statuses and raw values must survive the adapter. The consolidated file is the sole CLI input; the parts and raw snapshots are evidence, not a second competing import interface.

`building_key` is a locator within this bundle, not a durable house identifier. Record it with the bundle identifier in `house_sources.raw_json`. An offer referenced by the bundle must already exist in `offers`; an unmatched offer key is reported, not inserted by house ingestion.

### Output

- Independent `houses.id` values, stable on repeated import of the same normalized address.
- At most one current house link for each existing `(source, source_id)`.
- Immutable source observations and their fixed facts, contact assertions and event assertions.
- A read model for the passport, comparison and map, defined below.
- Explicit `unmatched`, `ambiguous`, `stale`, unavailable-source and unresolved-conflict results; none becomes a numerical zero or a positive safety statement.

## Six tables

The executable proposal is [house_schema.sql](house_schema.sql).

| Table | Meaning and boundaries |
| --- | --- |
| `houses` | Local address identity only. Unique normalized city, street type/name, house number, corpus and structure. No preferred manager or synthetic quality score. |
| `offer_houses` | Link by the existing composite offer key, with the address as it was checked, matching basis, evidence URL, candidates and status. It never uses `group_id` as a house key. |
| `house_sources` | One observation or failed attempt for a house and a logical source section; URL, provider, address match, distinct clocks, error, raw snapshot reference and coverage. |
| `house_facts` | Fixed nullable columns for facts and an address coordinate pair, attached to one source observation. A raw JSON copy preserves details that are not promoted to P0 columns. |
| `house_contacts` | A source's statement about an organization and its role, published contact strings, relationship evidence and unresolved conflict group. No inferred global organization merge. |
| `house_events` | A house-specific publication about an outage, a repair plan or reported repair execution. Original period text is retained alongside any actually known year/date. |

Facts, contacts and events reach the house through `house_sources.house_id`. This avoids repeating provenance columns on every event while retaining a separate source page or document location on the event itself. There is no need for an additional organizations table at this scale: matching companies by name alone would hide the current management conflicts.

SQLite foreign keys and checks enforce link existence, matched-link/house consistency, complete coordinate pairs, contact scope and basic event constraints. Repository validation additionally enforces section/child compatibility and the successful-source requirement; these cross-table business rules are deliberately not implemented as triggers.

## Address identity and linking

Proposed new `flats/house_types.py` contains only the identity value type:

```python
@dataclass(frozen=True)
class HouseAddress:
    city: str
    street_type: str
    street_name: str
    house_number: str
    corpus: str = ""
    structure: str = ""
```

Normalization is conservative: Unicode normalization, case folding, whitespace and known street/corpus abbreviations. Keep the original display address separately. The exact observed trailing label `Подробности о доме` may be removed by the bundle adapter; it is not part of a street name. Do not discard arbitrary trailing text.

House numbers are text. `133/1` and `133`, `12/1` and `12`, `120а` and `120`, and `10 корпус 1` and `10 корпус 2` are different identities. A slash is not rewritten into a corpus. Do not merge Cyrillic and Latin letters by appearance. The two observed ordinal street forms, such as `Поселковая 3-я` and `3-я Поселковая`, may use explicit reviewed aliases; no general fuzzy street transformation is required.

Missing corpus text is not permission to choose a numbered corpus. An unsupported or ambiguous address returns no identity; it does not fall back to the nearest map marker, a building of similar name, a common management company, or an offer's `group_id`. Map coordinates help diagnose a mismatch but do not prove address identity.

For this first import, resolve the 48 reviewed bundle addresses and their exact VL mappings, then link only the offer keys listed with each house. Source-level failures, such as an unmatched FKR row, do not invalidate an otherwise exact offer-to-address link. A house can exist with an empty passport section.

`offer_houses.offer_address_raw` records the existing offer address used for matching. On read, if it differs from the current `offers.address`, return `status="stale"`, `house_id=None` and no passport shortcut. Re-run conservative linking; do not silently keep the old association after an address edit. A change in price alone does not invalidate the house link. Different offers in one flat group may have different or missing links; the web layer uses the chosen offer's link and displays disagreements.

## Source observations, time and refresh

`house_sources.source_key` is a stable logical source, for example `vl:contacts`, `vl:history:page:0`, `fkr:regional-programme` or `fkr:execution:2024`. Keep the provider identifier and the source house ID separately. The local house is already part of the observation key. A document's edition, URL change or spreadsheet row number is not the logical source identity. Exact source/event key rules are defined in `02-ingestion-provenance.md`.

The immutable observation key is `(house_id, source_key, checked_at)`. Replaying the identical payload returns the existing observation ID without changes. A different payload under that same key is a validation error. `recorded_at` is set once by the repository and is not part of input equality.

Time fields have different meanings:

- `checked_at`: when the documented check/observation took place.
- `fetched_at`: when the underlying response was actually retrieved, if known; nullable for a search extract or a cache record without a reliable retrieval timestamp.
- `recorded_at`: when this database accepted the observation.
- `published_at`, `document_date`, `source_as_of`: dates asserted by the source, where provided.

Re-parsing a cached HTML file does not make the source freshly fetched. Keep an event's original `observed_at` in its raw payload and use the real page retrieval metadata when it exists. Normalize known timestamps to one UTC representation. Preserve date-only values as dates; do not invent a time or year.

`fetch_status` distinguishes `ok`, `empty`, `not_found`, `blocked`, `error`, `parse_error` and `ambiguous`. Only exact/manual source matches with a valid `ok` result may contain facts, contacts or events. `empty` is a successfully read source with no records in its stated scope, and has no child records. All other statuses retain the attempt and error without asserting empty real-world history.

`coverage_json` is a bounded source-section object, not a general facts store. Its known fields are `requested_from`, `requested_to`, `requested_period_complete`, `all_time_history_complete`, `coverage_uncertain`, `pages_collected`, `stop_reason`, `window_verified_empty`, and `collection_notes`; unknown keys from the original observation remain in `raw_json`. Boolean unknowns remain `null`. A source's zero records, an empty last page and unavailable history have different meanings.

Read the latest attempt and the last successful (`ok`/`empty`) observation separately for each logical source. A newer 403/503 or parse error never deletes the previous facts. Contact/fact sections show the last successful snapshot with its original dates and the later failure beside it. A valid empty result only describes that particular source and scope.

Outage archive pages are source observations of a continuing history. Preserve collected events across valid pages and de-duplicate visible records by `(house_id, provider, event_key)`, retaining the latest observation of a publication. A publication missing from a refreshed page is not proof that the historical outage never happened. Document editions for repair plans, by contrast, are snapshots: show the latest successful edition for each logical plan source and retain prior editions as evidence.

## Facts, contacts and event semantics

`house_facts` has explicit columns for construction/commissioning year, floors as published text, entrances, material, areas, connection descriptions and coordinates. Split a source-mixed input block into observations by provenance; do not put an official FKR floor value under a VL source reference. Keep a range such as `20–25` as text. A missing value does not overwrite a known value from another source. The read model exposes different source observations and any conflict, rather than creating a synthetic fully verified row.

Map coordinates come from the exact-address source observation. They do not change `offers.lat/lon`. Preserve the stated precision; the current VL markers are address markers without established entrance accuracy. There is no automatic fallback from a missing house marker to a listing's approximate location.

Contact roles are `management`, `emergency`, `lift`, `maintenance`, `resource_provider` and `city_service`. Preserve published phone strings; no phone call or current service availability has been verified. `scope="city_directory"` requires `relationship_status="not_house_specific"` and is displayed as general city information. A house-specific contact requires its explicit relationship source URL.

For B027, B029 and B042, store **both** management assertions with `relationship_status="unresolved_conflict"`, a shared `conflict_key` for the local house and a reason. The observed identifiers name different legal entities: АО «Технология управления», INN `2543125768`, and ООО УК «Технология комфорта», INN `2536352343`. B029's claimed contract date `2026-09-01` does not resolve the conflict. There is no `houses.management_id` to silently choose a winner. Preserve the supplied evidence in the source/contact raw payloads. An unresolved conflict and its dated claims remain visible even after a later successful refresh; P0 has no automatic conflict-resolution workflow. B006 and B023 retain no invented house manager.

Event kinds have separate meanings:

| Kind | Parsed data allowed | Interpretation |
| --- | --- | --- |
| `outage` | Native publication ID, resource, period text; full start/end timestamp only when explicit | Published interruption information. A displayed end label does not prove restoration. |
| `repair_plan` | Work type, programme/short-term scope, year or year range | A plan. A past planned year does not imply completion or overdue work. |
| `repair_execution` | Work type, reporting year, explicit completion mark where available | A source-reported result. A reporting year is not an exact completion date. |

`completion_reported` is nullable and applies only to execution. The present 28 explicit completion marks may map to `1`; the nine blank/unknown marks map to `NULL`, not `0`. Original sheet row/column or report locator stays in `source_locator` and `raw_json`. Whole-house repair amounts are not rental utility payments and never enter `Costs`.

Use the native VL event ID for a stable outage key, e.g. `vl:blackout:169288`. Programme keys follow the exact family/work-kind rules in `02-ingestion-provenance.md`; the regional programme's changing planned period is observation data rather than identity. Current edition dates and spreadsheet row numbers are never identity. If separate works collide on the chosen key, normalization fails for review instead of silently merging them. Do not guess that similar work descriptions across different documents describe the same completed job.

## Function contracts

All proposed modules use the existing SQLite connection. Writers never commit; the ingestion transaction owns the commit/rollback. SQL uses bound parameters.

| Module | Proposed signature and behavior |
| --- | --- |
| `flats/house_identity.py` | `normalize_house_address(city: str, raw: str) -> HouseAddress | None`; conservative supported-address parsing, no network or fuzzy fallback. |
| `flats/repo_houses.py` | `ensure_house(conn, address: HouseAddress, *, address_display: str, created_at: str) -> int`; insert/reuse normalized identity. |
| `flats/repo_houses.py` | `link_offer_house(conn, source: str, source_id: str, house_id: int | None, *, match_status: str, offer_address_raw: str | None, match_basis: str, evidence_url: str | None, checked_at: str, note: str | None = None, candidates: list[dict] | None = None) -> None`; validate existing offer and status/house pairing, then upsert only the link. |
| `flats/repo_houses.py` | `store_house_observation(conn, house_id: int, observation: dict) -> int`; validate and store one immutable source observation and its children, with exact replay as no-op. |
| `flats/repo_houses.py` | `house_links_for_offers(conn, offer_keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]`; one entry for every requested key, including missing/stale links. |
| `flats/repo_houses.py` | `get_house_passport(conn, house_id: int) -> dict | None`; return the contract below or `None` when the house does not exist. |

The write observation is the table-shaped, versioned ingestion boundary:

```python
{
    "source": {...},  # house_sources fields except id, house_id, recorded_at
    "facts": {...} | None,  # house_facts fields except source_id
    "contacts": [...],     # house_contacts rows except id, source_id
    "events": [...]        # house_events rows except id, source_id
}
```

The repository JSON-encodes the named JSON fields and validates source/child compatibility. It validates timestamps, enum values, coordinate bounds, required provenance and duplicate child keys before writing. Input JSON must not choose arbitrary SQL columns or identifiers. On any invalid child, the whole observation fails within the caller's transaction.

The bulk link result, agreed with the web owner, is:

```python
{
    "status": "exact|manual|unmatched|ambiguous|stale",
    "house_id": int | None,
    "candidates": [],  # address/source URL candidates for review, never auto-selected
    "summary": None | {
        "address": str,
        "coordinates": None | {
            "lat": float, "lon": float, "precision": str | None,
            "source_url": str, "fetched_at": str | None
        },
        "management_status": "published_claim|conflict_unresolved|not_found_in_checked_sources"
    }
}
```

The passport result is a plain mapping used by Flask/Jinja, not an extension of the existing `Offer` types:

```python
{
    "house": {"id": int, "address": str, "city": str},
    "coordinates": None | {...},  # same shape as the link summary
    "facts": [...],             # fixed fact columns + nested source evidence
    "contacts": [...],          # contact columns + nested source evidence
    "outages": {"events": [...], "coverage": [...], "published_records_count": int},
    "repairs": {"plans": [...], "executions": [...], "coverage": [...]},
    "sources": {"latest_attempts": [...], "last_successful": [...]},
    "issues": [{"code": str, "message": str, "source_urls": [str]}]
}
```

Every nested source evidence object contains `source_url`, `source_type`, `provider`, `checked_at`, `fetched_at`, `source_as_of`, `document_date` and `fetch_status` with nullable unknown dates. Events additionally keep their own page URL, raw period and source locator. `published_records_count` is the count of displayed distinct publication-to-house records, not a real incident rate or a comparable annual score. `issues` includes recorded management conflicts, source failures and insufficient/uncertain coverage. The original quality flags remain in source raw payloads; the repository may add a clearly labelled comparison issue when current listing and house observations disagree.

## Implementation tasks with exclusive files

These are proposed implementation tasks. They have not been executed against the application.

### H1 — Add the schema

**Owns:** `flats/schema.sql`, `tests/test_house_schema.py`.

Append the six table definitions and indexes from the proposed migration; preserve all existing SQL definitions. Reuse `db.init_db()` with no new migration runner. Test repeated initialization on a populated database, legacy row preservation, relationship constraints and a failed-source receipt coexisting with earlier claims.

**Inputs:** existing schema and `architecture/house_schema.sql`. **Output:** additive storage available through the existing database initializer.

**Acceptance:** `pytest -q tests/test_house_schema.py`; original offers, decisions, search profile and photo features are unchanged after two initializations.

### H2 — Add exact address identity

**Owns:** `flats/house_types.py`, `flats/house_identity.py`, `tests/test_house_identity.py`.

Implement the small frozen `HouseAddress` type and conservative normalizer. Use the 48 reviewed addresses as fixtures. Support only observed abbreviation/ordinal aliases. Return `None` for an unsupported form. Keep corpus, suffix and slash identities distinct; never make a nearest-coordinate choice.

**Inputs:** `addresses.json` and the verified mapping. **Output:** stable normalized identities or an explicit unsupported result.

**Acceptance:** `pytest -q tests/test_house_identity.py`; 48 expected normalized identities, repeated spelling variants normalize consistently, and negative tests retain all suffix/corpus/slash distinctions. No network in tests.

### H3 — Add repository and passport reads

**Owns:** `flats/repo_houses.py`, `tests/test_repo_houses.py`.

Implement exactly the five repository functions above. Ingestion supplies validated observations; web consumes the agreed dictionaries. Preserve immutable evidence, last successful data after failures, both conflicting manager claims, source scope and nullable event dates. Detect stale offer addresses on read.

**Inputs:** H1 schema, H2 identity type, small captured bundle fixtures. **Output:** the write/read boundary shared by ingestion and web.

**Acceptance:** `pytest -q tests/test_repo_houses.py`. Cover repeated identical import, rejection of modified data under an existing observation key, rollback on a malformed child, missing-offer FK failure, stale-address hiding, B027/B029/B042 conflict preservation, distinct page/publication de-duplication and a newer 503 retaining the preceding successful facts. No changes to existing costs or grouping tests are required beyond running the existing suite as the repository's normal gate.

## Verification actually performed

Command executed:

```bash
python /workspace/scratch/17b96a946d4e/farposton_house_data_20261114/architecture/check_house_schema.py
```

Observed result: **passed**, Python **3.12.14**, SQLite **3.53.1**. The migration was applied three times to a disposable database initialized with an exact copy of the actual `flats/schema.sql`: twice before adding house rows, and once afterward. The self-contained reference fixture is [reference_existing_schema.sql](reference_existing_schema.sql), copied without changes from commit `a697ee5d3805af89bb162705dc5cdb4b933f7cef`; its SHA-256 is `38e06ac1b83cd87f3d5d1b8f40c2a4fac2e7ed99c66b215979b921004679ad59`. All original table definitions, columns and seeded rows remained identical. The seeded legacy rows comprised three offers in two flat groups, two decisions including an undone decision, one photo vector, one profile and one successful import run.

The check also stored distinct corpus/slash identities, retained both management claims after a later HTTP 503 observation, retained unknown dates as `NULL`, rejected missing-offer links and invalid coordinate pairs, and passed `foreign_key_check` and `integrity_check`. The temporary database was removed. Full observed output and schema hashes are in [schema_verification.json](schema_verification.json).

This verifies the proposed DDL's additive behavior and selected constraints. The normalizer, importer, repository functions and web routes are **specified, not implemented or tested**. No application files, deployed database, GitHub repository or production service were changed.
