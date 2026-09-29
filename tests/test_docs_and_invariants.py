# -*- coding: utf-8 -*-
"""Обёртки: документы сходятся с реестром, инвариант списаний в допуске.

Эти тесты смотрят на АРТЕФАКТЫ (facts.json, документы), а не пересчитывают
данные — поэтому они мгновенные и гоняются в ночном прогоне. Если facts.json
не собран, тесты пропускаются, а не падают: на чужой машине без витрины
это норма.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROGNOZ = Path(__file__).resolve().parents[1]
FACTS = PROGNOZ / "analysis" / "output" / "facts.json"


@pytest.fixture(scope="module")
def facts() -> dict:
    if not FACTS.exists():
        pytest.skip("facts.json не собран (py -m prognoz.facts)")
    return json.loads(FACTS.read_text(encoding="utf-8"))


def test_docs_match_facts(facts):
    r = subprocess.run(
        [sys.executable, str(PROGNOZ / "tools" / "verify_docs.py")],
        capture_output=True, text=True, encoding="utf-8",
        env={"PYTHONUTF8": "1", "PYTHONPATH": str(PROGNOZ / "src"),
             "SYSTEMROOT": "C:\\Windows", "PATH": "C:\\Windows\\System32"},
    )
    assert r.returncode == 0, f"verify_docs:\n{r.stdout}\n{r.stderr}"


def test_writeoff_invariant_order_of_magnitude(facts):
    """Остаток (заказано − продано) и списания должны совпадать ПО ПОРЯДКУ ВЕЛИЧИН.

    Тождества нет и не бывает: списание в 1С не ссылается на заявку, часть потока
    ходит вне заявок (замечание панели проверки 04.08). Ловим то, что инвариант
    реально умеет ловить: старый двойной счёт Co предсказывал бы списаний ВТРОЕ
    меньше остатка — отношение вне [0,5 … 2] значит, что экономика снова поехала
    или в данных списаний дыра."""
    f = facts["facts"]
    leftover = f["eco.gap.leftover"]["value"]
    wo_scope = f["eco.gap.written_off_scope"]["value"]
    assert wo_scope > 0, "списаний за окно нет вовсе"
    ratio = leftover / wo_scope
    assert 0.5 < ratio < 2.0, f"остаток/списания = {ratio:.2f} — вне порядка величин"


def test_frozen_ladder_is_pinned(facts):
    """Канонические числа лестницы. Меняются ТОЛЬКО решением (новое окно или
    новый скоуп) — вместе с пересчётом dryrun_frozen.parquet и этим тестом."""
    f = facts["facts"]
    assert f["ladder.net_day.model_wape"]["value"] == pytest.approx(4.7, abs=0.05)
    assert f["ladder.net_day.human_wape"]["value"] == pytest.approx(9.9, abs=0.05)
    assert f["ladder.sku_day.model_wape"]["value"] == pytest.approx(30.7, abs=0.05)
    assert f["ladder.sku_day.human_wape"]["value"] == pytest.approx(21.0, abs=0.05)
    assert f["eco.ratio"]["value"] == pytest.approx(1.77, abs=0.02)
