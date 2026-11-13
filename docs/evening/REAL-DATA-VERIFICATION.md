# Evening — real data verification

## Предыдущий проверочный проход (2026-11-13)

### Scope
- Worktree: `/home/recara/farposton-evening`, branch `feature/evening-planner` (HEAD `51534ad`, без commit/push в этом проходе).
- Авторизованная база: `/home/recara/farposton/data/2gis/profiles-separated-v1/{01_2gis,02_generated}/profiles.jsonl` (4567 карточек, общепит 1344). Содержимое исходной базы не изменялось.
- События: `evening/data/vladivostok_events.json` (8 реальных событий 15–27.11.2026, 3 площадки, все 7 URL и venue ID проверены data-агентом и независимым review) — файл не пересоздавался, включён в git/образ узким исключением `.gitignore`/`.dockerignore` (только этот публичный JSON, без сырой базы).
- Ограничения соблюдены: без чтения секретов/env/gh/ssh/клиентских данных, без API 2ГИС, без исходного dirty `~/farposton`, без публикации/деплоя/push.

### Реализовано в этом проходе
- Узкие исключения `.gitignore`/`.dockerignore` для `evening/data/vladivostok_events.json`.
- POSIX ACL (минимально) для `uid 10001`: `r-x` на root каталога и 2 папках, `r--` на 2 JSONL (`/home/recara/farposton/data/2gis/profiles-separated-v1`, `01_2gis`, `02_generated`, их `profiles.jsonl`). Установлен `acl` из ОС, применён `sudo -n setfacl`; world-chmod/прочие файлы не трогались.
- `realdata.py`: OSError/PermissionError при доступе к каталогу/файлам → `RealDataUnavailable` (503), без 500; `source_checked_at` каталога `null/unknown` (mtime не дата проверки), `updated_at` карточек сохранён; категории общепита переиспользуют `web.places.CATEGORY_LABELS`; расписание — merge смежных/перекрывающихся интервалов, поддержка `24:00` как конца дня/overnight для полного `[start,end)`; строгая валидация событий (UUID, https, `date == aware starts_at` в VL, `synthetic:false`, обязательные поля, корректный `checked_at`) — повреждение/невалидность (в т.ч. всех записей) не даёт тихий успех (503), пустой валидный список допустим.
- Compose: один реальный сервис `evening` (real), demo — явный `docker run` в документации; Dockerfile/compose/docs/tests обновлены.
- Адаптирован `tests/evening_browser_smoke.cjs` на real (маркировка реальных данных, окно от начала события).

### Проверки (факт)

1. Evening tests (real+mvp+docker config):
`PYTHONPATH=/home/recara/farposton-evening /home/recara/farposton/.venv/bin/python -m pytest -q tests/test_evening_real.py tests/test_evening_mvp.py tests/test_evening_docker_config_F8.py`
→ **63 passed, 1 warning** (third-party `starlette.testclient`/anyio deprecation). В `test_evening_real.py` добавлены точечные проверки: permission/OSError каталога → 503, `checked_at` каталога `null/unknown`, merge интервалов/`24:00`, строгая валидация событий/отсутствие тихого успеха при повреждении.

2. Full tests:
`make test VENV=/home/recara/farposton/.venv/bin/python`
→ pytest **459 passed, 6 skipped, 1 warning in 22.89s**; `node tests/test_dom_helper.cjs` → **DOM helper regression checks passed**. Пропуски pre-existing (`facts.json` не собран): `test_docs_and_invariants.py` (3), `test_shelf.py` (3).

3. Docker (real, порт 18447, основной сервис 8445 не трогался):
`sudo -n env EVENING_CATALOG_HOST_DIR=/home/recara/farposton/data/2gis/profiles-separated-v1 docker compose -f compose.evening.yaml up -d --build`
→ образ пересобран, контейнер `farposton-evening-evening-1` пересоздан, **healthy** (`/health` 200, `{"ok":true,"data_mode":"real"}`), readonly bind mount каталога (`create_host_path:false`), volume `evening_state` сохранён.

4. HTTP end-to-end (real): `GET /api/evening/events` → **8 событий, `synthetic:false`**. Для всех 8 событий `POST /api/evening/plans` (окно вычислено от начала события, `max_distance_m=3000`) → **201, реальный primary+plan B, `synthetic:false`**, немедленный `GET` подтвердил snapshot (0 conflict/other, ничего не подгонялось):
   - `91fb6bb0-0c22-511c-a03f-ffec0649c37e` 15:00–16:30 → plan `b4ab39c7…`, primary `70000001118221791`, alt `70000001080145618`
   - `f3056ae9-3857-5390-9a31-2a847c503065` 17:00–18:30 → plan `6e220f04…`, primary `70000001074578014`, alt `70000001118221791`
   - `9dc2d6e5-dd9e-5cd5-8bc3-171d28675e88` 16:00–17:30 → plan `57cfa009…`, primary `70000001118407357`, alt `70000001029212388`
   - `6c3a4643-1a8b-5675-852e-b46edff95151` 09:00–10:30 → plan `67a91ee4…`, primary `70000001082683649`, alt `70000001019068272`
   - `bb64ba6d-41c9-5c49-8eeb-aaec26e071df` 17:00–18:30 → plan `75ce7797…`, primary `3518966490088551`, alt `70000001059827576`
   - `45b46a3e-52bd-56c2-8799-50966558d069` 15:00–16:30 → plan `68f1dde9…`, primary `70000001118221791`, alt `70000001080145618`
   - `c2f23be6-919e-5d99-920c-53912316c8c9` 09:00–10:30 → plan `b9747b2d…`, primary `70000001082683649`, alt `70000001019068272`
   - `d8a0f3da-9912-5758-92e7-91db42e90df5` 17:00–18:30 → plan `4eed0c2f…`, primary `70000001074578014`, alt `70000001118221791`

   3 разных события (первые три) — сквозной `POST→GET→restart→GET`: после `docker compose restart evening` планы сохранены в volume, `GET` вернул те же `id/primary/alternative`, `synthetic:false`.

5. 503 без fallback без каталога (отдельный временный контейнер, без mount каталога, events в образе): `GET /api/evening/events` → 200 (файл событий в образе), `POST /api/evening/plans` → **503 `real_data_unavailable`**. (Юнит-тесты дополнительно покрывают отсутствие каталога/событий и permission/OSError → 503.)

6. Browser smoke (real, Chromium headless, Playwright из `.demo/evening-browser/`): `node tests/evening_browser_smoke.cjs` → **`SMOKE_OK`** (exit 0). Выбрано live-событие `91fb6bb0…`, окно `15:00–16:30` от начала события; создан план `03b5e42e-f863-4a92-a8a3-b9423a2ca573` (201), результат/страница `/evening/p/...` с primary/plan B/источниками/реальной маркировкой/без `fictional_seed`, текущий статус `scheduled`; ошибка валидации без crash; ширины 320/1280 без overflow. Скриншоты: `.demo/evening-browser/screenshots-real/`. Подробности: `docs/evening/BROWSER.md`.

### Ограничения / не сделано в этом проходе
- В этом предыдущем проходе commit/push не выполнялись; независимая проверка и финальные результаты приведены ниже.
- Синтетическая точность демо ≠ клиентская точность; факт наличия столов/статусы билетов/сезонность террас/автообновление афиши не проверяются и не обещаются. Источники/`checked_at` отображаются честно.
- Отдельный временный контейнер «без каталога» показал 503 на операции, требующей каталог; листинг событий из образа доступен — это ожидаемо при наличии публичного events JSON в образе.

## История (предыдущий проход, 2026-11-13)

- На момент прошлого прохода `evening/data/vladivostok_events.json` и `docs/evening/REAL-EVENT-SOURCES.md` отсутствовали; проверялись schematic temp fixtures, Docker/HTTP e2e и browser smoke против real events не выполнялись. Результаты: evening 59 passed, full 454 passed (6 skipped), DOM passed; реализован real adapter/режимы/валидации в пределах доступных данных. Детали сохранены в git history и предыдущей ревизии этого файла.


## Финальная точечная правка (source-review, 2026-11-13)

- `evening/routes.py`: `_compute_current_real` при том же `event_id` и `status=scheduled` теперь сравнивает каноническое `starts_at` (по `datetime`, эквивалентный offset не считается переносом) и `venue_id` (если оба присутствуют) со снимком; при отличии возвращает `current.event_status=rescheduled` с актуальным `starts_at`, snapshot/расстояния не пересчитываются. Явная `cancelled` в приоритете. В fallback `GET /api/evening/plans/{id}` при недоступных событиях убран мёртвый `try evdata=None`, оставлен один явный stale-ответ с предупреждением.
- Тесты: добавлены `test_current_time_change_same_status_becomes_rescheduled_and_snapshot_preserved`, `test_current_venue_change_same_status_becomes_rescheduled_and_snapshot_preserved` в `tests/test_evening_real.py`.
- Документация: уточнены `docs/evening/SPEC.md`, `docs/evening/ACCEPTANCE.md` (поведение `current` при изменении времени/площадки без смены статуса).

### Проверки этого прохода
- Фокус: **69 passed, 1 warning** (OpenCode shell `sh_1d4344c75001lESYREiqVPTYM6`).
- Полный `make test`: **464 passed, 6 skipped, 1 warning in 23.87s**, DOM helper passed. Пропуски — прежние проверки без `facts.json`.
- Docker real пересобран, healthy, uid `10001:10001`; каталог 4567 / общепит 1344, события 8, `synthetic:false`.
- Повторный HTTP `POST→GET→restart→GET`: сохранены id, primary, alternative, event, synthetic; план `4a053fb9-3855-435a-8ca6-dcb6957dd0dd`.
- Browser: **SMOKE_OK**, план `53c8bdbc-ba3e-4f34-ab88-9c9e938d74fc`, создание/сохранение, источники, ошибка пересечения признаков, 320/1280 px без горизонтального overflow. OpenCode shell `sh_1d436a480001bDM5f06R6B74s8`, exit 0; старый сервис 8445 active.
- Независимое ограниченное ревью обнаружило два случая расписаний: отдельное покрытие previous-night/today и ложное closed при отсутствующем today. Теперь объединяются доверенные интервалы; missing day остаётся unknown без полного покрытия; реальный разрыв остаётся closed. Добавлены три регрессионных теста и проверка special/comment.
- Сужены исключения Git/Docker: разрешён только публичный JSON мероприятий; остальные файлы `evening/data/*` исключены.
- После повторных сбоев compaction/model loop исполнение завершено через документированный командный API OpenCode (`/api/shell`), без изменения глобальной конфигурации и без обхода прав доступа.
- Афиша — ручная подборка, без автообновления; наличие столов/билетов, сезонность террас не подтверждаются. Сырые данные каталога не включаются в Git/образ.

## Чистый пакет из Git (финальная проверка)

- OpenCode shell `sh_1d43a8ad3001DezPUvVkpX5qX7`, exit **0**.
- `git archive e71b1dbd550b` в новый ignored каталог, отдельная Docker-сборка `Dockerfile.evening`; без `.demo`, node_modules, секретов и сырой базы в контексте.
- Новый временный контейнер на `127.0.0.1:18448`, новый state volume; каталог подключён извне readonly. Пользователь `appuser` (uid 10001), mount `bind:RW=false`, state `volume:RW=true`.
- `/health` real; 8 реальных событий; 4567 карточек / 1344 общепита; `POST` 201 с `synthetic:false`, `GET→restart→GET` сохранил id/event/primary/alternative/synthetic. План `5ddb500c-b7ae-4e62-9ca9-3a013637003b`.
- `CLEAN_GIT_ARCHIVE_REAL_HTTP_PERSISTENCE_OK`, `CLEAN_CATALOG_OK`. Созданные для проверки временные контейнер/volume удалены; основной volume сохранён.
- Изменения после проверенного code commit затрагивают только документацию. Real mode намеренно требует внешнюю базу каталога; без неё операции подбора возвращают 503, а не вымышленные заведения.

## Согласование с текущей историей GitHub

На origin история ветки была заменена на базу `4b74da9` с изменёнными README и учебным notebook. Только два коммита этой задачи перенесены поверх новой базы (code `ffa67bd`, clean-check docs `902d039`), без force push и без возврата прежней истории.

README до раздела Evening и notebook сохранены из origin; раздел Evening сохранён из этой задачи. Побайтовое сравнение Git tree/blob `evening`, `web`, `src`, `tests`, `evening_app.py`, `Dockerfile.evening`, `compose.evening.yaml` с проверенной версией до переноса подтвердило идентичность. Поэтому чистый образ `e71b1dbd550b` содержит тот же runtime-код, что `ffa67bd`; изменились только история и README/notebook. Повторные профильные тесты: **69 passed, 1 warning**, OpenCode shell `sh_1d441757b001EiBAuFSV0CyT72`, exit 0.
