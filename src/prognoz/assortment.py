"""Единая версия фильтра ассортимента для заявки и всех метрик.

Политика хранится в ``analysis/config/assortment_policy.json``.  Она задаётся
стабильными парами «каноническая точка × склеенный SKU»; ``point_key=null``
означает всю сеть.  Один и тот же файл используется выпуском заявки, обучением
моделей, ретропрогонами и веб-аналитикой, чтобы строка не исчезала только из
одного отчёта, продолжая искажать остальные.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import pandas as pd


DEFAULT_POLICY = (
    Path(__file__).resolve().parents[2] / "analysis" / "config"
    / "assortment_policy.json"
)
PURPOSES = frozenset({"factory_order", "metric_scope"})
STATUSES = frozenset({"draft", "approved", "retired"})


@dataclass(frozen=True)
class Entry:
    sku_key: str
    point_key: str | None
    factory_order: bool
    metric_scope: bool
    target_mode: str
    reason: str
    valid_from: date | None
    valid_to: date | None


@dataclass(frozen=True)
class Policy:
    version: str
    status: str
    entries: tuple[Entry, ...]
    sha256: str
    path: Path

    @property
    def active(self) -> bool:
        return self.status == "approved"


def policy_path() -> Path:
    """Путь можно подменить только явной переменной локального/серверного контура."""
    explicit = os.environ.get("PROGNOZ_ASSORTMENT_POLICY", "").strip()
    if explicit:
        return Path(explicit)
    config_root = os.environ.get("PROGNOZ_CONFIG_DIR", "").strip()
    if config_root:
        return Path(config_root) / "assortment_policy.json"
    return DEFAULT_POLICY


def _parse_policy(source: Path) -> Policy:
    if not source.exists():
        return Policy("missing", "draft", (), "missing", source)
    raw_bytes = source.read_bytes()
    raw = json.loads(raw_bytes.decode("utf-8"))
    canonical_bytes = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    status = str(raw.get("status", "draft"))
    if status not in STATUSES:
        raise ValueError(f"Неизвестный статус ассортиментной политики: {status}")
    entries: list[Entry] = []
    seen: set[tuple] = set()
    for item in raw.get("entries", []):
        sku_key = str(item.get("sku_key", "")).strip()
        raw_point = item.get("point_key")
        point_key = str(raw_point).strip() if raw_point not in (None, "") else None
        if not sku_key:
            raise ValueError("В ассортиментной политике отсутствует sku_key")
        try:
            valid_from = (date.fromisoformat(str(item["valid_from"]))
                          if item.get("valid_from") else None)
            valid_to = (date.fromisoformat(str(item["valid_to"]))
                        if item.get("valid_to") else None)
        except ValueError as exc:
            raise ValueError(
                f"Некорректный период ассортиментной политики: {(point_key, sku_key)}"
            ) from exc
        if valid_from and valid_to and valid_from > valid_to:
            raise ValueError(
                f"valid_from позже valid_to: {(point_key, sku_key)}"
            )
        identity = (point_key, sku_key, valid_from, valid_to)
        if identity in seen:
            raise ValueError(f"Дублирующая запись ассортиментной политики: {identity}")
        seen.add(identity)
        target_mode = str(item.get("target_mode", "none"))
        if target_mode not in {"none", "observed_sales", "recovered_demand"}:
            raise ValueError(f"Неизвестный target_mode: {target_mode}")
        entries.append(Entry(
            sku_key=sku_key,
            point_key=point_key,
            factory_order=bool(item.get("factory_order", True)),
            metric_scope=bool(item.get("metric_scope", True)),
            target_mode=target_mode,
            reason=str(item.get("reason", "")).strip(),
            valid_from=valid_from,
            valid_to=valid_to,
        ))
    grouped: dict[tuple[str | None, str], list[Entry]] = {}
    for entry in entries:
        grouped.setdefault((entry.point_key, entry.sku_key), []).append(entry)
    for key, versions in grouped.items():
        ordered = sorted(versions, key=lambda value: value.valid_from or date.min)
        previous_end: date | None = None
        for index, entry in enumerate(ordered):
            if index and (
                previous_end is None or entry.valid_from is None
                or entry.valid_from <= previous_end
            ):
                raise ValueError(f"Пересекающиеся периоды политики: {key}")
            previous_end = entry.valid_to
    return Policy(
        version=str(raw.get("version", "unversioned")),
        status=status,
        entries=tuple(entries),
        # Хэш описывает смысл политики, а не CRLF/LF или отступы JSON. Иначе
        # один и тот же реестр заставлял бы лишний раз переобучать модель после
        # переноса между Windows и Linux.
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        path=source,
    )


@lru_cache(maxsize=8)
def _load_cached(path_text: str, mtime_ns: int, size: int) -> Policy:
    del mtime_ns, size
    return _parse_policy(Path(path_text))


def load_policy(path: Path | None = None) -> Policy:
    source = Path(path or policy_path())
    if not source.exists():
        return _parse_policy(source)
    stat = source.stat()
    return _load_cached(str(source.resolve()), stat.st_mtime_ns, stat.st_size)


def provenance(path: Path | None = None) -> dict:
    policy = load_policy(path)
    return {
        "assortment_policy_version": policy.version,
        "assortment_policy_status": policy.status,
        "assortment_policy_sha256": policy.sha256,
        "assortment_policy_active": policy.active,
        "assortment_policy_entries": len(policy.entries),
    }


def _expanded_skus(sku_key: str) -> tuple[str, ...]:
    """Политика хранит живой SKU, но старые факты могут ещё содержать призрака."""
    from prognoz.scope import sku_twins  # локальный импорт избегает цикла модулей

    ghosts = [ghost for ghost, live in sku_twins().items() if live == sku_key]
    return tuple(dict.fromkeys([sku_key, *ghosts]))


def _entry_match(
    frame: pd.DataFrame,
    entry: Entry,
    *,
    point_col: str,
    sku_col: str,
    dates: pd.Series | None,
) -> pd.Series:
    match = frame[sku_col].astype(str).isin(_expanded_skus(entry.sku_key))
    if entry.point_key is not None:
        match &= frame[point_col].astype(str).eq(entry.point_key)
    if dates is not None and entry.valid_from is not None:
        match &= dates >= entry.valid_from
    if dates is not None and entry.valid_to is not None:
        match &= dates <= entry.valid_to
    return match


def filter_frame(
    frame: pd.DataFrame,
    purpose: str,
    *,
    point_col: str = "point_key",
    sku_col: str = "sku_key",
    date_col: str | None = None,
    policy: Policy | None = None,
) -> pd.DataFrame:
    """Вернуть строки, разрешённые для фабричной заявки или метрик."""
    if purpose not in PURPOSES:
        raise ValueError(f"Неизвестное назначение ассортиментного фильтра: {purpose}")
    active = policy or load_policy()
    if not active.active or not active.entries or frame.empty:
        return frame.copy()
    if point_col not in frame or sku_col not in frame:
        raise ValueError(f"Нет обязательных колонок {point_col!r} и {sku_col!r}")
    needs_dates = any(entry.valid_from or entry.valid_to for entry in active.entries)
    if needs_dates and date_col is None:
        date_col = next((name for name in (
            "target_date", "trade_day", "sale_date", "wo_date", "date", "d"
        ) if name in frame.columns), None)
    if needs_dates and (date_col is None or date_col not in frame):
        raise ValueError("Для версионной ассортиментной политики нужна колонка даты")
    dates = pd.to_datetime(frame[date_col]).dt.date if needs_dates else None
    keep = pd.Series(True, index=frame.index)
    for entry in active.entries:
        if getattr(entry, purpose):
            continue
        keep &= ~_entry_match(
            frame, entry, point_col=point_col, sku_col=sku_col, dates=dates)
    return frame.loc[keep].copy()


def filter_pairs(
    pairs: set[tuple[str, str]] | None,
    purpose: str,
    *,
    policy: Policy | None = None,
) -> set[tuple[str, str]] | None:
    if pairs is None:
        return None
    if not pairs:
        return set()
    frame = pd.DataFrame(sorted(pairs), columns=["point_key", "sku_key"])
    allowed = filter_frame(frame, purpose, policy=policy)
    return set(zip(allowed["point_key"], allowed["sku_key"]))


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_exclusion_predicate(
    purpose: str,
    *,
    point_expr: str,
    sku_expr: str,
    date_expr: str | None = None,
    policy: Policy | None = None,
) -> str:
    """SQL-предикат ``TRUE``/``NOT (...)`` для агрегатов до pandas.

    Выражения задаются только кодом проекта; значения из JSON экранируются.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"Неизвестное назначение ассортиментного фильтра: {purpose}")
    active = policy or load_policy()
    if not active.active:
        return "TRUE"
    clauses: list[str] = []
    for entry in active.entries:
        if getattr(entry, purpose):
            continue
        sku_values = ", ".join(_sql_literal(value)
                               for value in _expanded_skus(entry.sku_key))
        bits = [f"{sku_expr} IN ({sku_values})"]
        if entry.point_key is not None:
            bits.append(f"{point_expr} = {_sql_literal(entry.point_key)}")
        if entry.valid_from is not None:
            if date_expr is None:
                raise ValueError("SQL-фильтру версионной политики нужна дата")
            bits.append(f"{date_expr} >= DATE '{entry.valid_from.isoformat()}'")
        if entry.valid_to is not None:
            if date_expr is None:
                raise ValueError("SQL-фильтру версионной политики нужна дата")
            bits.append(f"{date_expr} <= DATE '{entry.valid_to.isoformat()}'")
        clauses.append("(" + " AND ".join(bits) + ")")
    return "TRUE" if not clauses else "NOT (" + " OR ".join(clauses) + ")"
