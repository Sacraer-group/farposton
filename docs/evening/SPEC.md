# Evening — спецификация

MVP «Готовый вечер»: подбор места для еды до события. Поддерживает два режима данных:

- **`real` (по умолчанию)**: каталог — снимок 2ГИС-карточек `profiles-separated-v1` (4567 карточек, общепит 1344), события — ручная проверенная подборка `evening/data/vladivostok_events.json` (8 событий 15–27.11.2026, 3 площадки; все 7 URL и venue ID проверены data-агентом и независимым review). Синтетическая загруженность из карточек игнорируется.
- **`demo` (явный opt-in для fixtures)**: вымышленный runtime seed (25 мест, 6 событий) в `.demo/evening/`, не перезаписывается.

Недоступная реальная база или файл событий в `real` → `503` (в т.ч. при ошибке доступа/OSError к каталогу/файлам), **без fallback на fictional**. Повреждение/невалидность данных событий (включая все записи) не должно становиться тихим успехом.

## Режимы и конфигурация

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `EVENING_DATA_MODE` | `real` или `demo` | `real` |
| `EVENING_CATALOG_DIR` | Корень `profiles-separated-v1` (`01_2gis/`, `02_generated/profiles.jsonl`) | — (обязателен для `real`) |
| `EVENING_EVENTS_PATH` | Путь к `vladivostok_events.json` | `evening/data/vladivostok_events.json` |
| `EVENING_DEMO_DIR` | Runtime demo seed/state dir (demo mode) | `.demo/evening` |
| `EVENING_DB_PATH` | SQLite планов | `<EVENING_DEMO_DIR>/plans.sqlite3` |

Production compose: один сервис `evening`, `EVENING_DATA_MODE=real`, readonly bind mount каталога с `create_host_path: false`, env host path config (`EVENING_CATALOG_HOST_DIR`). Demo — явный opt-in отдельным `docker run` (без второго сервиса/конфликтов портов в compose). Публичный `evening/data/vladivostok_events.json` включён в git/образ узким исключением `.gitignore`/`.dockerignore`.

## Источники данных (real)

- **Каталог мест**: полный снимок для поиска координат площадок событий по `venue_id` (любой ID в полном каталоге 4567). Рекомендации — **только общепит** по нормализованным категориям (`web/places.py`-совместимая нормализация rubrics): кафе/кофейни/рестораны/бары/быстрое питание/пекарни. Карточки 2ГИС (даже если `profile_type=synthetic` в файле профилей) используются как карточки; `synthetic_congestion`/загруженность **полностью игнорируется**.
- **События**: ручная проверенная подборка, файл готовится отдельно (data-агент). Метаданные с источниками могут быть в image.

Сохраняются реальные поля карточек: `id`, `name`, `address`, `lat`, `lon`, `rating`, `review_count`, `schedule`, `attribute_groups`, `updated_at`. UI/API должны явно указывать: каталог — снимок, события — ручная проверенная подборка, факт наличия столов неизвестен, статусы билетов неизвестны.

## Схема событий

```json
{
  "schema_version": 1,
  "events": [
    {
      "id": "UUID",
      "title": "string",
      "starts_at": "aware ISO 8601, +10:00",
      "date": "YYYY-MM-DD",
      "status": "scheduled|cancelled|rescheduled|past",
      "synthetic": false,
      "venue_id": "любой ID полного каталога 4567",
      "venue_name": "string",
      "source_url": "https://...",
      "source_name": "string",
      "checked_at": "ISO datetime",
      "age_restriction": "optional string"
    }
  ]
}
```

`venue_id` обязан находить координаты в полном каталоге; если нет — площадка недоступна (не выдумывать/не подменять рестораном). `GET /api/evening/events` возвращает только `scheduled` future (для создания плана). `current` при чтении плана перепроверяет файлы; если при том же `id` и `status=scheduled` изменились каноническое `starts_at` или `venue_id` относительно снимка (сравнение времени по `datetime`, эквивалентный offset не считается переносом; `venue_id` — если оба значения присутствуют), `current.event_status=rescheduled` с актуальным `starts_at`, snapshot не меняется и план не пересчитывается автоматически. Явная `cancelled` в приоритете.

## Маппинг признаков (real)

`true` только при проверяемой положительной отметке в `attribute_groups[].attributes[].name`:

| Признак | Отметка |
|---|---|
| `dog` | «Можно с собакой» |
| `laptop` | «Можно с ноутбуком» |
| `children` | «Детское меню» (UI label) |
| `terrace` | «Летняя веранда» или «Столики на улице» (сезонная доступность не проверена) |
| `reservations` | «Заказ столиков» (не подтверждённая бронь) |

Отсутствие → `unknown` (не `false`). `unknown` не проходит required.

## Расписание (real)

- Проверка по weekday реальной даты (Asia/Vladivostok), минутная точность, полный интервал `[meal_start, meal_end)`.
- Overnight: интервал предыдущего дня, пересекающий полночь, учитывается для текущего дня.
- Split segments/holes: интервал должен полностью помещаться в доступный сегмент.
- Missing/неизвестное/специальные расписания (комментарии, нестандартные поля) → не обещать открыто (`unknown`, место не проходит фильтр открытости).
- Праздничные исключения не гарантируются; если не подтверждено — не обещать.

Demo mode сохраняет прежнюю упрощённую модель `hours {open, close}`.

## Расстояние

`distance_m` — Haversine от координат **площадки конкретного события** (`venue_id` → точка в полном каталоге) до точки места. Не предвычислять fake-значения и не показывать минуты пути.

## Маршруты

| Метод/путь | Назначение | Авторизация |
|---|---|---|
| `GET /evening` | UI выбора | `require_auth` |
| `GET /evening/p/{id}` | UI сохранённого плана | `require_auth` |
| `GET /api/evening/events` | События, доступные для создания плана | `require_auth` |
| `POST /api/evening/plans` | Создание плана | `require_auth` |
| `GET /api/evening/plans/{id}` | Чтение плана + `current` | `require_auth` |
| `GET /health` | Liveness (режим) | public |

`503` в real mode при недоступных каталоге/событиях (`real_data_unavailable`/`events_unavailable`). `409 event_venue_unavailable` если площадка события не найдена в каталоге с координатами.

## Вход `POST /api/evening/plans`

Строгая валидация (Pydantic, `extra=forbid`, строгие типы): `event_id` UUID, `date` совпадает с датой события (Asia/Vladivostok), `meal_start`/`meal_end` `HH:MM`, `meal_end > meal_start`, `meal_end <= starts_at`, `max_distance_m` 100–5000 (int, не bool), признаки из `{dog,laptop,children,terrace,reservations}` max 5 уникальных, без пересечения required/preferred.

## Рекомендация

Чистая функция: кандидаты открыты на всём `[meal_start, meal_end)`, `distance_m <= max_distance_m`, все required `true`. Сортировка: больше matched preferred, затем меньше distance_m, затем id. `alternative` — другая категория если есть, иначе следующее. Менее двух подходящих → `409 not_enough_places`.

## Снимок/чтение плана

- Snapshot сохраняется в SQLite, TTL 24h. `GET` после истечения → `410 plan_expired`.
- `current` пересчитывается при чтении (revalidate) по актуальным файлам, snapshot не меняется.
- `current.event_status`: `scheduled|cancelled|rescheduled|past|unavailable|stale` (`rescheduled` также при обнаруженном изменении времени/площадки относительно снимка без явного статуса `rescheduled` в источнике).
- Старые fictional/demo планы сохраняют `synthetic:true` и свои источники; при отсутствии события `current` → `unavailable`, план не стирается.

## Источники, synthetic, stale

- Real: `synthetic:false` для событий/мест/источников; источники включают каталог-снимок и источник события с `source_url` (кликабельный https), `checked_at`, понятные примечания. Без автоматического «проверено».
- `checked_at` события старше 24h → видимое предупреждение при list/plan/read.
- Demo: `synthetic:true`, источник `fictional_seed`.
- Disclaimer: наличие столов и статусы билетов не проверены.

## UI

`/evening` и `/evening/p/{id}`: событие/дата/окно/предпочтения, результат + plan B + объяснения, копирование ссылки, loading/error/empty/mobile/keyboard. Показывать год/таймзону/площадку/адрес, кликабельный https source, предупреждения stale, корректные метки признаков. Без минут пути и без гарантий мест. Убрать hardcoded fictional в real mode.

## Ограничения

Без LLM/внешних API/2ГИС-запросов в MVP. Без raw текста/контактов в хранилище. SQL параметризованный. Синтетическая загруженность игнорируется. Clean clone требует каталог+события или явный demo.
