import os
from pathlib import Path


def metrapi_key() -> str:
    key = os.environ.get("METRAPI_KEY")
    if not key:
        raise RuntimeError("METRAPI_KEY is not set")
    return key


def db_path() -> Path:
    return Path(os.environ.get("FLATS_DB", "data/flats.db"))


def photo_dir() -> Path:
    return Path(os.environ.get("FLATS_PHOTO_DIR", "data/photos"))
