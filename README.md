# Forecast

## Автоматический демопрогноз на семь дней — 13.11.2026

В карточке каждой точки доступен почасовой прогноз ансамбля на сегодня и шесть
следующих дней; день и час выбираются в графике. При запросе дата определяется
по времени Владивостока, последние семь дней искусственной истории заново
генерируются для этой точки. Открытая карточка проверяет смену дня и повторяет
запрос; после сетевой ошибки повторяет попытку при следующей проверке или
возврате на вкладку. Это не измерения, не реальная загруженность и не наличие
свободных мест. Профили загруженности 2ГИС в расчёте не используются.

Модель обучалась на прогноз следующего дня. Для дней 2–7 её собственные
предыдущие прогнозы последовательно подставляются в историю. Качество такого
многодневного прогноза отдельно не измерялось. Для часов без значения за
соответствующий день предыдущей недели прогноз не показывается.

API `/api/places/{id}/forecast` использует `.demo/venue-ensemble-extended-20261113-v1/ensemble.joblib`
и `data/2gis/demo-history-extended-v1/history.sqlite3`. Источник и модель
должны совпадать по контрольной сумме и быть помечены как синтетические;
при отсутствии или несовместимости отображается ошибка, а не подмена
результата. База используется для проверки происхождения модели; свежая
демоистория создаётся при запросе в памяти и никуда не записывается.

## Расширенная синтетическая история — 13.11.2026

Для эксперимента создана отдельная история с 13 августа по 12 ноября 2026 года
тем же генератором: `data/2gis/demo-history-extended-v1/history.sqlite3` на
сервере. Все 141 577 строк исходного месяца совпали с исходной базой; она не
изменялась. Создание и проверка совпадения: `tools/extend_venue_history.py`.
Добавленные прошлые значения искусственные, не реальные наблюдения.

Ансамбль переобучен на расширенной истории: 4 396 889 строк для итогового
обучения бустинга, 250 000 строк для ExtraTrees. Валидация и последние семь
тестовых дней остались прежними, данные по 4567 точкам. На тех же 394 361
примерах MAE уменьшилась с 6,8885 до 6,6250, RMSE — с 9,0635 до 8,7134;
R² вырос с 0,8651 до 0,8753, доля ошибок в пределах 10 пунктов — с 74,30%
до 75,82%. Базовое правило «неделю назад» сохранило MAE 7,7457. Проверочный
период не использовался для обучения, но уже применялся в предыдущем
эксперименте: для независимой оценки нужна ещё одна отложенная история.

Модель и метрики на сервере и Mac: `.demo/venue-ensemble-extended-20261113-v1/`.
Сравнение не подтверждает качество на реальной
загруженности или наличие свободных мест.

## Ансамбль для демоистории — 13.11.2026

На сервере обучен ансамбль HistGradientBoosting и ExtraTrees для прогноза
искусственного индекса на следующий день по часам и предыдущим значениям.
Реальные профили загруженности 2ГИС в обучении не использовались. Данные
синтетические и специально настроены для презентации; значения не измеряют
занятость, число людей или свободных столов.

Обучение: 20–29 октября; выбор весов на 30 октября — 5 ноября; затем повторное
обучение до 5 ноября. На ExtraTrees использованы 250 000 примеров, на
HistGradientBoosting — все 965 322. Проверка — 6–12 ноября, 394 361 доступный
почасовой пример по 4567 точкам. Для каждого дня прогноза разрешено использовать
предшествующие дни, в том числе предыдущие дни проверочного периода; для
обучения и выбора весов проверочный период не использовался.

На этом демонаборе ансамбль дал MAE 6,89 и RMSE 9,06 пункта индекса, R² 0,865;
74,30% ошибок не превышают 10 пунктов. Правило «тот же час неделю назад» дало
MAE 7,75, RMSE 10,48, R² 0,820 и 72,22% ошибок в пределах 10 пунктов.
MAE ансамбля ниже на 11,07%, но отдельный HistGradientBoosting показал MAE 6,83,
немного лучше ансамбля. Эти результаты не подтверждают качество на реальных
данных; для этого нужны реальные наблюдения и отдельная проверка по времени.

Код обучения: `tools/train_venue_ensemble.py`; сохранённая модель и метрики на
Mac и сервере — `.demo/venue-ensemble-20261113-v1/`. В сайт подключён ансамбль
из расширенной истории, описанный выше.

## «Не тесно»: демо поиска заведений — 13.11.2026

Для всех 4567 точек создана отдельная синтетическая история за календарный
месяц: 13.10–12.11.2026 включительно, 31 день, по 24 часовых слота. Хранилище
`data/2gis/demo-history-presentation-v1/history.sqlite3` содержит 141577 строк
по дням и 31969 недельных профилей, рассчитанных из этой же демо-истории;
профили и даты различаются, 0 точек без значений. Это искусственный индекс,
а не наблюдения, посещаемость, наличие столов или проверенный прогноз. 89
профилей 2ГИС не использовались как основа для генерации. Расписание 102 точек
отсутствовало, у одной не было пригодных часовых интервалов: для синтетики
условно использовано 09:00–21:00, что явно отмечено в данных и интерфейсе.
Предыдущие версии истории v1 и v2 сохранены отдельно.

По поручению пользователя подготовлен показательный сценарий: у части заведений
выражены пики в пятницу и субботу, у других в то же время индекс ниже. Сценарий
искусственный и подобран для объяснения идеи, не является подтверждением
реального поведения заведений. Кнопка «Демо: пт 19:00» включает его фильтры;
сортировка по демо-индексу доступна только для синтетических профилей. Значение
в списке и типовой недельный график рассчитаны из того же месячного сценария.

В каждой карточке доступны график по дням и выбор даты и часа; история
загружается с `/api/places/{id}/history`. `FARPOSTON_HISTORY_DB` позволяет
указать другое хранилище. Генератор `python -m tools.generate_venue_history`
не перезаписывает существующую базу; формат и повторная генерация описаны в
`data/2gis/demo-history-presentation-v1/README.md`. Данные размещены на Mac и сервере;
совпадение файлов, целостность SQLite и полнота по ID проверены. Сайт отклоняет
историю с неподтверждённой синтетической маркировкой. Выбор даты и часа и
показ исходного профиля 2ГИС отдельно от демо проверены через SSH-туннель.

При проработке интерфейса использованы референсы с картой и карточкой места:
[REZERVI](https://dribbble.com/shots/26326096-Restaurant-Map-UI-REZERVI-Web-App)
и [Atmosfy](https://agostinasalvatore.com/case-studies/atmosfy-map), а также
[почасовые графики](https://k-series-support.lightspeedhq.com/hc/en-us/articles/18234292249883-Sales-Reports).

Для локального режима без авторизации главная страница теперь открывает новый
интерфейс поиска заведений Владивостока. На сервере `vibe1` служба
`farposton.service` слушает `127.0.0.1:8445`; через существующий SSH-туннель
на Mac демо открывается по `http://127.0.0.1:18445/`. Также доступны `/places`
и прежний экран Forecast на `/forecast`. В обычном режиме с авторизацией
корень `/` сохраняет прежний экран. Сервис не публиковался в интернет.

В каталоге 4567 точек из неполной выборки, в категориях заведений питания —
1344. Поиск, категории, часы, сортировка, фильтр источника, карта и карточки
используют локальные файлы карточек
`data/2gis/profiles-separated-v1/{01_2gis,02_generated}/profiles.jsonl`;
почасовые демографики используют историю текущего сценария.
Профили 2ГИС и искусственные профили разделены, происхождение показано в
интерфейсе. Индекс демо не является измерением или прогнозом; шкала профилей
2ГИС не расшифрована, текущие свободные места неизвестны. Расписание —
обычное, праздничные исключения не учтены; часы без данных не считаются
достоверно закрытыми. Для карты используется Leaflet и тайлы OpenStreetMap;
без сети список продолжает работать. Текущие запросы к API 2ГИС не выполняются.

Новая серверная логика: `web/places.py`, маршруты — `web/routes_places.py`,
интерфейс — `web/static/places.html` и `web/static/assets/places.*`. В
`FARPOSTON_PLACES_DATA_DIR` можно указать другую папку с тем же устройством
файлов; каталог загружается один раз при первом запросе и обновляется после
перезапуска сервиса. Происхождение карточек и права на использование 2ГИС
остаются предметом проверки перед внешней публикацией.

На Mac и сервере `make test` прошёл: 385 тестов, 6 пропусков из-за отсутствия
`facts.json`, проверка DOM прошла. В браузере проверены поиск, фильтр источника,
карточки, график и переключение дня/часа, карта и мобильная ширина 320–390 px;
главная страница и поиск через SSH-туннель проверены после перезапуска службы.

## 2ГИС: подключение и проверка данных — 13.11.2026

На сервере `vibe1` настроен облачный MCP 2ГИС для OpenCode в
`.opencode/opencode.json`. Демо-ключ хранится отдельно в закрытом файле
`~/.config/farposton/2gis_api_key` с правами `600`; сам ключ в проекте не хранится.
Ключ и Places API проверены реальными запросами. По данным кабинета демо-ключ
действует до 13.12.2026; лимит Places API — 1000 запросов в месяц, 10 результатов
на страницу. `opencode mcp list` показывает подключение; отдельный запрос через
облачный MCP с нужными `extra_fields` также вернул данные.

Проверка ограниченной выборки: 13.11.2026 четыре запроса по категориям кафе,
ресторан, кофейня и бар в радиусе 3 км от выбранной точки в центре Владивостока
дали 30 уникальных объектов. У 4 из 30 `items.congestion` не пуст; у всех 30
`items.has_dynamic_congestion` равен `false`. Это нерепрезентативная выборка,
а не доля по городу. Непустой `congestion` имеет словарь с наблюдавшимися
числовыми ключами 2–10, кодами дней недели и массивами часов. Семантика уровней,
источник, дата обновления и часовой пояс не подтверждены; динамический флаг не
показывает свободные столы и не отменяет наличие отдельного профиля.
Для одного непустого примера повторный запрос `items/byid` вернул тот же профиль.

Команда `.venv/bin/python tools/check_2gis.py` выполняет один ограниченный запрос
и печатает сводку, не записывая ответ в файл. В проекте настроены агенты
`farposton-data` и `farposton-architecture` (Fable) в дополнение к существующим.
Fable прочитал `AGENTS.md` и `web/paths.py` и предложил отделить адаптер, факт
наблюдения и прогноз. После пробных запросов Fable также ответил на короткий
вопрос по полученной сводке. Попытки обработать большой контекст или результат
MCP завершались ошибкой compaction; не считать их успешной проверкой агента.

По прямому поручению пользователя 13.11.2026 на сервере выполнена разовая
выгрузка Places API в `data/2gis/`: 4312 карточек в SQLite, из них 4305
внутри границы города также сохранены в JSONL. По поисковым категориям питания
найдены 1279 уникальных карточек в городе; профиль `congestion` есть у 37,
`has_dynamic_congestion` у всех 4305 равен false. Это неполная поисковая
выборка, не подтверждение наличия свободных столов. В кабинете Places API
заблокирован после достижения демо-лимита; другие API отдельно не проверялись.
По поручению пользователя отдельно проверен второй демо-ключ и выполнена
дополнительная разовая выгрузка: 699 карточек внутри границы города, из них
262 отсутствовали в первой базе. После объединения двух выборок по ID получено
89 городских карточек с непустым профилем `congestion` (52 новых относительно
первой выгрузки). Полные карточки и исходные снимки находятся в
`data/2gis/congestion-profiles-expanded-2026-11-13/`. Вторая база и журнал
запросов — в `data/2gis/second-key-2026-11-13/`; всего там зарегистрировано
965 попыток. Новый ключ хранится отдельно, основную конфигурацию не заменяли.
Доступный остаток нового лимита в кабинете повторно не подтверждён.
По поручению пользователя подготовлены демонстрационные недельные профили для
остальных точек: `data/2gis/profiles-separated-v1/01_2gis/` содержит 89
исходных профилей, `02_generated/` — 4478 искусственных профилей. Данные
сохранены на Mac и сервере, происхождение указано в каждой записи. Сгенерированный
индекс 0–100 не является измерением или прогнозом и не сопоставлен со шкалой
2ГИС. Формат и отсутствие пересечений проверены; в приложение не подключено.
Fable подготовил черновики интерфейса, логики и архитектуры в
`docs/fable-design-2026-11-13/`; там же отмечены проверенные исправления и
границы достоверности. Документы скопированы на сервер и Mac. Последующая
реализация и её проверка описаны в разделе «Не тесно» выше.
Оставить регулярный сбор и обучение выключенными до подтверждения условий
конкретного ключа и назначения данных. В опубликованной оферте
WebAPI есть ограничения на извлечение и хранение, кэширование не предусмотрено:
https://law.2gis.ru/offer-license-agreement-webapi . Это не установленный вывод
о применимости оферты к данному демо-ключу; нужны условия ключа и разъяснение
2ГИС. Отдельно проверить смысл профиля и период обновления. Старые товарные веса
Forecast не являются прогнозом количества посетителей или свободных столов.

На Mac и сервере прошли 4 теста проверки ответа, ошибок и отсутствия ключа в
выводе. Регулярный сбор 2ГИС не развёрнут.

## Рабочая связка: Mac и vibe1

Рабочая папка на Mac — `вайбатон/farposton`, на сервере —
`/home/recara/farposton`. Вход с настроенного Mac: `ssh vibe1`. На сервере
OpenCode доступен в `/home/linuxbrew/.linuxbrew/bin/opencode`; запускайте его
из папки проекта, чтобы он видел местный `AGENTS.md`.

```sh
ssh vibe1
cd ~/farposton
opencode
make test
```

На сервере запущена служба `farposton.service`: она использует существующие
синтетические данные `.demo/forecast` и слушает только `127.0.0.1:8445`.
Открыть её на Mac можно через SSH-туннель в отдельном терминале:

```sh
ssh -N -L 127.0.0.1:18445:127.0.0.1:8445 vibe1
```

Пока терминал открыт, приложение доступно по `http://127.0.0.1:18445`.
Это демо без входа; не публикуйте его в интернете как есть. Управление на
сервере: `systemctl status farposton`, `sudo systemctl restart farposton`,
журнал — `journalctl -u farposton`. Служба включена при загрузке системы.

Проектные агенты OpenCode лежат в `.opencode/agents/`: `farposton-explore`
исследует код, `farposton-implement` реализует ограниченные задачи,
`farposton-review` проверяет переданный diff. Исследователь и ревьюер не могут
редактировать файлы или запускать команды. Список загруженных агентов можно
проверить из корня проекта командой `opencode debug agents`.

Для неинтерактивных команд с Mac путь Homebrew указывается явно:

```sh
ssh vibe1 'cd ~/farposton && PATH=/home/linuxbrew/.linuxbrew/bin:$PATH make test'
```

Копии на Mac и сервере самостоятельные и автоматически не синхронизируются.
Перед переносом изменений проверяйте `git status` с обеих сторон: не копируйте
поверх чужих правок и не переносите между системами `.venv`. В дальнейшем
используйте Git после настройки доступа к `origin`. На 13.11.2026 GitHub
отклоняет ключи, новый репозиторий пуст; проверка Actions там не запускалась.
Локальный `.github/workflows/ci.yml` предусматривает Python 3.12, Node и
`make test` при push/PR. До публикации проекта он не действует на GitHub.

Проверено 13.11.2026: на сервере Python 3.12.16, установлены зависимости,
`make test` — 370 passed и 6 skipped из-за отсутствующего `facts.json`;
проверка DOM прошла. Синтетические аналитика и weekly-данные проверены.
Служба после перезапуска отвечает на `/health` и `/api/meta`, доступ через
SSH-туннель проверен. На Mac `make test` дал тот же результат. GitHub Actions
не проверялся: ключи отклоняются, новый репозиторий пуст.

## Docker demo on 176.53.253.25 — 13.11.2026

The demo tree is unpacked (`git archive`, no `.git`) into
`/home/tech/farposton` on `tech@176.53.253.25` (Ubuntu 24.04) and runs via
`sudo docker compose up --build -d`; see `DOCKER.md`. Docker and the Compose
plugin were installed from the Ubuntu repositories. The container restart policy
was set to `unless-stopped` with `docker update`; `compose.yaml` does not carry
it, so reapply after the container is recreated.

The container port stays bound to `127.0.0.1:8445`. Public access is
https://farposton.renosaza.com (A record → 176.53.253.25): Caddy 2.6.2 from the
Ubuntu repositories terminates TLS with an automatically renewed Let's Encrypt
certificate, redirects HTTP to HTTPS and proxies to the container. Caddy
rewrites `Host` to the loopback upstream because the demo profile accepts only
loopback hosts. Manage with `sudo systemctl restart caddy`,
`journalctl -u caddy`.

The site is public on 13.11.2026 at the owner's request: no login and no basic
auth, so every visitor gets the demo admin role and can save edits. Those edits
live in the Docker volume and are visible to all visitors; remove or reset the
volume before any real use. The demo is synthetic only.

Checked 13.11.2026: certificate issued for `farposton.renosaza.com`, valid until
11.02.2027; anonymous `/`, `/forecast`, `/health` and `/api/meta` return 200,
HTTP returns 308 to HTTPS, port 8445 is closed from outside. `/health` reports
`ok:false` because the synthetic snapshot is dated, as `DOCKER.md` describes.
`data/2gis/` and the venue model were not copied, so the venue catalogue and
forecast are empty there. Not checked: a browser session, saving edits through
the proxy, certificate renewal, behaviour after a reboot.

## Локальная разработка и показ демо

Работать из корня этого репозитория. Поддерживается Python **3.12**, не системный
Python 3.14. На этом Mac проверен Python 3.12.14; закреплённые зависимости
из `web/requirements.txt` доступны и установлены без изменения версий.

```sh
make setup                 # отдельная .venv, зависимости и проверка версий
make test                  # pytest и существующая проверка DOM
make demo-prepare          # один раз: SHA-256 и безопасная распаковка в .demo/
make demo-check            # версии, заполненность аналитики, сравнение, weekly CSV
make demo                  # http://127.0.0.1:8445, остановка Ctrl+C
```

В другом терминале, пока демо запущено:

```sh
make demo-smoke            # реальные HTTP, правки, replay/conflict, Excel, weekly
make demo-ml               # HGB-инференс и заявка; отдельный учебный CatBoost
```

Если `python3.12` не в PATH: `make setup PYTHON=/absolute/path/to/python3.12`.
Для остальных команд используется уже созданная `.venv`. `make demo-prepare`
**отказывается перезаписывать** существующий `.demo/forecast`; при повторном
показе нужны только `demo-check` и `demo`. Команда `make demo` сама не
устанавливает службу; служба на vibe1 настроена отдельно. Не запускайте
параллельно два экземпляра на одном порту.

Настройки формирует [demo/local.py](demo/local.py) только для дочернего процесса:
loopback, профиль local, демонстрационный admin без входа, пустые ключи внешнего
ИИ, отключённые 1С/Telegram/LLM-отчёты, все БД/правки/модели/логи/Excel в `.demo/`.
Существующий приватный `.env` не переписывается. Рабочие пути из него переопределяются.
Сценарий явно фиксирует **29.09.2026**, поэтому заявки на 30.09 и 01.10 можно
редактировать независимо от сегодняшней даты. `WEB_DEMO_DATE` учитывается
только при local + `WEB_DEMO_NO_AUTH=1`; клиентский и обычный авторизованный
профили её игнорируют. Интерфейс показывает фиксированную дату и синтетическую природу данных.

Для автономности создаётся **учебный календарь дней недели**, без сетевого
обращения к isdayoff.ru; фиксированные коэффициенты особых дат остаются в ядре.
Это не официальный производственный календарь переносов праздников.
`make test` использует отдельный такой календарь `.demo/test-external`, чтобы
существующие календарные тесты не зависали на HTTP при первом запуске. Проверка
с официальным календарём, когда он уже загружен в разрешённый каталог:
`PROGNOZ_EXTERNAL_DIR=/absolute/calendar-dir PYTHONPATH=.:src .venv/bin/python -m pytest -q tests`.

**Просмотр и расчёт — разные сценарии.** Веб показывает готовые parquet-заявки
из архива, не запускает ежедневный ML. `demo-ml` отдельно загружает архивные
HGB-веса, делает инференс и проходит `daily.build` без смены движка или
переобучения HGB. Для отсутствующего standalone CatBoost создаётся отдельная
учебная модель с **100 итерациями**, вместо стандартных 700; повторный запуск
использует её кэш. Это не утверждённые рабочие веса и не полный stacked_guard.

Артефакты проверки: `.demo/forecast/model-check.json`, `hgb-inference.parquet`,
`catboost-inference.parquet`, `hgb-order-demo.xlsx`, `catboost-model-demo.xlsx`.
Последний Excel — вывод модели до дневных правил, а HGB Excel — полный локальный
расчёт проекта заявки. Исторические `output/forecast_log` не подменяются.
`demo-smoke` сохраняет `smoke-check.json` и `smoke-export.xlsx`, после проверки
восстанавливает прежнее ручное значение; события остаются в отдельном журнале демо.

## Обзор проекта по исходникам

Forecast прогнозирует дневную потребность в готовых товарах по парам
**точка × SKU** и готовит проект заявки кухни/склада на точки. Он использует
историю продаж, заявок, чеков, уценок и списаний. Ежедневный выпуск даёт
количество рекомендуемой поставки, но не выполняет полноценную оптимизацию
закупок сырья, сроков годности и доступных остатков. Учёт остатка, упаковки,
минимального заказа и срока жизни показан в отдельном недельном сценарии.

| Папка | Назначение и реальные связи |
|---|---|
| `analysis/etl/` | JSONL 1С → DuckDB `raw.*` → справочники и факты `mart.*`; чеки и расчётные остатки |
| `analysis/config/` | Точки/роли, ассортиментная политика, исключённые даты, двойники SKU, параметры отчётов |
| `src/prognoz/shelf/` | Общая оценка исходов полки и недопродаж для моделей, метрик и отчётов |
| `src/prognoz/engine/` | Исходная и донастроенная формулы по прошлым одноимённым дням недели |
| `src/prognoz/predict/` | Дневной расчёт, HGB/CatBoost/ансамбль, сторожа, архив выпуска, сверки |
| `src/prognoz/backtest/` | Исторические сравнения и метрики; условия отличаются от выпуска |
| `src/prognoz/facts/`, `report/` | Реестр фактов, Excel, утренние и операционные сводки, контролируемая доставка |
| `web/` | FastAPI, статический HTML/JS, read-only снимок и parquet; отдельный SQLite правок |
| `scripts/`, `tools/` | Обучение frozen bundle, проверки данных/моделей/отчётов, сборка полки |
| `notebooks/` | Рецепт обучения на синтетике, включая построение OOF для метамодели |
| `demo/`, `tests/` | Переносимая синтетика, weekly CSV, проверки бизнес-правил и интерфейса |

**Подготовка истории.** [10_ingest.py](analysis/etl/10_ingest.py) читает JSONL
с явными схемами и заменяет таблицы raw. [20_marts.py](analysis/etl/20_marts.py)
строит справочники, продажи за вычетом возвратов, заявки, фактические поставки
и списания. Дата заявки — дата поступления/перемещения, не создания документа.
В расчёте история `ЗаказПоставщику` сменяется `ЗаказНаПеремещение` с первого
transfer каждой точки: два канала не суммируются одновременно.
[30_cheki.py](analysis/etl/30_cheki.py) привязывает ночные чеки к дате начала
смены и даёт продажи-нетто. Отсутствующий дневной документ может дополняться
чеками при пересборке продаж. [40_stock.py](analysis/etl/40_stock.py) считает
остаток от инвентаризации и движений; это не прямой доступ к регистру остатков 1С.

**Ассортимент и спрос.** [scope.py](src/prognoz/scope.py) задаёт группы меню,
роли точек и канонизацию; карточки-двойники склеиваются через `sku_twins.json`.
[assortment.py](src/prognoz/assortment.py) применяет approved-политику отдельно
для `factory_order` и `metric_scope`; в текущем демо она approved с пустыми
исключениями. В дневную матрицу идут пары с продажами или заявками за 28 дней,
а не весь каталог. Совсем новый товар без таких фактов сам не появится.

[shelf/core.py](src/prognoz/shelf/core.py) определяет списание, вечернюю уценку,
отсутствие продаж, раннее прекращение продаж и распродажу под закрытие.
Недопродажа `lost_units` оценивается по часовому профилю после последнего чека,
с минимальной историей 30 дней и порогом значимости 2 штуки. Основная ML-цель —
`sold_units + lost_units`. Это модельная оценка спроса, не наблюдение пустой
полки датчиком. Есть флаги незрелых актов, массовых нулей и ещё не загруженных дней.
Реконструкция −40% из общей скидки также является оценкой; ETL даёт верхнюю и
нижнюю границы при смешении скидочных программ.

**Движки.** [daily.py](src/prognoz/predict/daily.py) всегда считает справочную
формулу и при выбранном ML заменяет её результат модельным. Формула использует
7/14/21/28 дней назад, веса свежести, демпфированный тренд и КВП, усадку к профилю,
блокировку повышения при большой уценке, рамки к заявке человека и дневному итогу.
ML не получает эти узкие рамки; для него действует широкий аварийный сторож.
После модели применяются относительный календарь, сетевой старт новинок,
ограничение медленных позиций и правило нулевого спроса. Погода в дневную заявку не входит.

| Движок | Реальная модель и поведение |
|---|---|
| `baseline` | Донастроенная формула; `BuildCfg()` выбирает её по умолчанию |
| `ml` | HGB Quantile 0.70, стандартно 400 итераций; обучение до `target−lag`, недельный refit; joblib + stamp |
| `catboost` | Самостоятельный CatBoost Quantile 0.70, стандартно 700 итераций; другой набор/подготовка признаков; CBM + stamp |
| `stacked_guard` | Собственный HGB + CatBoost q0.67/q0.70 + residual CatBoost meta q0.67; итог HGB либо HGB+1 при согласии экспертов и meta |

HGB внутри bundle отличается набором признаков от standalone `ml`.
[stacked_guard_core.py](src/prognoz/predict/stacked_guard_core.py) загружает
замороженный комплект `hgb.joblib/q67.cbm/q70.cbm/meta.cbm/manifest.json`, проверяет
схему, размеры, SHA-256, pin и дату обучения.
[train-stacked-guard.py](scripts/train-stacked-guard.py) принимает готовый OOF,
проверяет его временной provenance и обучает meta на остатке `demand−hgb_qty`.
Сам OOF этот скрипт не генерирует. [Ноутбук](notebooks/Пайплайн_обучения_моделей.ipynb)
показывает генерацию синтетики и OOF; все сохранённые execution_count пустые,
поэтому он не доказывает успешный прогон или качество на клиентских данных.

**Горизонты и выпуск.** При issue D цели D+2/D+3/D+4 получают лаги 3/4/5:
доступная история заканчивается D−1. `daily.main` передаёт явный `known_until`.
Ансамбль использует один frozen lag3 bundle с адаптацией признаков для дальних
горизонтов; самостоятельный CatBoost через daily разрешён только для D+2.
Отдельная перспективная точность D+3/D+4 ещё требуется, это записано в provenance.
При ошибке модели daily может сохранить резервную формулу, но возвращает код 2
при несовпадении запрошенного движка. Production-конфиг stacked запрещает скрытый
HGB fallback; самостоятельный фасад ансамбля такой fallback допускает и маркирует.

[release.py](src/prognoz/predict/release.py) архивирует неизменяемый выпуск сразу
на три горизонта, с provenance и контрольными суммами. Строгий режим требует
stacked_guard; `baseline` — явный резерв, `auto` допускает разные движки между
горизонтами, но не внутри одного файла. Веб сначала выбирает полный архивный
выпуск, затем legacy-файлы по дате. Сам запуск трёх расчётов, снимок БД и
расписание сервера здесь не реализованы единым локальным планировщиком.

**Хранение и оператор.** Пути задаёт [etl/_db.py](analysis/etl/_db.py).
Рабочая DuckDB отделена от read-only `prognoz_web.duckdb`; прогнозы и полка —
parquet, модели — joblib/CBM, метаданные — JSON. [web/order.py](web/order.py)
накладывает правки из отдельного SQLite [edits.py](web/edits.py); пакетная запись
атомарна, проверяет ревизии и повтор `batch_id`. Правки связаны с SHA-256 выпуска,
после перевыпуска автоматически не переносятся. [export.py](web/export.py)
берёт сохранённую правку либо прогноз и формирует XLSX. Экспорт администратора
охватывает все точки, даже если таблица на экране отфильтрована одной.

Операционная дата UI — завтра UTC+10; окно правок D+1/D+2, D+3/D+4 предварительные.
Переключатель формулы показывает сохранённую справочную колонку и не меняет
движок заказа. Запись в 1С зависит от внешнего `odata/writer.py` и OData-конфига;
этого писателя в репозитории нет, создание реального документа здесь не проверено.
Роли admin/manager ограничивают точки на сервере; аналитические router требуют admin.

**Точность, аналитика и отчёты.** [accuracy.py](web/accuracy.py) читает сохранённые
сверки shadow, [comparison.py](web/comparison.py) — ретросимуляции duel.
WAPE и bias зависят от уровня агрегации; продажи ограничены привозом, а спрос
полки оценён. WAPE прогноза и заявки сотрудника в accuracy могут использовать
разные подмножества строк. Денежная оценка использует демонстрационные доли
0.33/0.67, а не подтверждённую себестоимость и прибыль клиента.
[analytics.py](web/analytics.py) показывает продажи, YoY, часовые/недельные
профили, уценку, списания, дефицит, скорость распродажи и SKU; раскрывает
пропуски и предварительные данные. Реестр facts и отчёты сохраняют происхождение
цифр; утренний текст и доставка опциональны и отдельно конфигурируются.

**Помощник и weekly.** [web/ai/](web/ai/) содержит восемь разрешённых read-only
инструментов без произвольного SQL и записи заявки. Нужны URL, модель, ключ и
`LLM_ALLOW_CLIENT_DATA=1`; лимит 30 попыток/час на аккаунт, до трёх инструментов
на вопрос. Ограничение tool-loop 90 секунд не равно жёсткому общему дедлайну
всего SSE. Обычное `make demo` не вызывает внешний ИИ; `make demo-llm`
явно включает его на синтетике.
[weekly_demo.py](web/weekly_demo.py) отдельно обучает небольшой HGB на 84 днях
3×6 пар, сравнивает с недельной формулой на четырёх completed holdout-неделях
и выбирает lower WAPE. Рассчитывает только первую доставку с lead/shelf life,
stock/incoming, safety, pack/min-order; последующие доставки и оптимизация
списаний отсутствуют. Утверждение и правки остаются в localStorage браузера.

## Дефолты, ограничения и запись продолжения

| Что | Код / репозиторий | Что определяется сервером |
|---|---|---|
| Движок | `BuildCfg=baseline`; `daily.main=ml`; `duel=catboost`; archive=`stacked_guard` | `PROGNOZ_ENGINE`, CLI; duel принимает только ml/catboost и заменяет stacked_guard на catboost |
| Пути | `analysis/db`, `analysis/output`, repo config | APP_OUTPUT_ROOT, WEB_OUTPUT_ROOT, WEB_DB_SNAPSHOT, PROGNOZ_DB_PATH/CONFIG_DIR/ML_MODEL_DIR и overrides |
| Веб | host 0.0.0.0, port 8445, профиль local | .env, reverse proxy/TLS, accounts; локальный launcher явно bind 127.0.0.1 |
| Ассортимент | Демо-menu scope в Python; пустая approved политика | Точки/роли и политика; `scope.json` не универсальный переключатель всех Python-констант |
| Demand uplift | Выключен; отдельное правило +≤25% с отзывом по уценке/списаниям | PROGNOZ_DEMAND_RECOVERY; для ML не нужен, цель уже sold+lost |
| 1С / ИИ | Требуют явной настройки, не нужны для основного UI | Внешний писатель, OData, ключи/разрешение передачи; в локальном демо выключены |

Защита cutoff не доказывает полную причинность всех исторических сравнений:
raw-таблицы заменяются, нет истории времени доступности каждой поздней проводки.
Полка для ретросравнений может использовать часовой профиль всего окна, включая
будущее; это прямо оговорено в `duel.py` и `backtest/ab_shelf.py`. Duel вызывает
build без issue−1 границы для формулы. Legacy HGB отличается от CatBoost в
масках зрелости/пропусков; без явно переданной causal-полки его фасад строит её
от текущей даты. Поэтому цифры старых replay не заменяют prospective scoring.
Упоминания старых «годовых экзаменов» в комментариях не подтверждены текущим
исследованием; исследовательских файлов и рабочих данных для воспроизведения здесь нет.

Следующий существенный шаг для полного ансамбля: отдельно получить утверждённый
bundle с доверенным SHA-256 либо обучить нового кандидата с настоящим causal OOF
и независимой оценкой. Не подменять его учебными standalone-весами. 1С,
реальный сервер/планировщик и точность на клиентских данных не проверялись.

This directory is an independent private Git repository. `main` contains the integrated neutral demo.

- Application: `web/`; start with `python -m web.app` after installing `web/requirements.txt` and configuring a private `.env`.
- Existing demo runtime: `/opt/product/forecast` on the local demo VM. Data remain separately under `/opt/product/data/forecast`.
- Portable synthetic fixture and generators: `demo/`. Shared product guides are supplied separately in the desktop bundle’s `passport` folder.
- Secrets, virtual environments and runtime data are excluded from Git.

The [training notebook](notebooks/Пайплайн_обучения_моделей.ipynb) walks through the model pipeline on generated data. Run it from the repository root or `notebooks/`; its generated data and weights stay in the ignored `notebooks/_sandbox/` directory. The notebook source is included without saved execution outputs.

## Synthetic demo assistant

The local default is **GLM-5.3**, with **Qwen3.8-Max** available for comparison.
GLM uses Go Chat Completions; Qwen uses Go Messages. Zen returned 402 with this
test account, while the explicit Go endpoint worked; there is no hidden endpoint fallback.

```sh
make demo-llm                       # GLM-5.3, http://127.0.0.1:8445
make demo-smoke-llm                 # another terminal while that server runs
make demo-llm MODEL=qwen3.8-max      # optional comparison after stopping GLM
.venv/bin/python ../verify_llm.py forecast glm-5.3  # four bounded live tasks
```

The local launcher reads the shared private key from `../.secrets/opencode.key`
only when `--llm` is explicit, without changing `.env`. Answers/ground truth,
tool results, request usage and latency are in ignored `.demo/llm-verification/`.
Semantic refusals also need content review; these examples do not guarantee
all future answers or validate accuracy on real business data. On another
authorized synthetic demo server, use its private `.env`:

```dotenv
LLM_BASE_URL=https://opencode.ai/zen/go/v1
LLM_MODEL=glm-5.3
LLM_API_KEY=<OpenCode Go API key>
LLM_ALLOW_CLIENT_DATA=1
```

Keep the key server side. Enable `LLM_ALLOW_CLIENT_DATA` only for a dataset approved for external processing; the demo fixture is synthetic. The assistant remains bounded by its existing read-only tools, per-account rate limit and request timeout. Do not use this configuration for client data without separate authorization.

## Synthetic demo and analytics audit

The isolated admin page `/weekly-demo` rehearses a seven-day auto-order on
fictional data in `demo/weekly/{history,planning}.csv`. The same deterministic
source is maintained in the product bundle's `demo-data/weekly`; the vendored
copy makes the forecast service self-contained. `WEEKLY_DEMO_DATA_DIR` can
point to an equivalent private fixture directory. The page checks a simple
four-week same-weekday average against a small HistGradientBoostingRegressor
(the same model family as the daily ML core, retrained for this fictional
sample) on four completed, causal holdout weeks. It chooses lower WAPE for
the next seven days. The first-delivery draft subtracts projected demand
during lead time from fictional stock and incoming units, then uses demand
through shelf life, safety stock, pack size and minimum order. It does not
cover later deliveries in that week. Edits and approval remain in this
browser's localStorage; they do not reach 1C or the daily order store.

`demo/forecast-demo-data.tar.gz` is a fictional shawarma data snapshot (2026-09-29), with three stores and fourteen products. It includes 485 days of source facts, a newer store with 120 days, two new products with 17 days, stockout estimates, markdowns, writeoffs, and a precomputed shelf. No customer export or original brand is used. The archive contains 30 days of **simulated causal engine comparison** (2026-08-30 to 2026-09-28), replayed with `prognoz.predict.duel`: historical ML models are trained in a sandbox and each cutoff is checked before writing a day. The recorded forecast evaluation still spans only seven days, so its 30-day accuracy view is not a 30-day result. Do not copy a recent model backward or relabel simulated rows as production accuracy.

The existing analytics page covers sales, year-over-year trends, hourly and weekday profiles, writeoffs/markdowns, deficit, sellout speed, and SKU leaders. These sections use the snapshot plus `shelf_rolling.parquet`; they do not need new business formulas. `demo/check_analytics.py` checks that the fixture populates each section and reports source/evaluation coverage.

To inspect the portable fixture locally (Python 3.12, `web/requirements.txt` installed):

```sh
mkdir -p /tmp/forecast-demo-check
shasum -a 256 demo/forecast-demo-data.tar.gz
tar -xzf demo/forecast-demo-data.tar.gz -C /tmp/forecast-demo-check
python demo/check_analytics.py /tmp/forecast-demo-check/forecast
```

Expected archive SHA-256: `f3bc8d83b430c0bc44d7d84f35e1d87511d32da04b4fad32dbd03ded1ab15408`.

To create a new synthetic source database, use `python demo/generate.py /absolute/path/prognoz.duckdb --as-of YYYY-MM-DD`; the target must not exist. Build a matching read-only web snapshot and shelf with the existing project tools. `demo/status.py` publishes synthetic source completeness. Keep the selected `--as-of` date, model cutoff, forecast issue date, and display labels together when rebuilding the served demo.

To extend causal comparison on an extracted copy of the fixture, set `PROGNOZ_DB_PATH`, `APP_OUTPUT_ROOT` and `PROGNOZ_CONFIG_DIR` to that copy; then run `PYTHONPATH=src python -m prognoz.predict.duel --since=YYYY-MM-DD --until=YYYY-MM-DD --db=snapshot`. Its `--db=snapshot` mode reads `prognoz_web.duckdb` beside the configured source DB and writes only to the configured output directory. Verify with `demo/check_analytics.py` before packaging or serving it.

For a server refresh, extract into a **new staging directory**, validate there, stop the web service, then replace only the source DB, web snapshot, generated output and demo config after backing up the existing directory. Preserve the server's `.env`, `web_edits.sqlite`, users, logs and any operator changes. Never extract the archive directly over the live data tree. The archive deliberately excludes `web_edits.sqlite`.

## Continuous integration

GitHub Actions runs Python 3.12 regression checks and the dependency-free DOM check on the organization's `self-hosted`, `Linux`, `X64` runners. Actions are pinned; the workflow has read-only repository permissions and does not run code from fork pull requests. Deployment to the local VM is separate from CI. `actions/checkout` v5 is retained because v6's credential include paths do not match the runners' symlinked work directories.

## Evening (`feature/evening-planner`, состояние 2026-11-13)

Сервис «Готовый вечер»: подбор общепита к окну до события (без LLM/внешних API/2ГИС-запросов, без минут пути и гарантий мест/столов/билетов/сезонности/автообновления афиши). Реализация: [`evening/`](evening/), [`evening_app.py`](evening_app.py), UI [`web/static/evening.html`](web/static/evening.html), [`web/static/evening-plan.html`](web/static/evening-plan.html).

### Данные и режимы
- **`real` (по умолчанию, один compose-сервис)**: каталог `profiles-separated-v1` (4567, общепит 1344) — readonly bind mount, `create_host_path:false` (`EVENING_CATALOG_HOST_DIR`), `synthetic_congestion` игнорируется, `checked_at` каталога `null/unknown` (mtime не дата проверки). События — `evening/data/vladivostok_events.json` (8 реальных, 15–27.11.2026, 3 площадки, проверенные URL/venue ID), включён узким исключением `.gitignore`/`.dockerignore`. Недоступность/ошибка доступа/OSError или невалидность событий (в т.ч. все записи) → **503, без fallback**. Для чтения контейнером `uid 10001` на предоставленной базе выданы минимальные POSIX ACL (`r-x` root/2 папки, `r--` 2 JSONL) через `sudo -n setfacl`.
- **`demo` (явный opt-in)**: отдельный `docker run` (без второго сервиса в compose), вымышленный runtime seed в `.demo/evening/`, `synthetic:true`.

Правила real: distance = Haversine от площадки события; признаки true только по проверяемым отметкам (`Можно с собакой`, `Можно с ноутбуком`, `Детское меню`, `Летняя веранда`/`Столики на улице`, `Заказ столиков`), иначе unknown (required unknown не проходит); расписание weekday/overnight с merge интервалов и `24:00`, полный `[start,end)`, unknown/special не обещают открыто; `checked_at` события >24h → предупреждение; старые fictional планы сохраняют `synthetic:true`.

### Проверки (факт, 2026-11-13)
- Pytest: `tests/test_evening_real.py tests/test_evening_mvp.py tests/test_evening_docker_config_F8.py` → **69 passed, 1 warning**. Full `make test VENV=/home/recara/farposton/.venv/bin/python` → **464 passed, 6 skipped, 1 warning**, DOM helper passed (пропуски pre-existing: `facts.json`).
- Docker real (18447, 8445 не трогался, volume сохранён): пересборка/up с `EVENING_CATALOG_HOST_DIR`, healthy, readonly mount. HTTP: 8/8 событий → **201** (реальные primary+plan B, `synthetic:false`) + GET; 3 события `POST→GET→restart→GET` сохранены. Временный контейнер без mount каталога: `POST /api/evening/plans` → **503 `real_data_unavailable`**.
- Browser smoke (Chromium headless, Playwright в `.demo/evening-browser/`, real): `SMOKE_OK` — создание/ссылка/ошибки/источники/320×1280, окно от начала события.

Документация: [`docs/evening/SPEC.md`](docs/evening/SPEC.md), [`ARCHITECTURE.md`](docs/evening/ARCHITECTURE.md), [`ACCEPTANCE.md`](docs/evening/ACCEPTANCE.md), [`VERIFICATION.md`](docs/evening/VERIFICATION.md), [`REAL-DATA-VERIFICATION.md`](docs/evening/REAL-DATA-VERIFICATION.md), [`BROWSER.md`](docs/evening/BROWSER.md), [`REAL-EVENT-SOURCES.md`](docs/evening/REAL-EVENT-SOURCES.md), [`DOCKER-EVENING.md`](DOCKER-EVENING.md). Тесты: [`tests/test_evening_real.py`](tests/test_evening_real.py), [`tests/test_evening_mvp.py`](tests/test_evening_mvp.py), [`tests/test_evening_docker_config_F8.py`](tests/test_evening_docker_config_F8.py), [`tests/evening_browser_smoke.cjs`](tests/evening_browser_smoke.cjs). Независимая проверка источников и расписаний завершена; исправлены ночные часы, отсутствие расписания дня и обнаружение переноса события. Финальные проверки выполнены через командный API OpenCode после ошибки compaction в модельных запусках. Следующий шаг: чистая сборка из Git и push ветки.
