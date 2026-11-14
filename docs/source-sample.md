# Source sample: Metrapi, Vladivostok long-term rent

Fetched 2026-11-13 with trial access, one request:

```
GET https://api.metrapi.ru/v1/items?city=vladivostok&deal_type=rent&rent_period=long&realty_type=flat&with_photo=1&limit=50
```

Stored normalized (no phones, no seller names) in `flats/demo/metrapi_vladivostok.json`. Fetch script: `scripts/fetch_metrapi.py`.

## Fill rate (50 offers)

| Field | Filled |
| --- | --- |
| `price` | 50/50 |
| `rooms` | 40/50 |
| `area_total` | 50/50 |
| `floor` | 50/50 |
| `district` | 50/50 |
| `address` | 50/50 |
| `lat` | 50/50 |
| `deposit` | 48/50 |
| `commission` | 32/50 |
| `prepayment` | 0/50 |
| `utilities_payer` | 49/50 |
| `pets_allowed` | 0/50 |
| `description` | 50/50 |
| `photos` | 50/50 |
| `url` | 50/50 |
| `group_id` | 50/50 |
| `published_at` | 0/50 |

## Observed facts

- All 50 offers come from FarPost (`source=farpost`); no duplicate groups in this sample.
- `deposit` and `commission` are always `"N ₽"` strings or `null`; no percents or text terms in this sample.
- `prepayment` and `pets_allowed` are never filled. FarPost puts `Можно с животными` only into `description` (10/50); the loader copies it to `pets_raw`.
- `utilities_payer` is `Арендатор` (49/50) -> utilities not included.
- `rooms` is `0` for studios and `null` for `Гостинка` (small room-type flat).
- `description` is a FarPost-generated summary with HTML entities (`&#8381;`); one contained a phone number, masked by the loader.
- Photo URLs (`static.baza.farpost.ru`) returned 200 `image/webp` without a referrer.

## Consequences

- Prepayment was absent in this sample. The first month of known rent is included in the known move-in amount; extra prepayment remains unknown. An offer exceeding the move-in budget on known costs is rejected; otherwise missing prepayment makes it unknown.
- Money parsing rules in contracts §5 cover all observed strings.
