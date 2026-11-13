# Evening MVP — review journal (кратко, по фактам)

Журнал составлен по записям в `docs/evening/VERIFICATION.md`, `docs/evening/BROWSER.md` и текущему состоянию worktree. Отдельные текстовые артефакты review1/review2 в репозитории не найдены; ниже только зафиксированные факты.

## review1 → fix1

- **Факты review1 (P1, зафиксировано в VERIFICATION):**
  1. `GET /api/evening/events` не исключал scheduled-события с `starts_at <= now`.
  2. `GET /api/evening/plans/{id}` мог возвращать 500 при сломанном/недоступном seed вместо снимка со `current.event_status=stale`.
- **fix1 (зафиксировано):** исключение прошедших scheduled из списка событий; обёртка загрузки/ревалидации seed при чтении плана с возвратом 200 + snapshot + `stale` при недоступном/сломанном seed; добавлены тесты `test_F1_events_excludes_scheduled_event_in_past`, `test_F7_broken_seed_json_returns_snapshot_stale`, `test_F7_unreadable_seed_returns_snapshot_stale`, `test_F7_rescheduled_current_status`. Валидный seed runtime-логикой не переписывается.
- **Проверки после fix1:** evening tests 43 passed (1 warning), full `make test` 438 passed / 6 skipped + DOM helper passed; Docker rebuild/restart на `127.0.0.1:18447`, реальные HTTP POST/GET и сохранение плана в volume после restart подтверждены (детали в VERIFICATION).

## review2 → fix2

- **Факты review2/уточнения контракта (зафиксировано в VERIFICATION, pass 2026-11-13):**
  1. `GET /api/evening/plans/{id}`: невалидный UUID должен давать `422 validation_error`, валидный неизвестный — `404 plan_not_found`.
  2. `GET /api/evening/events` должен возвращать только `status == scheduled` и future-события, доступные для `POST /api/evening/plans`; `rescheduled/cancelled/past` исключаются. Сохранённые планы остаются читаемыми, `current` ревалидируется (включая `rescheduled`) без изменения snapshot.
- **fix2 (зафиксировано):** обновлены `evening/routes.py`, `tests/test_evening_mvp.py`, `docs/evening/SPEC.md`, `docs/evening/ACCEPTANCE.md`, `docs/evening/VERIFICATION.md`; добавлены `test_F1_events_excludes_rescheduled_future_event`, `test_F2_get_plan_invalid_uuid_returns_422_validation_error`; `test_sql_parameterized_safety` приведён к ожиданию `422 validation_error` для невалидного UUID.
- **Проверки после fix2:** evening tests 45 passed (1 warning), full `make test` 440 passed / 6 skipped + DOM helper passed; Docker image/container на `127.0.0.1:18447` пересобран, реальные HTTP проверки дали expected 201/200/422/404, после restart план сохранился в volume (детали в VERIFICATION).

## Browser

- Реальный browser smoke (Playwright/Chromium, headless) зафиксирован в `docs/evening/BROWSER.md`: `SMOKE_OK`, exit code 0, сценарии загрузки `/evening`, выбора live scheduled seed-события, валидного ввода, создания плана, отображения primary/plan B/маркировки, открытия `/evening/p/{id}`, ошибки невалидного ввода без crash, отсутствие горизонтального переполнения на 320/1280.
- BROWSER.md отмечает проверку против конкретного запущенного контейнера/seed на момент прогона; при проверке на другом/старом image требуется rerun `tests/evening_browser_smoke.cjs` по инструкциям BROWSER.md на current container (результат rerun должен быть отражён в VERIFICATION).
