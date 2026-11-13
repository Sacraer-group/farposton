# Evening MVP — browser smoke (реальный браузер)

## Окружение и запуск

- Дата проверки: 2026-11-13.
- Цель: `http://127.0.0.1:18447` (запущенный контейнер Evening MVP, проверен `GET /evening` → 200).
- Браузер: Playwright `playwright@1.61.0` + Chromium/Chrome for Testing `158.0.8127.18` (playwright build `chromium-1254`, headless shell). Установка выполнена **только** в игнорируемой директории `.demo/evening-browser/` (npm official package), браузер — официальная поставка Playwright (`PLAYWRIGHT_BROWSERS_PATH=.demo/evening-browser/ms-playwright`, `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1` из-за отсутствия поддержки хоста `ubuntu26.04-x64` в проверке версий).
- Недостающие runtime OS-библиотеки для Chromium установлены из репозитория ОС (`sudo npx playwright install-deps chromium` в окружении `.demo/evening-browser`; пакеты X11/mesa/fonts/atk/pango/cups/vulkan и др.). `web/requirements.txt` и runtime Docker image не изменялись.
- Runtime-код не изменялся (параллельный review2).

Команда запуска:

```bash
cd /home/recara/farposton-evening
PLAYWRIGHT_BROWSERS_PATH="$PWD/.demo/evening-browser/ms-playwright" \
PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1 \
BASE_URL="http://127.0.0.1:18447" \
SHOTS_DIR="$PWD/.demo/evening-browser/screenshots" \
node tests/evening_browser_smoke.cjs
```

Тест: `tests/evening_browser_smoke.cjs` (Node/CommonJS, реальный Chromium через Playwright API, без статической DOM-проверки вместо браузера).

## Покрытые сценарии и фактический результат

Реальное браузерное e2e (Chromium headless) выполнено по инструкциям выше на **текущем** контейнере `http://127.0.0.1:18447` (health `200` на `/api/evening/events` и `/evening` перед прогоном). Итог `SMOKE_OK`, exit code `0`.

1. **Загрузка `/evening`**: страница открыта (`networkidle`), форма параметров отображена после загрузки событий, заголовок «Готовый вечер», явная маркировка вымышленных данных («Вымышленные данные»).
2. **Выбор реального события seed (live runtime)**: события получены из `/api/evening/events` контейнера. Выбрано scheduled-событие `04728bad-edd6-595b-98c2-35e47ebe7ae4` — «Концерт в вымышленном зале», дата `2026-11-14`, старт `2026-11-14T19:30:00+10:00` (Asia/Vladivostok). Дата в форме совпадает с датой события.
3. **Ввод валидного окна/признаков**: `meal_start=18:00`, `meal_end=19:15` (не позже начала события), `max_distance_m=3000`, предпочтение `laptop` (без пересечения required/preferred).
4. **Создание плана из UI (POST через форму)**: отправка формы, получен ответ `POST /api/evening/plans`, отображён блок результата.
5. **Результат содержит primary/plan B/явную маркировку вымышленности**: присутствуют «Основной вариант» и «План Б», маркировка вымышленности, источник/проверка `fictional_seed`, пояснения признаков («Подтверждённые признаки» / «Совпавшие предпочтения» / «Неизвестно»), сформирована ссылка `/evening/p/{id}`.
6. **Открытие ссылки `/evening/p/{id}`**: переход по реальной ссылке плана `http://127.0.0.1:18447/evening/p/df23a304-fbd9-488c-bec4-c179a1b492ac`, план виден на странице: событие, «Основной вариант», «План Б», «Текущий статус» (`scheduled`, проверено), источники/дата проверки (`fictional_seed`).
7. **Invalid input показывает ошибку без crash**: пересечение обязательного и предпочтительного признака (`dog`) дало ошибку формы «Обязательные признаки и предпочтения не должны пересекаться.»; форма остаётся видимой и submit доступен.
8. **Ширины 320 и 1280 без горизонтального переполнения**: для `/evening` и `/evening/p/{id}` при viewport 1280×900 и 320×900 `document.documentElement.scrollWidth <= clientWidth + 1` (`overflow: 0` в проверенных случаях).

## Скриншоты (только игнорируемые артефакты)

Перезаписаны в `.demo/evening-browser/screenshots/` (текущий прогон):
- `01-evening-loaded.png` — `/evening` загружен.
- `02-form-filled.png` — форма с реальным событием seed и валидными параметрами.
- `03-result.png` — результат с primary/plan B/маркировкой.
- `04-plan-page.png` — страница сохранённого плана `/evening/p/{id}`.
- `05-invalid-input.png` — ошибка валидации без crash.
- `evening-1280.png`, `evening-320.png`, `plan-1280.png`, `plan-320.png` — проверки переполнения.

## Дефекты

Воспроизводимых дефектов в проверенных сценариях не обнаружено (код не изменялся).

## Ограничения

- Проверка выполнена против конкретного запущенного контейнера `127.0.0.1:18447` и его текущего runtime seed; ID/даты событий зависят от seed этого инстанса (тест выбирает первое `scheduled` событие из live API).
- Проверена доступность UI/потоков и видимость плана; семантическая корректность ранжирования/бизнес-правил покрывается отдельными тестами, здесь — браузерный smoke.
- Headless Chromium; `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=1` использован для обхода проверки поддержки Ubuntu 26.04 при наличии установленных из OS-репозитория библиотек.
- План, созданный при прогоне, сохраняется в хранилище контейнера (обычное поведение API); внешние сервисы/2ГИС/клиентские данные не использовались.
