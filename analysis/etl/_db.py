# -*- coding: utf-8 -*-
"""Общее для ETL-слоя прогноза: пути, подключение к DuckDB.

У DuckDB один процесс-писатель; читатели подключаются read_only.

Лимиты памяти рассчитаны на сервер 8 ГБ RAM / 3 потока CPU, где рядом живут
другие службы — писателю до 1 ГБ (переопределяется PROGNOZ_MEM_MB),
читателям 1200 МБ / 1 поток.
"""
import os
from pathlib import Path

import duckdb

# Лимит памяти писателя. Занижен намеренно и переопределяется переменной
# PROGNOZ_MEM_MB: рядом могут жить другие службы. При нехватке DuckDB уходит
# в spill на диск — это медленнее, но ничего не роняет.
WRITER_MEM_MB = int(os.environ.get("PROGNOZ_MEM_MB", "1000"))

_DEFAULT_PROGNOZ = Path(__file__).resolve().parents[2]
PROGNOZ = Path(os.environ.get("APP_REFERENCE_ROOT", _DEFAULT_PROGNOZ))
ANALYSIS = PROGNOZ / "analysis"
# JSONL-выгрузка 1С (вне git). Путь задаётся PROGNOZ_DATA_DIR: у прод-сервера
# выгрузка лежит в соседней папке, в разработке — куда положили из share/raw.
DATA = Path(os.environ.get("PROGNOZ_DATA_DIR", PROGNOZ / "data" / "raw"))
_db_path = os.environ.get("PROGNOZ_DB_PATH", "").strip()
DB_PATH = Path(_db_path) if _db_path else ANALYSIS / "db" / "prognoz.duckdb"
DB_DIR = DB_PATH.parent
OUTPUT = Path(os.environ.get("APP_OUTPUT_ROOT", ANALYSIS / "output"))
CONFIG = Path(os.environ.get("PROGNOZ_CONFIG_DIR", ANALYSIS / "config"))
EXTERNAL = Path(os.environ.get("PROGNOZ_EXTERNAL_DIR", PROGNOZ / "data" / "external"))


def connect_writer():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    (DB_DIR / "spill").mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(f"SET memory_limit='{WRITER_MEM_MB}MB'")
    con.execute("SET threads=2")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET temp_directory='{(DB_DIR / 'spill').as_posix()}'")
    return con


def connect_reader(mem_mb: int = 1200):
    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute(f"SET memory_limit='{mem_mb}MB'")
    con.execute("SET threads=1")
    return con


def jsonl(name: str) -> str:
    """POSIX-путь к файлу выгрузки для read_json (DuckDB понимает прямые слэши)."""
    return (DATA / f"{name}.jsonl").as_posix()
