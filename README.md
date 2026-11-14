# Берег — квартира под ваш повседневный график

Подбор долгосрочной аренды во Владивостоке по бюджету, множеству поездок недели,
интерьеру и опубликованным сведениям дома. Список, карта, свайпы и сравнение используют
общие правила. У публичного демо раздельные анонимные профили браузеров и CSRF.

## Поездки и дома

`/trips`: подтвердите места поиском или точкой на карте, добавьте независимые поездки
и цепочки с днями и временем выезда. Даты — следующая календарная неделя во Владивостоке.
Расчёт запускается явно, порциями, с отменой. Подходящие полностью рассчитанные варианты
сортируются по недельному времени; неполные отмечаются, нарушающие ограничения исключаются.
Пешие маршруты обозначены отдельно. Время — оценка 2ГИС, не гарантия прибытия.

Технические пределы:100 мест,200 участков графика; максимум10000 датированных участков
на расчёт и30000 в памяти сервера. Для большого графика сначала сузьте фильтры квартир.
По умолчанию100 запросов API на браузер в сутки и1000 на сервер; задаются
`FLATS_TRIP_SESSION_BUDGET` и `FLATS_TRIP_GLOBAL_BUDGET`. Счётчики хранятся в SQLite.
Задания хранятся в памяти один час; после перезапуска нужен повторный расчёт.
Сервер запускается одним процессом с потоками. Сырые ответы API не сохраняются.

Ключ — только в серверном файле `FLATS_2GIS_KEY_FILE`. Проверка существующего ключа:
маршрутный API ответил маршрутом; каталог Places вернул код403, поэтому доступен выбор
точки на карте. Полный лимит тарифного плана отдельно не подтверждён.

Паспорт открывается из объявления и сравнения. Импорт опубликованного снимка:

```bash
flats import-house-passports research/house-passports/2026-11-14/farposton-house-data-2026-11-14.zip
```

Снимок:48 домов,50 привязанных объявлений,3005 связей публикаций об отключениях,
433 строки планов ремонта,37 сообщений об исполнении,3 конфликта УК. Это архивные
публикации и утверждения источников, не рейтинг дома и не текущее состояние услуг.
Повторный импорт не создаёт дублей, ошибки откатывают транзакцию.

## Публичный запуск

Для нескольких посетителей задайте `FLATS_PUBLIC_DEMO=1`, постоянный случайный
`FLATS_SECRET_KEY` и `FLATS_SECURE_COOKIE=1` за HTTPS. Без PUBLIC_DEMO сохраняется
прежний локальный однопользовательский режим. Фильтры и выбранное фото хранятся в раздельных сессиях посетителей; устаревшие сессии удаляются через30 дней.
`compose.yaml` использует существующий внешний том `bereg-data`; ключ монтируется
отдельным закрытым файлом. Docker-образ не содержит ключей, базы и фотографий.

CI проверяет pytest и сборку контейнера. После push в main deploy использует
ограниченный SSH-вход, резервную копию SQLite, проверку кандидата и переключение Caddy.
Описание продолжения и ограничения: [docs/implementation-status.md](docs/implementation-status.md).

---

# Flats — Vladivostok long-term rental shortlist

Local study app: filter long-term rentals in Vladivostok, see clear monthly and move-in costs per offer, and the questions to ask before a viewing.

## Run (Python 3.11+)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
flats init-db
flats demo                      # 50 real offers (Metrapi snapshot, 2026-11-13)
flats demo --set invented       # optional: 26 invented edge-case offers
flask --app flats.web:create_app run --host 127.0.0.1
pytest -q
```

Visual interior search (optional, CPU is enough, ~12 min for 800 photos):

```bash
pip install -e '.[ml]'
flats embed            # downloads photos to data/photos and the SigLIP 2 model
```

Then open a flat, click «Найти похожие интерьеры» under a photo: the list is re-ordered by visual similarity among flats that pass the filters.

Open http://127.0.0.1:5000: search, flat page, «Избранное» (compare up to 5), «Свайпы». Save, skip and undo history is stored in this browser’s local storage, survives reloads and browser restarts, and is separate on other browsers or devices. Clearing site data deletes it. Legacy SQLite decisions remain intact and are not imported. Filters and the sample photo use visitor sessions in PUBLIC_DEMO; the default local mode remains single-user. The database file is `data/flats.db` (override with `FLATS_DB`).

## Advisor

Open «Мой выбор» (`/advisor`) to set requirements and a priority, review candidates and evidence, compare them, and prepare viewing questions. The route link opens an external map with the destination; choose the starting point and travel mode there. House records are dated evidence, not live outage status.

Without a configured model provider, the advisor clearly uses retrieval. An optional OpenAI-compatible provider can be configured locally with `FLATS_LLM_BASE_URL` and `FLATS_LLM_MODEL`. The app appends `/chat/completions` to the base URL; for a compatible service, the base may end in `/v1`. Set `FLATS_LLM_API_KEY` if the provider requires a key, and keep it secret; a keyless local provider does not need one. Live provider behavior has not been verified. See [decision flow and demo](docs/decision-flow.md).

## Refresh real data

Needs a Metrapi API key. Use the official API only: their terms forbid scraping the website.

```bash
METRAPI_KEY=... python scripts/fetch_metrapi.py --limit 50
flats demo
```

## Limitations

Visual similarity matches room type well, style matching is not verified (`docs/ml-check.md`). Photos load from the source site and disappear when a listing is removed. Plan and contracts: `docs/`.
