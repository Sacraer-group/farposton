import sqlite3

import numpy as np

MODEL = "google/siglip2-base-patch16-224"


def cosine_scores(sample: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return matrix @ sample  # vectors are stored L2-normalized


def rank_by_photo(conn: sqlite3.Connection, sample_url: str, keys: list[str], model: str) -> dict[str, tuple[float, str]]:
    row = conn.execute(
        "SELECT vector FROM photo_features WHERE photo_url = ? AND model = ?", (sample_url, model)
    ).fetchone()
    if row is None or not keys:
        return {}
    sample = np.frombuffer(row[0], dtype=np.float32)
    wanted = set(keys)
    rows = conn.execute(
        "SELECT p.photo_url, p.vector, o.group_id, o.source, o.source_id FROM photo_features p"
        " JOIN offers o ON o.source = p.source AND o.source_id = p.source_id WHERE p.model = ?",
        (model,),
    ).fetchall()
    rows = [(url, vec, gid if gid is not None else f"{src}:{sid}") for url, vec, gid, src, sid in rows]
    rows = [r for r in rows if r[2] in wanted]
    if not rows:
        return {}
    scores = cosine_scores(sample, np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows]))
    best: dict[str, tuple[float, str]] = {}
    for (url, _, key), score in zip(rows, scores):
        if key not in best or score > best[key][0]:
            best[key] = (float(score), url)
    return best
