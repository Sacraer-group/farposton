# House passport research snapshot

The [14 November 2026 archive](2026-11-14/farposton-house-data-2026-11-14.zip) contains all 43 files of the collected Vladivostok snapshot, including the primary `house_passports.json`, source/provenance metadata, validation, parts and the original architecture. Its SHA-256 is `4bdd1d223bfebeeb55607e149c2e31bc3fef95f30ca41f796142fa798b52aa0e`. The [coverage table](2026-11-14/COVERAGE.ru.md) is also readable without extraction. This is a frozen research snapshot, not live data.

Extract locally from the repository root using Python's standard library (the destination is local `data/`):

```sh
python -c "from pathlib import Path; from zipfile import ZipFile; p=Path('data'); p.mkdir(exist_ok=True); ZipFile('research/house-passports/2026-11-14/farposton-house-data-2026-11-14.zip').extractall(p)"
```

The extracted root is `data/farposton_house_data_20261114/`; its `house_passports.json` is the input proposed by the architecture, and `summary.json`, `manifest.json`, `validation.json`, and `README.ru.md` describe the collection. Keep local extracted data out of commits.

The snapshot covers 50 offers and 48 unique houses, with coordinates for 48. Management claims exist for 46 houses, including three source conflicts; two are unknown. Regional capital-repair programme matches cover 44 houses. Management claims are mostly secondary and not confirmed against current management contracts. The 3,005 outage publication-to-house relationships refer to 2,222 unique source publications, including 1,357 older house–publication relationships; these are not annual incident counts. The archive preserves source URLs, timestamps, observations, unknowns and errors. Actual utility bills and independently confirmed resident problems are unavailable; municipal layers are incomplete. A source gap is not evidence of absence. Offer addresses have not been confirmed by visiting the apartments.
