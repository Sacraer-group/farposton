# -*- coding: utf-8 -*-
"""Реестр фактов — единственный источник публикуемых чисел проекта.

ЗАЧЕМ. За июль–август одна и та же «лестница точности» разъехалась на три
несовместимые версии по разным файлам, формула издержки Co ушла клиенту с двойным
счётом, а скоуп оказался определён в четырёх местах. Общая причина: число рождалось
в одноразовом прогоне, публиковалось руками и дальше жило своей жизнью.

Реестр это ломает: каждое публикуемое число — именованный факт с id, методом
и периодом. Документы привязываются к фактам якорями <!--fact:id--> и проверяются
tools/verify_docs.py. Панель показателей читает только выход реестра.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py -m prognoz.facts
Выход:  analysis/output/facts.json + facts_history.jsonl + facts/tables/*.parquet
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd


@dataclass(frozen=True)
class Fact:
    id: str                  # "ladder.frozen.net_day.model_wape"
    value: float | int | str
    unit: str = ""           # "%", "шт", "млн ₽/год", "₽", "дн."
    title: str = ""          # человекочитаемое имя
    block: str = ""          # блок-владелец
    method: str = ""         # как посчитано, одним предложением
    period: str = ""         # окно данных, на котором стоит число
    source: str = ""         # модуль:функция


class DuplicateFactError(ValueError):
    pass


class Registry:
    """Копилка фактов и таблиц одного прогона калькулятора."""

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}
        self._tables: dict[str, tuple[pd.DataFrame, str]] = {}

    def add(self, f: Fact) -> None:
        if f.id in self._facts:
            raise DuplicateFactError(f"факт «{f.id}» уже зарегистрирован")
        self._facts[f.id] = f

    def add_many(self, block: str, source: str, items: list[dict]) -> None:
        for it in items:
            self.add(Fact(block=block, source=source, **it))

    def table(self, id: str, df: pd.DataFrame, *, title: str) -> None:
        if id in self._tables:
            raise DuplicateFactError(f"таблица «{id}» уже зарегистрирована")
        self._tables[id] = (df.copy(), title)

    def get(self, id: str) -> Fact:
        return self._facts[id]

    def ids(self) -> list[str]:
        return sorted(self._facts)

    # --- сериализация ---------------------------------------------------------

    def to_json(self, *, version: str, data_stamp: dict) -> dict:
        return {
            "_": "СГЕНЕРИРОВАНО py -m prognoz.facts — руками не править",
            "version": version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data": data_stamp,
            "facts": {fid: {k: v for k, v in asdict(f).items() if k != "id" and v != ""}
                      for fid, f in sorted(self._facts.items())},
            "tables": {tid: {"title": title, "rows": len(df)}
                       for tid, (df, title) in sorted(self._tables.items())},
        }

    def write(self, out_dir: Path, *, version: str, data_stamp: dict,
              merge_from: Path | None = None, append_history: bool = True) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = self.to_json(version=version, data_stamp=data_stamp)

        # Частичный прогон подмешивает свежие факты к прежнему файлу, а не
        # затирает его: пересчитанные id заменяются, остальные остаются как были.
        if merge_from is not None and merge_from.exists():
            prev = json.loads(merge_from.read_text(encoding="utf-8"))
            merged_facts = {**prev.get("facts", {}), **payload["facts"]}
            merged_tables = {**prev.get("tables", {}), **payload["tables"]}
            payload["facts"] = dict(sorted(merged_facts.items()))
            payload["tables"] = dict(sorted(merged_tables.items()))

        main = out_dir / "facts.json"
        main.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")

        # История скаляров — по строке на ПОЛНЫЙ прогон. Отвечает на «почему
        # тогда было 25, а сейчас 19» и станет графиком «метрики во времени».
        if append_history:
            hist = out_dir / "facts_history.jsonl"
            scalars = {fid: f.value for fid, f in self._facts.items()
                       if isinstance(f.value, (int, float))}
            with hist.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"version": version,
                                     "generated_at": payload["generated_at"],
                                     "data": data_stamp, "facts": scalars},
                                    ensure_ascii=False) + "\n")

        tdir = out_dir / "facts" / "tables"
        tdir.mkdir(parents=True, exist_ok=True)
        for tid, (df, _title) in self._tables.items():
            df.to_parquet(tdir / f"{tid}.parquet", index=False)
        return main


# --- регистрация блоков -------------------------------------------------------

BLOCKS: dict[str, Callable] = {}


def block(name: str):
    """Декоратор: функция (Context, Registry) -> None становится блоком калькулятора."""
    def deco(fn):
        BLOCKS[name] = fn
        return fn
    return deco
