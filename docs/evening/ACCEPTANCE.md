# «Готовый вечер» — приёмка MVP

## Уточнения координатора (обязательные)

- Enum признаков (5): `dog`, `laptop`, `children`, `terrace`, `reservations`.
- Интервалы еды `[meal_start, meal_end)` проверяются полностью.
- `current` при чтении: `cancelled`, `rescheduled`, `past`, `unavailable`, `stale` со снимком; отсутствующий/удалённый event не превращает сохранённый план в 404. Сохранённый план с `current.event_status = rescheduled` остаётся доступным по `GET`.
- `GET /api/evening/events` возвращает только `scheduled` future-события, доступные для `POST /api/evening/plans`; `rescheduled`, `cancelled`, `past` не выбираются.
- `GET /api/evening/plans/{id}`: невалидный UUID → `422 validation_error`; валидный неизвестный → `404 plan_not_found`; истёкший → `410 plan_expired`.
- UUID валидировать.
- SQL параметризованный; никакого raw текста/контактов.
- Пользователь может получить ссылку без учётки только в local demo.
- Авторизация API соответствует существующему `require_auth`.
- Runtime fictionalseed: 25 мест и 6 событий на ближайшие даты, в отдельной игнорируемой `.demo/evening/`, не перезаписывать, открыт только этот источник по умолчанию, UI чётко вымышленный.
- POST strict validation: bool/int, max 5 уникальных фич, неизвестные/лишние поля.
- Нет 2 подходящих — 409 `not_enough_places`; неизвестное required не проходит; предпочтения затем distance затем id; plan B другая category если есть.
- Сохранение SQLite, expiry 24h, read current revalidate, источники/дата проверки.
- UI `/evening` и `/evening/p/{id}`: событие, дата, окно еды, предпочтения, результат+plan B+объяснения+копирование ссылки, errors/loading/mobile/keyboard, без минут пути/гарантий мест.
- Никаких LLM или внешних API в MVP.
- Docker: `Dockerfile.evening`, `compose.evening.yaml`, `DOCKER-EVENING.md`, `.dockerignore` если нет; allowlist COPY, без ключей/2GIS/.git/.venv/.opencode/.demo хоста; nonroot, `127.0.0.1:18447`, volume для состояния, healthcheck.

## Матрица F1–F8 → тесты

Каждому ID соответствует хотя бы один автотест с этим ID в имени.

| ID | Требование | Проверка | Ожидаемые тесты (имена содержат ID) |
|---|---|---|---|
| F1 | События | `GET /api/evening/events` возвращает только `scheduled` future-события, доступные для `POST`; `rescheduled/cancelled/past` excluded. POST на отменённое/прошедшее даёт 409 | `test_F1_events_list_returns_seed`, `test_F1_create_plan_rejects_cancelled_event`, `test_F1_create_plan_rejects_past_event`, `test_F1_events_excludes_scheduled_event_in_past`, `test_F1_events_excludes_rescheduled_future_event` |
| F2 | Валидация | неверные типы, границы, лишние/неизвестные поля, bool/int, UUID дают 422; неизвестный `event_id` при POST — 404; `GET /api/evening/plans/{id}`: невалидный UUID → 422, валидный неизвестный → 404 | `test_F2_validation_*`, `test_F2_unknown_event_404`, `test_F2_get_plan_invalid_uuid_returns_422_validation_error`, `test_F7_plan_not_found_404` |
| F3 | Еда до события | `meal_end > meal_start`, `meal_end <= starts_at`; дата совпадает с датой события; нарушения → 422 | `test_F3_meal_end_after_start_rejected`, `test_F3_meal_end_after_event_start_rejected`, `test_F3_date_mismatch_rejected` |
| F4 | Часы работы | Место, закрытое в любой части `[meal_start, meal_end)`, не попадает в выдачу | `test_F4_place_closed_in_window_excluded`, `test_F4_full_interval_required` |
| F5 | Обязательные признаки | Только `true`; `unknown` не засчитан и показан отдельно; неизвестное required не проходит | `test_F5_only_confirmed_required_pass`, `test_F5_unknown_required_rejected`, `test_F5_unknown_listed_separately` |
| F6 | План Б | `alternative` проходит те же ограничения; другая category если есть; меньше двух мест → 409 `not_enough_places` | `test_F6_alternative_different_category_preferred`, `test_F6_alternative_same_constraints`, `test_F6_not_enough_places_409` |
| F7 | Сохранённый план | `current` считается при чтении, снимок не меняется; после 24 ч → 410; отсутствующий/удалённый event не 404 плана; saved plan при rescheduled показывает `current.event_status=rescheduled` | `test_F7_current_revalidated_snapshot_unchanged`, `test_F7_expired_plan_410`, `test_F7_missing_event_not_plan_404`, `test_F7_cancelled_current_status`, `test_F7_rescheduled_current_status`, `test_F7_broken_seed_json_returns_snapshot_stale`, `test_F7_unreadable_seed_returns_snapshot_stale` |
| F8 | Docker из чистого clone | Порт `127.0.0.1:18447`, nonroot, healthcheck `/api/evening/events` проходит; в image только вымышленный seed, без секретов/2GIS/.git/.venv/.opencode/.demo | `test_F8_*` (конфигурационные проверки файлов/compose/dockerfile где применимо) + реальные Docker проверки в VERIFICATION |

## Дополнительные тесты

- Timezone: вычисления дат/окон в `Asia/Vladivostok`.
- SQL injection safety: параметризованные запросы (проверка через опасные строки/ids не ломает и не исполняет).
- TTL: expiry ровно/после 24h.
- Отмена/полное окно: cancelled/rescheduled/past/unavailable/stale состояния current; `[start,end)` полное покрытие.
- План Б сортировка: предпочтения desc, distance asc, id asc.

## Коды ошибок

| Код | Случай |
|---|---|
| 401 | Нет авторизации (require_auth) |
| 404 | event_not_found / plan_not_found |
| 409 | event_unavailable / not_enough_places |
| 410 | plan_expired |
| 422 | validation_error |

## Проверки исполнения

- `make test VENV=/home/recara/farposton/.venv/bin/python` (после `demo.local prepare` в worktree при необходимости) — существующие + новые тесты.
- Docker: build, реальный HTTP сквозной сценарий POST/GET на `127.0.0.1:18447`, перезапуск с volume.
- Результаты и ограничения фиксируются в `docs/evening/VERIFICATION.md` и продолжении README.
