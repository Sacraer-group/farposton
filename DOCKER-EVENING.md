# Docker: Evening

Контейнер Evening (порт `127.0.0.1:18447`) в рабочем compose — один реальный сервис `evening` (`EVENING_DATA_MODE=real`). Demo — явный opt-in отдельным `docker run` (без второго сервиса в compose и без конфликтов портов).

- **Real**: снимок каталога 2ГИС `profiles-separated-v1` (4567 карточек, общепит 1344) монтируется readonly bind mount (`create_host_path: false`) и читается контейнером `uid 10001`; база/модель/ключи в образ не пакуются. Публичный файл событий `evening/data/vladivostok_events.json` (ручная проверенная подборка, 8 событий 15–27.11.2026, 3 площадки) включён в git/образ узким исключением `.gitignore`/`.dockerignore` (сырой каталог `data/` не пакуется).
- **Demo**: вымышленный runtime seed в `.demo/evening` (volume/state dir), `synthetic:true`, без доступа к реальной базе.

Недоступная реальная база или файл событий в real mode → API возвращает `503` (без fallback на fictional). Чистый clone: нужно предоставить каталог+события **или** запустить явный demo-режим.

## Real mode (compose, по умолчанию)

Требуется каталог `profiles-separated-v1` с `01_2gis/profiles.jsonl` и `02_generated/profiles.jsonl` на хосте (в окружениях, где предоставлен: `/home/recara/farposton/data/2gis/profiles-separated-v1`). Для чтения контейнером `10001:10001` при правах `700/600` выдаётся минимальный POSIX ACL только на root каталога, 2 папки и 2 JSONL (`r-x`/`r--`, `sudo -n setfacl`; при отсутствии — установка `acl` из ОС). World-chmod/прочие файлы/содержимое исходной базы не меняются.

```sh
sudo -n env EVENING_CATALOG_HOST_DIR=/absolute/path/to/profiles-separated-v1 \
  docker compose -f compose.evening.yaml up -d --build
```

Проверка:

```sh
sudo -n docker compose -f compose.evening.yaml ps
curl -i http://127.0.0.1:18447/health
curl -i http://127.0.0.1:18447/api/evening/events
```

## Demo mode (явный `docker run`)

Пример (после сборки образа `evening:local`; порт 18447 не должен конфликтовать с запущенным real-сервисом):

```sh
sudo -n docker build -f Dockerfile.evening -t evening:local .
sudo -n docker run --rm -p 127.0.0.1:18447:18447 \
  -e DEPLOYMENT_PROFILE=local -e WEB_DEMO_NO_AUTH=1 \
  -e EVENING_DATA_MODE=demo \
  -e EVENING_DEMO_DIR=/app/.demo/evening \
  -e EVENING_DB_PATH=/app/.demo/evening/plans.sqlite3 \
  evening:local
```

Для сохранения demo-планов добавьте отдельный volume, например `-v evening_demo_state:/app/.demo/evening` (не смешивать с production volume `evening_state` без необходимости).

## State и ограничения

- State/plans SQLite real-сервиса хранится в volume `evening_state` (`/app/.demo/evening`), сохраняется между перезапусками; старые планы не удаляются пересборкой.
- Каталог монтируется `read_only: true`, `bind.create_host_path: false`. Образ не содержит базу, модель, ключи, host `.demo`, `.git`, `.venv`, `.opencode`.
- Файл событий входит в образ (публичная проверенная подборка); при недоступности каталога/событий в real — `503`, без fallback. Синтетическая загруженность из карточек игнорируется.
- Факт наличия столов и статусы билетов не проверяются; источники/`checked_at` отображаются честно (mtime каталога не является датой проверки).

## Остановка

```sh
sudo -n docker compose -f compose.evening.yaml down
# с удалением state volume (удалит сохранённые планы):
sudo -n docker compose -f compose.evening.yaml down -v
```
