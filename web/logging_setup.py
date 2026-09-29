"""Настройка loguru: вывод в stdout и ротация файлового лога.

ВАЖНО (ПДн): в лог не пишем содержимое поездок/ФИО/телефоны — только имена файлов,
IP, счётчики и трейсбэки.
"""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_file: Path, level: str = "INFO") -> None:
    # На русской Windows stdout по умолчанию cp1251 — loguru будет падать на юникоде.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    logger.remove()

    # enqueue=False сознательно: enqueue=True спавнит subprocess-листенер,
    # который при kill становится сиротой. Для нашего объёма логов синхронная запись ок.
    logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}:{line}</cyan> — <level>{message}</level>"
        ),
        enqueue=False,
    )

    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_file,
        level=level,
        rotation="10 MB",
        retention=10,
        compression="zip",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} — {message}",
        enqueue=False,
    )
