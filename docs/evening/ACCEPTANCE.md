# Evening — приёмка

## Режимы

- `EVENING_DATA_MODE=real` по умолчанию (production compose — один сервис `evening`). Недоступная реальная база/события или ошибка доступа (OSError/Permission) → `503`, **никогда не fallback на fictional**. Demo — явный opt-in отдельным `docker run` (без второго сервиса в compose).
- Старые demo тесты выставляют `EVENING_DATA_MODE=demo`.

## Real data

- Каталог: полный `profiles-separated-v1` (4567) для venue coords; рекомендации только общепит (нормализованные категории, `web.places.CATEGORY_LABELS`). Реальные поля сохранены: `id/name/address/lat/lon/rating/review_count/schedule/attribute_groups/updated_at`. `synthetic_congestion` игнорируется. `checked_at` каталога `null/unknown` (mtime не дата проверки).
- События: публичный `evening/data/vladivostok_events.json` (8, 15–27.11.2026, 3 площадки, проверенные URL/venue ID) включён узким исключением `.gitignore`/`.dockerignore`. Строгая валидация: UUID, `https://` `source_url`, aware `starts_at` (+10), `date == starts_at` (VL), `synthetic:false`, обязательные поля, корректный `checked_at`; повреждение/невалидность (в т.ч. всех записей) → `503`, не тихий успех. `venue_id` резолвится в полном каталоге; иначе `409 event_venue_unavailable`.
- Признаки true только при позитивных отметках: `dog`=«Можно с собакой», `laptop`=«Можно с ноутбуком», `children`=«Детское меню», `terrace`=«Летняя веранда»/«Столики на улице» (сезонность не проверена), `reservations`=«Заказ столиков» (не подтверждённая бронь). Отсутствие → `unknown`; `unknown` required не проходит.
- Расписание: weekday реальной даты, overnight (предыдущий день), merge смежных/перекрывающихся сегментов, `24:00` как конец дня, полный `[start,end)` поминутно; missing/unknown/special → не обещать открыто.
- Distance: haversine от координат площадки события до точки места (не минуты, не fake precomputed).

## Источники и состояние

- Real ответы: `synthetic:false`, источники каталог+событие с `source_url` (https), `checked_at`, примечания; наличие столов/статусы билетов не проверены. `checked_at` старше 24h → видимое предупреждение при list/plan/read. Не писать «автоматически проверено».
- `current` перепроверяет файлы; snapshot не меняется. Статусы: `scheduled/cancelled/rescheduled/past/unavailable/stale`. Изменение канонического `starts_at`/`venue_id` у события с тем же `id` и `status=scheduled` (время по `datetime`, `venue_id` при наличии обоих) даёт `current.event_status=rescheduled` с актуальным `starts_at`, без пересчёта плана; явная `cancelled` в приоритете.
- Старые fictional планы: сохраняют `synthetic:true` и источники; при отсутствии события `current=unavailable`; планы/volume не стираются.

## Матрица требований → тесты

| Область | Проверка |
|---|---|
| 503/no fallback | отсутствующие events/catalog в real → 503, нет fictional |
| Adapter | schematic temp JSONL, полный каталог для venue, рекомендации только общепит, реальные поля/`synthetic:false` |
| Distance | haversine known pair, dateline, error coords исключены, от venue события |
| Schedule | weekday, overnight prev day, holes/split, full interval, minute precision, missing/unknown/special |
| Mapping | только позитивные отметки true, отсутствие unknown, terrace/reservations semantics |
| Stale/source | checked_at > 24h warning list/plan/read, source links/meta, synthetic markers |
| Current | removed/cancelled/rescheduled/past/unavailable/stale, snapshot unchanged; time/venue change (same id/status) → rescheduled |
| Legacy plans | fictional `synthetic:true` читается в real как unavailable при отсутствии события |
| F1–F7 demo | существующие сценарии в явном demo mode |
| F8 Docker | real default env, readonly bind mount `create_host_path:false`, no DB packed, docs/modes |

## Документация/артефакты

Обновить `docs/evening/SPEC.md`, `ARCHITECTURE.md`, `ACCEPTANCE.md`, `VERIFICATION.md`, добавить `REAL-DATA-VERIFICATION.md`, обновить `DOCKER-EVENING.md`, `README.md`, `compose.evening.yaml`, `Dockerfile.evening`, UI.
