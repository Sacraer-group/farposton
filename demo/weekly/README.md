# Weekly ordering demo fixture

Fictional data for a small shawarma business. `history.csv` contains daily finished-product demand and sales for three points and six menu SKUs over 12 complete weeks, 2026-07-07 through 2026-09-28. Demand includes simulated lost sales on occasional stockout days; it is never lower than sales. The point and SKU keys match the forecast demo dimensions (`DEMO-POINT-01`…`03`, `DEMO-SKU-01`…`06`). Quantities are individual ready-to-sell units.

`planning.csv` is an **independent fictional stock snapshot as of 2026-09-29**. `stock_on_hand` is available now; `incoming_qty` is confirmed to arrive before the next planned replenishment. `safety_stock`, `pack_size`, `min_order`, `shelf_life_days`, and `lead_time_days` are explicit scenario assumptions. The products are chilled and delivered frequently from a central kitchen; a weekly plan may require more than one delivery within the week. A positive order must meet `min_order` and be rounded to `pack_size`.

Inventory demo revisions concern raw ingredients and packaging at periodic count dates. They are related operational context, **not a live finished-product stock feed** for these order calculations. No customer data, 1C access, credentials, or network service is used.

`generate.py` creates the exact CSVs with Python standard library and a fixed random seed; it reads no external data and refuses an existing output directory. Reproduce with `python generate.py /tmp/weekly-order-fixture` and compare the two CSVs to this directory. Run `python validate.py` here to check dates, keys, quantities, and planning constraints.
