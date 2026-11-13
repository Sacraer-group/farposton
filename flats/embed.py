import hashlib
import io
import json
import sqlite3

import numpy as np
import requests

from flats import config

MAX_BYTES = 10 * 1024 * 1024
BATCH = 16
_model = None


def _download(url: str) -> bytes:
    path = config.photo_dir() / (hashlib.sha256(url.encode()).hexdigest() + ".img")
    if path.exists():
        return path.read_bytes()
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    if not response.headers.get("content-type", "").startswith("image/") or len(response.content) > MAX_BYTES:
        raise ValueError("not an image or too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.content


def _encode(images: list, model_name: str) -> np.ndarray:
    global _model
    import torch
    from transformers import AutoModel, AutoProcessor

    if _model is None or _model[0] != model_name:
        _model = (model_name, AutoProcessor.from_pretrained(model_name), AutoModel.from_pretrained(model_name).eval())
    _, processor, model = _model
    with torch.no_grad():
        out = model.get_image_features(**processor(images=images, return_tensors="pt"))
    vectors = getattr(out, "pooler_output", out).numpy().astype(np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def _missing(conn: sqlite3.Connection, model_name: str) -> list[tuple[str, str, str]]:
    done = {r[0] for r in conn.execute("SELECT photo_url FROM photo_features WHERE model = ?", (model_name,))}
    todo = []
    for source, source_id, photos in conn.execute("SELECT source, source_id, photos_json FROM offers"):
        for url in json.loads(photos):
            if url not in done:
                done.add(url)
                todo.append((url, source, source_id))
    return todo


def embed_missing(conn: sqlite3.Connection, *, model_name: str, limit: int | None = None) -> int:
    from PIL import Image

    todo = _missing(conn, model_name)[:limit]
    stored = 0
    for start in range(0, len(todo), BATCH):
        batch, images = [], []
        for url, source, source_id in todo[start:start + BATCH]:
            try:
                data = _download(url)
                images.append(Image.open(io.BytesIO(data)).convert("RGB"))
                batch.append((url, source, source_id, hashlib.sha256(data).hexdigest()))
            except Exception as exc:  # one broken photo must not stop the run
                print(f"skip {url}: {exc}")
        if not images:
            continue
        vectors = _encode(images, model_name)
        conn.executemany(
            "INSERT OR REPLACE INTO photo_features (photo_url, source, source_id, content_sha256, model, vector)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [(*row, model_name, vec.tobytes()) for row, vec in zip(batch, vectors)],
        )
        conn.commit()
        stored += len(batch)
        print(f"embedded {stored}/{len(todo)}")
    return stored
