# Real event sources (Vladivostok)

Manual selection for `evening/data/vladivostok_events.json` (checked 2026-11-13, Asia/Vladivostok, UTC+10). Each event was verified on its public event card. Only brief facts are included; descriptions, posters and images are not copied.

Checked public sources:
- https://www.vl.ru/afisha/vladivostok
- https://prim.mariinsky.ru/playbill/archive/?month=11&season=14&year=2026 (reviewed; no listed performance in 2026-11-14..2026-11-27 could be matched to an existing venue ID in `/home/recara/farposton/data/2gis/profiles-separated-v1/{01_2gis,02_generated}/profiles.jsonl`)

2GIS API access conditions were not checked. No 2GIS API requests were made; venue IDs were matched only from local `profiles.jsonl` summaries by venue name/address.

## Events

| Source (event card) | Date / time (UTC+10) | Venue / catalog ID | Confirmed | Unknown / notes |
|---|---|---|---|---|
| [VL.ru Афиша 261766](https://www.vl.ru/afisha/vladivostok/event/261766) | 2026-11-15 17:00 | Фетисов Арена, концертно-спортивный комплекс / `3518966490262508` | Date, time, venue, status `scheduled`, age 0+ (event page) | End time not stated; ticket purchase flow not confirmed on card |
| [VL.ru Афиша 261766](https://www.vl.ru/afisha/vladivostok/event/261766) | 2026-11-16 19:00 | Фетисов Арена, концертно-спортивный комплекс / `3518966490262508` | Date, time, venue, status `scheduled`, age 0+ (same event card, second session) | End time not stated; ticket purchase flow not confirmed on card |
| [VL.ru Афиша 261188](https://www.vl.ru/afisha/vladivostok/event/261188) | 2026-11-15 18:00 | Лимончелло / `70000001118407357` | Date, time, venue, status `scheduled`, age 12+, tickets shown on card | End time not stated |
| [VL.ru Афиша 261998](https://www.vl.ru/afisha/vladivostok/event/261998) | 2026-11-19 11:00 | Con tempo, кафе / `3518966490088551` | Date, time, venue, status `scheduled`, age 18+, registration shown on card | End time not stated |
| [VL.ru Афиша 260907](https://www.vl.ru/afisha/vladivostok/event/260907) | 2026-11-19 19:00 | Con tempo, кафе / `3518966490088551` | Date, time, venue, status `scheduled`, age 6+, duration 1 h 15 min stated, tickets shown on card | End time not explicitly stated as clock time |
| [VL.ru Афиша 261767](https://www.vl.ru/afisha/vladivostok/event/261767) | 2026-11-22 17:00 | Фетисов Арена, концертно-спортивный комплекс / `3518966490262508` | Date, time, venue, status `scheduled`, age 0+ (event page) | End time not stated; ticket purchase flow not confirmed on card |
| [VL.ru Афиша 261997](https://www.vl.ru/afisha/vladivostok/event/261997) | 2026-11-26 11:00 | Con tempo, кафе / `3518966490088551` | Date, time, venue, status `scheduled`, age 18+, registration shown on card | End time not stated |
| [VL.ru Афиша 261768](https://www.vl.ru/afisha/vladivostok/event/261768) | 2026-11-27 19:00 | Фетисов Арена, концертно-спортивный комплекс / `3518966490262508` | Date, time, venue, status `scheduled`, age 0+ (event page) | End time not stated; ticket purchase flow not confirmed on card |

## Limitations

- Manual selection does not promise all city events and has no auto-update.
- Only events whose venue could be matched to an existing catalog venue ID were included; events at venues not present in the catalog (including Mariinsky Primorsky Stage performances) were excluded rather than assigned invented or nearby venue IDs/coordinates.
- Ticket availability, prices and end times may change after `checked_at`; see event cards for current details.
