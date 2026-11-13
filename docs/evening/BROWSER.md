# Evening MVP — browser smoke (реальный браузер)

## Текущий прогон (real mode, 2026-11-13)

- Цель: `http://127.0.0.1:18447` (контейнер `farposton-evening-evening-1`, `EVENING_DATA_MODE=real`, readonly bind mount каталога `profiles-separated-v1`, health `200`).
- Браузер: Playwright `playwright@1.61.0` + Chromium/Chrome for Testing `158.0.8127.18` (`chromium-1254`, headless), установленный в игнорируемой `.demo/evening-browser/` (`PLAYWRIGHT_BROWSERS_PATH=.demo/evening-browser/ms-playwright`, `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1`). Runtime OS-библиотеки ранее установлены из репозитория ОС; `web/requirements.txt` и runtime Docker image не менялись.
- Тест: `tests/evening_browser_smoke.cjs` (адаптирован под real: проверка маркировки «Реальные данные»/отсутствия `fictional_seed`, окно еды вычисляется от начала выбранного события, не hardcode 17–18).

Команда запуска:

```bash
cd /home/recara/farposton-evening
PLAYWRIGHT_BROWSERS_PATH="$PWD/.demo/evening-browser/ms-playwright" \
PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 \
BASE_URL="http://127.0.0.1:18447" \
SHOTS_DIR="$PWD/.demo/evening-browser/screenshots-real" \
node tests/evening_browser_smoke.cjs
```

Итог: **`SMOKE_OK`**, exit code `0`.

Покрыто (факт):
1. Загрузка `/evening`, форма после загрузки 8 реальных событий (`synthetic:false`), заголовок «Готовый вечер», маркировка «Реальные данные», без «Вымышленные данные».
2. Выбор scheduled-события из live `/api/evening/events`: `91fb6bb0-0c22-511c-a03f-ffec0649c37e` «Хоккей: „Тайфун“ – „Динамо Санкт-Петербург“», `2026-11-15T17:00:00+10:00`, дата совпадает. Окно еды вычислено от начала события: `15:00–16:30` (ev `17:00`), `max_distance_m=3000`.
3. Создание плана из UI (`POST /api/evening/plans` → `201`): отображены «Основной вариант» и «План Б», маркировка реальных данных, источники/каталог (`2gis_catalog_snapshot`/«Источники»), пояснения признаков («Подтверждённые признаки»/«Совпавшие предпочтения»/«Неизвестно»), без `fictional_seed`, сформирована ссылка `/evening/p/{id}`.
4. Открытие `/evening/p/03b5e42e-f863-4a92-a8a3-b9423a2ca573`: план виден (событие, основной/план Б, «Текущий статус» `scheduled`, «Источники», без `fictional_seed`).
5. Invalid input (пересечение required/preferred `dog`) → ошибка «Обязательные признаки и предпочтения не должны пересекаться.» без crash, форма/submit доступны.
6. Ширины 1280 и 320 без горизонтального переполнения (`scrollWidth <= clientWidth + 1`, overflow `0`) для `/evening` и `/evening/p/{id}`.

Скриншоты (игнорируемые): `.demo/evening-browser/screenshots-real/` (`01-evening-loaded.png`, `02-form-filled.png`, `03-result.png`, `04-plan-page.png`, `05-invalid-input.png`, `evening-1280.png`, `evening-320.png`, `plan-1280.png`, `plan-320.png`).

Дефекты: воспроизводимых в проверенных сценариях не обнаружено.

Ограничения: проверка против конкретного запущенного контейнера/текущих данных (выбирается первое `scheduled` событие live API); семантика ранжирования/правил покрывается тестами, здесь — браузерный smoke. Headless Chromium. План, созданный прогоном, сохраняется в volume контейнера (обычное поведение API); внешние сервисы/2ГИС/клиентские данные не использовались.

## История (demo, 2026-11-13)

Предыдущий прогон выполнялся против demo/fictional runtime (`SMOKE_OK`), проверял маркировку «Вымышленные данные», `fictional_seed`, фиксированное окно `18:00–19:15`, seed-событие `04728bad-edd6-595b-98c2-35e47ebe7ae4` и план `/evening/p/df23a304-fbd9-488c-bec4-c179a1b492ac`. Скриншоты: `.demo/evening-browser/screenshots/`. Этот отчёт сохранён как исторический контекст; текущий факт — real-mode прогон выше.
