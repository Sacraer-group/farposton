# Sprint 4 — visual interior search (ML)

Goal: user picks a photo on a flat page; eligible flats are re-ordered by visual similarity and show the matched photo.

Model: `google/siglip2-base-patch16-224` (Hugging Face). Install with `pip install -e '.[ml]'`. Quality on real Vladivostok photos is not verified yet; S4-T4 checks it.

## Wave 4A

### S4-T1 Photo embedding

- **Owns:** `flats/embed.py`, `tests/test_embed.py`
- **Steps:**
  1. `embed_missing(conn, model_name, limit=None)`: for every photo URL in `offers.photos_json` without a `photo_features` row for this model: download (`requests`, timeout 20, max 10 MB, only `image/*` content type) into `config.photo_dir()/<sha256 of URL>.jpg` unless cached; compute `content_sha256` of bytes; open with Pillow, convert RGB.
  2. Model loaded lazily once: `AutoModel.from_pretrained`, `AutoProcessor.from_pretrained`; `model.get_image_features(**processor(images=batch, return_tensors="pt"))` under `torch.no_grad()`, batch 16; L2-normalize; store `vector` as `np.float32` bytes.
  3. Download/decode error on one photo: log one line, skip it, continue. Commit every batch.
  4. Inject seams for tests: module-level functions `_download(url) -> bytes` and `_encode(images) -> np.ndarray` that tests monkeypatch.
  5. Return number of new vectors.
- **Tests (no network, no model):** monkeypatch `_download`/`_encode`; 3 photos -> 3 rows; second run -> 0; failing download -> skipped, others stored; vector length preserved.
- **Accept:** `pytest -q tests/test_embed.py`.

### S4-T2 Similarity

- **Owns:** `flats/similar.py`, `tests/test_similar.py`
- **Steps:**
  1. `cosine_scores(sample, matrix)`: both already L2-normalized -> `matrix @ sample`.
  2. `rank_by_photo(conn, sample_url, keys, model)`: load sample vector (missing -> return `{}`); load vectors of all photos of offers in `keys` (key = `group_id` or `source:source_id`); for each key keep max score and its photo URL. The sample's own flat simply ranks first.
  3. Module constant `MODEL = "google/siglip2-base-patch16-224"`.
- **Tests:** hand-made 3-dim vectors: closest key first; key without vectors absent from result; unknown sample -> `{}`.
- **Accept:** `pytest -q tests/test_similar.py`.

### S4-T3 CLI embed command

- **Owns:** `flats/cli.py`, `tests/test_cli.py`
- **Steps:** replace the `embed` placeholder: `--limit N`, `--model` default `google/siglip2-base-patch16-224`; calls `embed.embed_missing`; prints `embedded N photos`. Import `flats.embed` inside the handler so `init-db`/`import` work without ML deps. Keep existing tests green; add test with monkeypatched `flats.embed.embed_missing`.
- **Accept:** `pytest -q tests/test_cli.py`.

## Wave 4B (after 4A merged)

### S4-T4 Similarity in search and UI

- **Owns:** `flats/search.py`, `tests/test_search.py`, `flats/templates/_card.html`, `flats/templates/search.html`
- **Steps:**
  1. In `search`: call `rank_by_photo(conn, url, keys_of_non_reject_cards, MODEL)` (use `similar.MODEL`), set `SearchRow.similar`, order by contracts §5 rule 4. Rejected cards are filtered **before** ranking.
  2. `_card.html`: when `row.similar`, show the matched photo instead of the first photo and label "Похожий интерьер". No percentages.
  3. `search.html`: when a sample is set, show the sample thumbnail and a "Сбросить образец" button (`POST /api/sample/clear`).
- **Tests:** add: sample set + vectors -> similar card first; rejected card with the most similar photo stays absent; card without vectors stays in list, last.
- **Accept:** `pytest -q`.

### S4-T5 Quality spot check (manual, needs real data and ML deps)

- **Owns:** `docs/ml-check.md`
- **Steps:** `flats embed --limit 300`; pick 5 sample photos of different styles (light modern, old renovation, dark furniture, kitchen, empty room); record for each the top 5 matched photo URLs and a yes/no "looks similar" judgement; record embed time per photo and CPU/GPU.
- **Accept:** `docs/ml-check.md` with observed numbers and judgements; a clear verdict: keep model / try another.

## Sprint check

`pytest -q`; manual: open a flat, click "Найти похожие интерьеры", list re-orders, budget filter still applies.
