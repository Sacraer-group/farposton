# Constraints

| Restriction | Source | Consequence |
| --- | --- | --- |
| Metrapi `/v1/items` and `/v1/item` allow at most 1 request per 2 s per key; 429 body has `detail.retry_after` (no header) | https://metrapi.ru/api-docs.html | `flats/source.py` sleeps ≥2 s between calls and honors `retry_after` from the body |
| Max 1000 items per response, 50 on trial | Metrapi docs | Page with `cursor`/`next_cursor` until `null` |
| API key is a secret | Metrapi docs | Key only in env var `METRAPI_KEY`; never in templates, JS, commits, logs |
| `deposit`, `commission`, `prepayment`, `rent_period` are free strings ("63 000 ₽", "Без залога", "50%", "за 1 месяц") | Metrapi docs | Parse in `flats/money.py`; keep raw string; unparseable -> unknown |
| `price` for rent is per month for the whole flat | Metrapi docs | `price` = monthly rent |
| `photos` are links on the origin site and die with the listing | Metrapi docs | Embedding step downloads and caches photos locally |
| `group_id` may be `null` | Metrapi docs | Offer with `null` group is its own flat card |
| Daily rent is excluded by the source | Metrapi docs | Request `deal_type=rent&rent_period=long&realty_type=flat&city=vladivostok` |
| Vladivostok code is `vladivostok` | `GET /v1/cities`, observed 2026-11-13 | Use this code in import |
| Local single-user study project | User decision | No auth, no accounts, bind Flask to `127.0.0.1` |
| One branch per task, merged into `main` | User decision | Parallel executors never share a branch; the plan docs themselves go straight to `main` |
