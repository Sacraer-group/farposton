# Evening — архитектура

## Модули

| Модуль | Ответственность |
|---|---|
| `evening/config.py` | Режим (`real`/`demo`), пути каталога/событий/state |
| `evening/realdata.py` | Загрузка/кэширование снимка каталога `profiles-separated-v1`, загрузка `vladivostok_events.json`, нормализация rubrics→общепит, маппинг признаков, haversine, проверка расписания (weekday, overnight, split/holes, full interval, unknown/special) |
| `evening/seed.py` | Demo runtime seed (25 мест, 6 событий) в `.demo/evening/`, без перезаписи |
| `evening/models.py` | Строгая валидация запроса (UUID, типы, границы, enum, unique/no intersection) |
| `evening/recommender.py` | Чистая функция: фильтр (окно, distance, required true), сортировка (preferred desc, distance asc, id asc), выбор primary/alternative (другая категория при возможности) |
| `evening/store.py` | SQLite snapshot/TTL 24h, параметризованный SQL |
| `evening/routes.py` | API/UI, режимы, 503 в real при недоступности, 409 venue unavailable, `current` revalidate, source/synthetic/stale propagation |
| `evening/timeutil.py` | `Asia/Vladivostok`, интервалы `[start,end)` |

## Потоки данных

- **Real**: `load_catalog()` читает полный каталог (venue coords для всех 4567) и фильтрованный общепит-список для рекомендаций; `load_events()` читает проверенную подборку. `venue_id` события резолвится в полном каталоге; distance = haversine(venue,event place). Синтетическая загруженность игнорируется.
- **Demo**: `load_or_create_seed()` в `.demo/evening/seed.json`, precomputed distance/hours.
- **Create plan**: валидация → событие доступно → venue coords (real) → recommend → snapshot в SQLite (без `current`) → ответ с `current`.
- **Read plan**: загрузка snapshot → expiry check → revalidate `current` по актуальным данным/файлам; snapshot не меняется. Старые synthetic планы остаются читаемыми.

## Ошибки

`401 unauthorized`, `404 event_not_found|plan_not_found`, `409 event_unavailable|event_venue_unavailable|not_enough_places`, `410 plan_expired`, `422 validation_error`, `503 real_data_unavailable|events_unavailable` (real, без fallback).

## Docker

`Dockerfile.evening` (nonroot, allowlist copy, без БД/моделей/ключей), `compose.evening.yaml`: один сервис `evening`, `127.0.0.1:18447`, `EVENING_DATA_MODE=real`, readonly bind mount каталога `create_host_path:false`, env host path config (`EVENING_CATALOG_HOST_DIR`), volume `evening_state` для планов, healthcheck `/health`. Demo — явный `docker run` (отдельно, без второго сервиса/конфликта портов). Публичный `evening/data/vladivostok_events.json` включён в образ узким исключением `.dockerignore`; для чтения каталога контейнером `uid 10001` при `700/600` применяются минимальные POSIX ACL на root/2 папки/2 JSONL.
