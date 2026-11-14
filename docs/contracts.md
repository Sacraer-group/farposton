# Contracts

Fixed interfaces between tasks. Tasks implement them exactly. Changing a contract is a separate task in a later sprint.

## 1. Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `METRAPI_KEY` | none (required for import) | Metrapi API key |
| `FLATS_DB` | `data/flats.db` | SQLite file |
| `FLATS_PHOTO_DIR` | `data/photos` | Cached photos |

`data/` is in `.gitignore`.

## 2. SQLite schema (`flats/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS import_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','ok','failed')),
  fetched INTEGER NOT NULL DEFAULT 0,
  error TEXT
);

CREATE TABLE IF NOT EXISTS offers (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  group_id TEXT,
  url TEXT,
  address TEXT,
  district TEXT,
  lat REAL, lon REAL,
  rooms INTEGER,
  area_total REAL,
  floor INTEGER, floors_total INTEGER,
  price INTEGER,
  deposit_raw TEXT, commission_raw TEXT, prepayment_raw TEXT,
  utilities_raw TEXT, pets_raw TEXT, rent_period_raw TEXT,
  description TEXT,
  photos_json TEXT NOT NULL DEFAULT '[]',
  published_at TEXT, last_seen TEXT,
  raw_json TEXT NOT NULL,
  import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
  PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS offers_group ON offers(group_id);

CREATE TABLE IF NOT EXISTS photo_features (
  photo_url TEXT NOT NULL,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  model TEXT NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (photo_url, model),
  FOREIGN KEY (source, source_id) REFERENCES offers(source, source_id)
);

CREATE TABLE IF NOT EXISTS search_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  monthly_max INTEGER,
  move_in_max INTEGER,
  rooms_json TEXT NOT NULL DEFAULT '[]',
  districts_json TEXT NOT NULL DEFAULT '[]',
  pets_required INTEGER NOT NULL DEFAULT 0,
  sample_photo_url TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('save','skip')),
  created_at TEXT NOT NULL,
  undone INTEGER NOT NULL DEFAULT 0
);
```

Rules: offers are upserted by `(source, source_id)`; old offers are never deleted by import. Totals are not stored. For legacy SQLite decision callers, the current decision for an offer is the latest row with `undone = 0`. Web decisions do not use or import those rows.

## 3. Domain types (`flats/types.py`)

Plain `dataclasses`, `frozen=True`. Money is integer rubles. `None` = unknown.

```python
Status = Literal["match", "unknown", "reject"]

@dataclass(frozen=True)
class Amount:            # parsed money term
    rub: int | None      # known sum in rubles, None if unknown
    kind: Literal["none", "rub", "percent", "months", "unknown"]
    value: float | None  # percent or months count when kind is percent/months
    raw: str | None      # original string

@dataclass(frozen=True)
class Offer:             # one row of `offers`, parsed
    source: str; source_id: str; group_id: str | None; url: str | None
    address: str | None; district: str | None; lat: float | None; lon: float | None
    rooms: int | None; area_total: float | None
    floor: int | None; floors_total: int | None
    price: int | None
    deposit_raw: str | None; commission_raw: str | None; prepayment_raw: str | None
    utilities_raw: str | None; pets_raw: str | None; rent_period_raw: str | None
    description: str | None; photos: tuple[str, ...]
    published_at: str | None; last_seen: str | None

@dataclass(frozen=True)
class Costs:
    rent: int | None
    monthly_known: int | None        # rent + known mandatory monthly payments
    monthly_unknown: tuple[str, ...] # e.g. ("utilities",)
    prepayment: Amount
    deposit: Amount
    commission: Amount
    move_in_known: int | None        # prepayment + deposit + commission, known parts only
    move_in_unknown: tuple[str, ...] # e.g. ("deposit", "commission")

@dataclass(frozen=True)
class Profile:
    monthly_max: int | None; move_in_max: int | None
    rooms: tuple[int, ...]; districts: tuple[str, ...]
    pets_required: bool; sample_photo_url: str | None

@dataclass(frozen=True)
class Verdict:
    status: Status
    reasons: tuple[str, ...]          # human Russian text, why reject/unknown

@dataclass(frozen=True)
class FlatCard:                       # one flat = offers sharing group_id
    key: str                          # group_id or f"{source}:{source_id}"
    offers: tuple[Offer, ...]
    best: Offer                       # offer used for list display (see grouping rule)
```

## 4. Function signatures

| Module | Signature |
| --- | --- |
| `flats/config.py` | `metrapi_key() -> str` (raises `RuntimeError` if missing); `db_path() -> Path`; `photo_dir() -> Path` |
| `flats/db.py` | `connect(path: Path \| None = None) -> sqlite3.Connection` (row_factory=Row, foreign_keys on); `init_db(conn) -> None` |
| `flats/money.py` | `parse_deposit(raw: str \| None, rent: int \| None) -> Amount`; `parse_commission(raw, rent) -> Amount`; `parse_prepayment(raw, rent) -> Amount`; `utilities_included(raw: str \| None) -> bool \| None`; `pets_allowed(raw: str \| None) -> bool \| None` |
| `flats/costs.py` | `compute_costs(offer: Offer) -> Costs` |
| `flats/eligibility.py` | `check(offer: Offer, costs: Costs, profile: Profile) -> Verdict` |
| `flats/questions.py` | `questions_for(card: FlatCard, chosen: Offer) -> list[str]` |
| `flats/source.py` | `fetch_all(key: str, params: dict, *, page_size=1000, sleep=time.sleep, http=requests) -> Iterator[dict]` |
| `flats/importer.py` | `run_import(conn, items: Iterable[dict]) -> int` (returns run id) |
| `flats/repo_offers.py` | `upsert_offer(conn, row: dict, run_id: int) -> None` (normalized dict: `offers` columns + `photos` list + `raw` dict; no commit); `all_offers(conn) -> list[Offer]`; `offers_by_key(conn, key: str) -> list[Offer]`; `last_ok_run(conn) -> sqlite3.Row \| None` |
| `flats/repo_user.py` | `get_profile(conn) -> Profile`; `save_profile(conn, p: Profile) -> None`; `decide(conn, source, source_id, action) -> int`; `undo_last(conn) -> int \| None`; `current_decisions(conn) -> dict[tuple[str, str], str]` |
| `flats/grouping.py` | `group_offers(offers: list[Offer]) -> list[FlatCard]` |
| `flats/search.py` | `search(conn, profile: Profile, *, include_skipped=False, decisions=None) -> list[SearchRow]` |
| `flats/embed.py` | `embed_missing(conn, *, model_name: str, limit: int \| None = None) -> int` |
| `flats/similar.py` | `cosine_scores(sample: np.ndarray, matrix: np.ndarray) -> np.ndarray`; `rank_by_photo(conn, sample_url: str, keys: list[str], model: str) -> dict[str, tuple[float, str]]` (key -> score, best photo url) |
| `flats/cli.py` | `main(argv=None) -> int`; subcommands `init-db`, `import [--limit N]`, `embed [--limit N]` |
| `flats/web/__init__.py` | `create_app(db_path: Path \| None = None) -> Flask` |

`SearchRow` (in `flats/types.py`): `card: FlatCard`, `offer: Offer` (offer used for costs), `costs: Costs`, `verdict: Verdict`, `decision: str | None`, `similar: tuple[float, str] | None`.

## 5. Business rules

### Money parsing (`flats/money.py`)

Parsing order for `parse_deposit` / `parse_commission` / `parse_prepayment` (first match wins). Text is normalized first: lowercase, `ё` -> `е`, NBSP/thin space -> space, spaces between digits removed.

| # | Raw | Result |
| --- | --- | --- |
| 1 | `None`, empty or only spaces | `kind="unknown", rub=None, value=None` |
| 2 | starts with `без` or is the word `нет` | `kind="none", rub=0, value=None` |
| 3 | `N%` (N may be decimal with `,` or `.`) | `kind="percent", value=N`; `rub=round(rent*N/100)` if rent known else `None` |
| 4 | `N мес...` or `за мес...` without N (N=1) | `kind="months", value=N`; `rub=rent*N` if rent known else `None` |
| 5 | number followed by `₽`, `руб` or `р` | `kind="rub", rub=<int>, value=None` |
| 6 | anything else (`по договоренности`, `частично`) | `kind="unknown", rub=None, value=None` |

`raw` always keeps the original, un-normalized string.

`utilities_included(raw)`: `None`/empty -> `None`; contains `не включ`, `арендатор`, `отдельно` or `счетчик` -> `False`; else contains `включ` or `собственник` -> `True`; else `None`.

`pets_allowed(raw)`: a Python `bool` is returned as is; `None`/empty -> `None`; contains `нельзя` or `запрещ`, or is `нет`/`false`/`0` -> `False`; contains `можно` or `разреш`, or is `да`/`true`/`1` -> `True`; else `None`.

These rules come from the Metrapi docs, not from observed data. When the listings database arrives, its real strings are added here and to `tests/test_money.py`.

### Costs (`flats/costs.py`)

- `rent = offer.price`.
- `monthly_known = rent` (`None` if rent unknown). `monthly_unknown`: `"rent"` if rent unknown; `"utilities"` if `utilities_included` is not `True`.
- Prepayment unknown -> assume nothing, put `"prepayment"` in `move_in_unknown`. Prepayment "за 1 месяц" = first month rent paid once; never add rent again.
- `move_in_known` = sum of known `rub` of prepayment, deposit, commission; `None` only if all three unknown.
- Each component with `rub is None` goes to `move_in_unknown`, order: `"prepayment"`, `"deposit"`, `"commission"`.

Example (test fixture): rent 40000, deposit `40 000 ₽`, commission `50%`, prepayment `за 1 месяц`, utilities `оплачиваются отдельно` -> `monthly_known=40000`, `monthly_unknown=("utilities",)`, `move_in_known=100000`, `move_in_unknown=()`.

### Eligibility (`flats/eligibility.py`)

Check in order; any reject -> `reject`; else any unknown -> `unknown`; else `match`.

| Condition | reject when | unknown when |
| --- | --- | --- |
| `monthly_max` | `monthly_known > monthly_max` | rent unknown |
| `move_in_max` | `move_in_known` not `None` and `> move_in_max` (even if other parts unknown) | otherwise, one reason per `move_in_unknown` item |
| `rooms` (if non-empty) | rooms known and not in list | rooms unknown |
| `districts` (if non-empty) | district known and not in list | district unknown |
| `pets_required` | pets explicitly not allowed | pets unknown |

A reject verdict carries only reject reasons. Exact reason texts are listed in issue #15 (S1-T3). Utilities unknown is not an eligibility reason; it becomes a question.

### Grouping (`flats/grouping.py`)

- Key = `group_id`, or `f"{source}:{source_id}"` if `group_id` is `None`.
- `best` = offer with the lowest known `move_in_known + monthly_known`; offers with unknowns go after known ones; tie -> latest `last_seen`.
- Offers are never merged field by field.

### Web decisions and comparison

The browser stores save/skip/undo history as journal triples `[source, source_id, action]` in `localStorage` key `farposton.decisions.v1`. It does not automatically import legacy SQLite decisions. Changes are reflected across tabs through storage events and on `pageshow`. Clearing site data removes the history; browsers and devices have separate histories. The search profile and sample photo remain shared in SQLite.

`/favorites` renders public offer candidates hidden; browser code reveals saved offers and disables hidden comparison checkboxes. `/compare` accepts up to five explicitly requested existing offer IDs, independent of SQLite favorites, and does not import them into browser storage. Legacy `POST /api/decide` and `POST /api/undo` return 410 without mutation.

### Search order (`flats/search.py`)

`decisions=None` means an empty mapping; search never loads shared SQLite decisions implicitly. Domain callers can pass an explicit mapping keyed by `(source, source_id)`.

1. Drop `reject`. Drop `skip` decisions unless `include_skipped`.
2. For each card, evaluate every offer; the card's offer = best non-reject offer by grouping rule.
3. Without sample photo: `match` before `unknown`, then by `monthly_known` ascending (unknown last).
4. With sample photo: same filter, then by similarity descending; cards without photo vectors go last in rule-3 order.

### Questions (`flats/questions.py`)

| Trigger on chosen offer | Question (Russian) |
| --- | --- |
| utilities unknown / not included | `Какова обычная сумма коммунальных платежей?` |
| deposit kind `unknown` | `Какой залог и на каких условиях он возвращается?` |
| commission kind `unknown` | `Есть ли комиссия и какой она суммы?` |
| prepayment kind `unknown` | `За сколько месяцев нужна предоплата?` |
| pets unknown | `Можно ли жить с животными?` |
| another offer of the card differs in price/deposit/commission/prepayment (raw, stripped, lowercased) | `Условия на площадках различаются — подтвердите условия этого предложения` |
| always | `Есть ли другие платежи при въезде?` |
