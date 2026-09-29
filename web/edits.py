# -*- coding: utf-8 -*-
"""Журнал ручных правок заявки (страница «Заявка»).

Правки живут ОТДЕЛЬНО от выпущенной заявки: parquet в forecast_log не переписывается
никогда. Журнал — sqlite (WAL), история хранится целиком: каждая правка = новая
строка INSERT, действующее значение — последняя запись по ключу
(target_date, point_key, sku_key). qty_user = NULL означает «правка снята»
(строка вернулась к прогнозу), и это тоже событие истории.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import ROOT

_edits_db = os.getenv("WEB_EDITS_DB", "").strip()
DB_PATH = (Path(_edits_db).resolve()
           if _edits_db else ROOT / "analysis" / "db" / "web_edits.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS edits (
    id          INTEGER PRIMARY KEY,
    target_date TEXT NOT NULL,
    release_id  TEXT,
    point_key   TEXT NOT NULL,
    sku_key     TEXT NOT NULL,
    qty_user    REAL,
    edited_at   TEXT NOT NULL,
    ip          TEXT,
    actor       TEXT,
    batch_id    TEXT
);
CREATE INDEX IF NOT EXISTS ix_edits_date ON edits(target_date);
CREATE TABLE IF NOT EXISTS edit_batches (
    batch_id     TEXT PRIMARY KEY,
    target_date  TEXT NOT NULL,
    release_id   TEXT NOT NULL,
    actor        TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    edited_at    TEXT NOT NULL,
    edit_count   INTEGER NOT NULL
);
"""


class EditConflictError(RuntimeError):
    """Сохранённая правка изменилась после загрузки заявки в браузере."""


class BatchReplayMismatch(RuntimeError):
    """Один batch_id повторно использован для другого набора правок."""


def _connect() -> sqlite3.Connection:
    """Короткоживущее подключение; схема создаётся лениво при первом обращении."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        # Самое первое включение WAL требует эксклюзивной блокировки файла.
        # Короткий timeout с ограниченным retry не подвешивает первый запрос на
        # минуты, а рабочим транзакциям после инициализации возвращаются 10 с.
        con.execute("PRAGMA busy_timeout=50")
        for attempt in range(20):
            try:
                mode = con.execute("PRAGMA journal_mode=WAL").fetchone()
                if not mode or str(mode[0]).lower() != "wal":
                    raise RuntimeError("SQLite WAL mode could not be enabled")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 19:
                    raise
                time.sleep(0.05)
        con.execute("PRAGMA busy_timeout=10000")
        con.executescript(_SCHEMA)
        # Совместимая миграция уже существующего append-only журнала. Старые
        # строки не меняются; BEGIN IMMEDIATE сериализует первые подключения.
        edit_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(edits)")}
        batch_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(edit_batches)")}
        if (not {"actor", "batch_id", "release_id"}.issubset(edit_columns)
                or "release_id" not in batch_columns):
            con.execute("BEGIN IMMEDIATE")
            edit_columns = {
                str(row[1]) for row in con.execute("PRAGMA table_info(edits)")}
            batch_columns = {
                str(row[1]) for row in con.execute("PRAGMA table_info(edit_batches)")}
            if "actor" not in edit_columns:
                con.execute("ALTER TABLE edits ADD COLUMN actor TEXT")
            if "batch_id" not in edit_columns:
                con.execute("ALTER TABLE edits ADD COLUMN batch_id TEXT")
            if "release_id" not in edit_columns:
                con.execute("ALTER TABLE edits ADD COLUMN release_id TEXT")
            if "release_id" not in batch_columns:
                con.execute("ALTER TABLE edit_batches ADD COLUMN release_id TEXT")
            con.commit()
        return con
    except Exception:
        if con.in_transaction:
            con.rollback()
        con.close()
        raise


def add_edit(target_date: str, point_key: str, sku_key: str,
             qty_user: float | None, ip: str | None) -> str:
    """Записывает правку (или её снятие, qty_user=None). Возвращает edited_at."""
    edited_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = _connect()
    try:
        with con:  # транзакция
            con.execute(
                "INSERT INTO edits (target_date, point_key, sku_key, qty_user, edited_at, ip) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (target_date, point_key, sku_key,
                 float(qty_user) if qty_user is not None else None, edited_at, ip),
            )
    finally:
        con.close()
    return edited_at


def _payload_hash(
        entries: Iterable[tuple[str, str, float | None, int]]) -> str:
    """Стабильный отпечаток набора для безопасного повтора после обрыва сети."""
    canonical = sorted(
        (str(point_key), str(sku_key),
         float(qty_user) if qty_user is not None else None, int(revision))
        for point_key, sku_key, qty_user, revision in entries
    )
    raw = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bind_legacy_release(
        con: sqlite3.Connection, target_date: str, release_id: str) -> None:
    """Один раз привязывает старые безымянные правки к текущему выпуску.

    После следующего перевыпуска эти строки больше не наложатся автоматически.
    Меняются только миграционные метаданные, сами события и их id неизменны.
    """
    if con.execute(
            "SELECT 1 FROM edits WHERE target_date = ? AND release_id IS NULL LIMIT 1",
            (target_date,),
    ).fetchone() is None:
        return
    owns_transaction = not con.in_transaction
    if owns_transaction:
        con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            "UPDATE edits SET release_id = ? "
            "WHERE target_date = ? AND release_id IS NULL",
            (release_id, target_date),
        )
        if owns_transaction:
            con.commit()
    except Exception:
        if owns_transaction and con.in_transaction:
            con.rollback()
        raise


def add_edits_bulk(
        target_date: str,
        release_id: str,
        entries: list[tuple[str, str, float | None, int]],
        ip: str | None,
        actor: str,
        batch_id: str,
) -> dict:
    """Атомарно записывает набор правок и защищает от устаревшего браузера.

    ``expected_revision`` в каждой записи — id последнего события, которое видел
    браузер (0, если событий ещё не было). Если хотя бы одна строка успела
    измениться в другой сессии, не записывается ничего. Повтор того же batch_id с
    тем же содержимым возвращает прежний успех и не дублирует историю.
    """
    payload_hash = _payload_hash(entries)
    edited_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        replay = con.execute(
            "SELECT target_date, release_id, actor, payload_hash, edited_at, edit_count "
            "FROM edit_batches WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        if replay is not None:
            old_date, old_release, old_actor, old_hash, old_at, old_count = replay
            if (old_date, old_release, old_actor, old_hash) != (
                    target_date, release_id, actor, payload_hash):
                raise BatchReplayMismatch(
                    "Идентификатор сохранения уже использован для другого набора")
            con.commit()
            return {
                "edited_at": str(old_at),
                "count": int(old_count),
                "replayed": True,
            }

        _bind_legacy_release(con, target_date, release_id)
        latest = {
            (str(point_key), str(sku_key)): int(revision)
            for point_key, sku_key, revision in con.execute(
                """
                SELECT e.point_key, e.sku_key, e.id
                FROM edits e
                JOIN (
                    SELECT max(id) AS id FROM edits
                    WHERE target_date = ? AND release_id = ?
                    GROUP BY point_key, sku_key
                ) last ON last.id = e.id
                """,
                (target_date, release_id),
            ).fetchall()
        }
        conflicts = [
            (point_key, sku_key)
            for point_key, sku_key, _qty_user, expected_revision in entries
            if latest.get((point_key, sku_key), 0) != int(expected_revision)
        ]
        if conflicts:
            raise EditConflictError(
                "Заявка была изменена в другой сессии; обновите страницу")

        con.executemany(
            "INSERT INTO edits "
            "(target_date, release_id, point_key, sku_key, qty_user, edited_at, ip, actor, batch_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (target_date, release_id, point_key, sku_key,
                 float(qty_user) if qty_user is not None else None,
                 edited_at, ip, actor, batch_id)
                for point_key, sku_key, qty_user, _revision in entries
            ],
        )
        con.execute(
            "INSERT INTO edit_batches "
            "(batch_id, target_date, release_id, actor, payload_hash, edited_at, edit_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (batch_id, target_date, release_id, actor, payload_hash,
             edited_at, len(entries)),
        )
        con.commit()
        return {"edited_at": edited_at, "count": len(entries), "replayed": False}
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()


def get_effective_state(
        target_date: str,
        release_id: str,
) -> dict[tuple[str, str], tuple[float | None, int]]:
    """Последнее значение и id-ревизия каждого ключа, включая снятые правки."""
    con = _connect()
    try:
        _bind_legacy_release(con, target_date, release_id)
        rows = con.execute(
            """
            SELECT e.point_key, e.sku_key, e.qty_user, e.id
            FROM edits e
            JOIN (SELECT max(id) AS id FROM edits
                  WHERE target_date = ? AND release_id = ?
                  GROUP BY point_key, sku_key) last ON last.id = e.id
            """,
            (target_date, release_id),
        ).fetchall()
    finally:
        con.close()
    return {
        (str(point_key), str(sku_key)):
            (float(qty_user) if qty_user is not None else None, int(revision))
        for point_key, sku_key, qty_user, revision in rows
    }


def get_effective(target_date: str, release_id: str) -> dict[tuple[str, str], float]:
    """Действующие правки; снятые (последнее значение NULL) не возвращаются."""
    return {
        key: float(qty_user)
        for key, (qty_user, _revision) in get_effective_state(
            target_date, release_id).items()
        if qty_user is not None
    }


def get_history(target_date: str, point_key: str | None = None) -> list[dict]:
    """Вся история правок даты (свежие сверху) — для GET /api/edits."""
    con = _connect()
    try:
        query = ("SELECT id, release_id, point_key, sku_key, qty_user, edited_at, "
                 "ip, actor, batch_id "
                 "FROM edits "
                 "WHERE target_date = ?")
        args = (target_date,)
        if point_key is not None:
            query += " AND point_key = ?"
            args = (target_date, point_key)
        rows = con.execute(query + " ORDER BY id DESC", args).fetchall()
    finally:
        con.close()
    return [
        {"id": rid, "release_id": release_id, "point_key": p, "sku_key": s,
         "qty_user": (float(q) if q is not None else None),
         "edited_at": ts, "ip": ip, "actor": actor, "batch_id": batch_id}
        for rid, release_id, p, s, q, ts, ip, actor, batch_id in rows
    ]
