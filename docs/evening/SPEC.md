# «Готовый вечер» — спецификация MVP

Документ фиксирует контракт, правила и границы MVP для ветки `feature/evening-planner`. Источник — `docs/evening/FABLE-DRAFT.md` (автоматические обрывки/повторы) с уточнениями координатора. Весь seed и UI — вымышленные данные; это не измерение, не прогноз загруженности и не проверка наличия свободных мест.

## Маршруты

| Метод/путь | Назначение | Авторизация |
|---|---|---|
| `GET /evening` | Страница выбора события, даты, окна еды и предпочтений | `require_auth` |
| `GET /evening/p/{id}` | Страница сохранённого плана (ссылка) | `require_auth` |
| `GET /api/evening/events` | Только `scheduled` future-события, доступные для `POST /api/evening/plans` (rescheduled/cancelled/past не возвращаются) | `require_auth` |
| `POST /api/evening/plans` | Создание плана | `require_auth` |
| `GET /api/evening/plans/{id}` | Чтение сохранённого плана | `require_auth` |

Авторизация API соответствует существующему `web.auth.require_auth`: без валидной сессии API возвращает `401`. В local demo с `WEB_DEMO_NO_AUTH=1` пользователь может получить ссылку и открыть страницы/читать план без учётки только в этом режиме (поведение `session_is_valid`).

## Вход `POST /api/evening/plans`

Тело — JSON-объект. Лишние поля, неизвестные поля, неверные типы и нарушение границ дают `422`. Валидация строгая.

```json
{
  "event_id": "string, UUID, 1-64 символа",
  "date": "YYYY-MM-DD, дата события в Asia/Vladivostok",
  "meal_start": "HH:MM",
  "meal_end": "HH:MM",
  "required_features": ["dog", "laptop", "children", "terrace", "reservations"],
  "preferred_features": ["dog", "laptop", "children", "terrace", "reservations"],
  "max_distance_m": 1500
}
```

Правила валидации:
- `event_id`: строка, валидный UUID (формат проверяется), длина 1–64.
- `date`: `YYYY-MM-DD`, должна совпадать с датой события в `Asia/Vladivostok`.
- `meal_start`, `meal_end`: `HH:MM` (24 часа), `meal_end > meal_start`, `meal_end <= starts_at_event_local_time`.
- Интервал еды `[meal_start, meal_end)` проверяется полностью: место должно быть открыто на всём интервале.
- `required_features`, `preferred_features`: массивы, max 5 элементов, только значения enum `{dog,laptop,children,terrace,reservations}`, уникальные внутри массива, без пересечения между required и preferred.
- `max_distance_m`: integer, 100–5000.
- `bool` не принимается как `int` там, где ожидается integer; типы проверяются строго.

## Seed и runtime-данные

- Runtime fictionalseed: 25 мест и 6 событий на ближайшие даты, хранится отдельно в игнорируемой `.demo/evening/`.
- Seed не перезаписывается при повторных запусках; по умолчанию открыт только этот источник.
- UI чётко помечает данные как вымышленные.
- `GET /api/evening/events` возвращает только события со статусом `scheduled` и `starts_at` в будущем — то есть доступные для создания плана через `POST /api/evening/plans`; `rescheduled`, `cancelled` и `past` не выбираются.
- Отсутствующий/удалённый event не превращается в 404 сохранённого плана: `GET plans/{id}` возвращает снимок и `current` со статусом; сохранённый план с `current.event_status = rescheduled` остаётся доступным по `GET` (снимок не меняется).

## Рекомендация

- Рекомендатор — чистая функция, без LLM, 2ГИС и Forecast.
- Кандидаты: открыты на всём `[meal_start, meal_end)`, в пределах `max_distance_m`, все `required_features` подтверждены (`true` в seed). Неизвестное required не проходит.
- Порядок: больше совпавших `preferred_features`, затем меньше `distance_m`, затем `id` места.
- `alternative` (plan B): другая `category`, если есть подходящая; иначе следующее по тому же порядку. Те же ограничения (окно, distance, required). Если подходящих мест меньше двух — `409 not_enough_places`.

## Выход `POST /api/evening/plans` (201) и `GET /api/evening/plans/{id}` (200)

```json
{
  "id": "uuid4",
  "created_at": "2026-11-13T19:00:00+10:00",
  "expires_at": "2026-11-14T19:00:00+10:00",
  "synthetic": true,
  "request": {
    "event_id": "...",
    "date": "YYYY-MM-DD",
    "meal_start": "HH:MM",
    "meal_end": "HH:MM",
    "required_features": [],
    "preferred_features": [],
    "max_distance_m": 1500
  },
  "event": {
    "id": "...",
    "title": "...",
    "starts_at": "ISO +10:00",
    "status": "scheduled"
  },
  "primary": {
    "place_id": "...",
    "name": "...",
    "category": "...",
    "distance_m": 420,
    "confirmed": ["dog"],
    "unknown": ["terrace"],
    "matched_preferred": ["dog"]
  },
  "alternative": {
    "place_id": "...",
    "name": "...",
    "category": "...",
    "distance_m": 500,
    "confirmed": [],
    "unknown": [],
    "matched_preferred": []
  },
  "current": {
    "event_status": "scheduled|cancelled|rescheduled|past|unavailable|stale",
    "starts_at": "ISO +10:00 или null",
    "stale": false,
    "checked_at": "ISO +10:00"
  },
  "sources": [
    {"name": "fictional_seed", "checked_at": "ISO +10:00", "note": "Вымышленные данные, не реальная проверка"}
  ],
  "disclaimer": "Наличие столов не проверено. Данные вымышленные. Минуты пути и гарантии мест не показываются."
}
```

- `snapshot` сохраняется в SQLite; `current` пересчитывается при чтении (revalidate) и не меняет снимок.
- `current.event_status`: `cancelled`, `rescheduled`, `past`, `unavailable` (отсутствует/удалён), `stale` (источник недоступен/нельзя проверить) — по состоянию seed на момент чтения.
- План живёт 24 часа: после истечения `GET` возвращает `410`.

## Ошибки

| Код | `error` | Когда |
|---|---|---|
| 401 | `unauthorized` | Нет валидной сессии (соответствует `require_auth`) |
| 404 | `event_not_found` | `event_id` неизвестен при создании плана |
| 404 | `plan_not_found` | План не найден (валидный UUID, запись отсутствует) |
| 409 | `event_unavailable` | Событие отменено/прошло/rescheduled/недоступно для создания плана |
| 409 | `not_enough_places` | Менее двух подходящих мест (нет primary+alternative) |
| 410 | `plan_expired` | План старше 24 ч |
| 422 | `validation_error` | Формат, типы, границы, UUID, дата, окно, лишние/неизвестные поля, bool/int, дубли/пересечение признаков; невалидный UUID в `GET /api/evening/plans/{id}` |

Тело ошибки: `{"detail": {"error": "<code>", "message": "<понятный текст>"}}` (или совместимая форма FastAPI с сохранением кода).

## UI

- `/evening`: выбор события, даты, окна еды, required/preferred признаков, max_distance_m; показ результата, plan B, объяснения (confirmed/unknown/matched_preferred), копирование ссылки, состояния loading/error/empty; mobile/keyboard доступны.
- `/evening/p/{id}`: отображение сохранённого плана, `current` статус, источники/дата проверки, disclaimer.
- Без минут пути и без гарантий мест. Данные явно вымышленные.

## Ограничения MVP

- Никаких LLM или внешних API в MVP.
- Никакого raw текста/контактов в хранилище.
- SQL параметризованный.
- Текущие запросы к 2ГИС не выполняются.
