# -*- coding: utf-8 -*-
"""Проверка чисел в документах против реестра фактов.

Механика: в живых .md перед проверяемым числом стоит невидимый комментарий
    | **сеть × день** | ... | **<!--fact:ladder.net_day.model_wape-->4,7%** |
Скрипт находит якоря, берёт первое число после якоря и сверяет со значением
факта из analysis/output/facts.json. Расхождение → exit 1 и список.

Допуск по умолчанию — половина последнего показанного разряда («4,7» при факте
4,68 проходит), переопределяется в якоре: <!--fact:eco.ratio tol=0.05-->.

Замороженные снимки (docs/замеры/*) и исходящие клиенту НЕ проверяются:
они историчны по природе и обязаны расходиться с живыми данными.

Запуск: PYTHONUTF8=1 PYTHONPATH=prognoz/src py prognoz/tools/verify_docs.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))
from prognoz.facts.fmt import parse_ru      # noqa: E402

FACTS = PROGNOZ / "analysis" / "output" / "facts.json"
DOCS = [
    PROGNOZ / "README.md",
    PROGNOZ / "docs" / "ПРОТОКОЛ_измерения.md",
    PROGNOZ / "docs" / "Экономика_ошибки_заказа.md",
    PROGNOZ / "docs" / "Понятным_языком_что_мы_делаем.md",
    PROGNOZ / "docs" / "Методичка_формул.md",
]

ANCHOR = re.compile(r"<!--\s*fact:([\w.\-]+)(?:\s+tol=([\d.]+))?\s*-->")


def default_tol(shown: float) -> float:
    """Половина последнего показанного разряда: 4,7 → 0,05; 36 → 0,5."""
    s = f"{shown}"
    if "." in s:
        digits = len(s.split(".")[1])
        return 0.5 * 10 ** -digits
    return 0.5


def check_doc(path: Path, facts: dict) -> list[str]:
    text = path.read_text(encoding="utf-8")
    errors = []
    for m in ANCHOR.finditer(text):
        fid, tol_s = m.group(1), m.group(2)
        after = text[m.end():m.end() + 80]
        before = text[max(0, m.start() - 8):m.start()]
        try:
            shown = parse_ru(after)
        except ValueError:
            errors.append(f"{path.name}: якорь {fid} — числа после него не найдено")
            continue
        # знак может стоять и ПЕРЕД якорем: «−<!--fact:…-->1,2%»
        if shown > 0 and ("−" in before[-3:] or before.rstrip().endswith("-")):
            shown = -shown
        if fid not in facts:
            errors.append(f"{path.name}: якорь {fid} — факта нет в реестре")
            continue
        val = facts[fid]["value"]
        if not isinstance(val, (int, float)):
            errors.append(f"{path.name}: якорь {fid} — факт не числовой ({val!r})")
            continue
        tol = float(tol_s) if tol_s else default_tol(shown)
        if abs(shown - val) > tol:
            errors.append(f"{path.name}: {fid} — в тексте {shown}, в реестре {val}")
    return errors


def main() -> int:
    if not FACTS.exists():
        print("facts.json не найден — сначала py -m prognoz.facts")
        return 1
    facts = json.loads(FACTS.read_text(encoding="utf-8"))["facts"]
    all_errors, n_anchors = [], 0
    for doc in DOCS:
        if not doc.exists():
            all_errors.append(f"{doc.name}: файл не найден")
            continue
        n_anchors += len(ANCHOR.findall(doc.read_text(encoding="utf-8")))
        all_errors += check_doc(doc, facts)
    if all_errors:
        print(f"РАСХОЖДЕНИЯ ({len(all_errors)}):")
        for e in all_errors:
            print(f"  ✗ {e}")
        return 1
    print(f"Все документы сходятся с реестром: якорей {n_anchors}, расхождений нет.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
