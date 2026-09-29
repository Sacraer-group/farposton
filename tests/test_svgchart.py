# -*- coding: utf-8 -*-
"""SVG-генератор: валидность XML, CSS-переменные, русские числа."""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROGNOZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROGNOZ / "src"))
sys.path.insert(0, str(PROGNOZ / "tools"))
from svgchart import bars, hbars, lines      # noqa: E402


def _valid(svg: str) -> ET.Element:
    return ET.fromstring(svg)      # ParseError = тест упал


def test_hbars_valid_and_themed():
    svg = hbars([("сеть × день", {"human": 9.9, "model": 4.7, "floor": 2.1}),
                 ("точка × день", {"human": 11.2, "model": 9.1, "floor": 4.7})],
                title="Лестница")
    _valid(svg)
    assert "var(--accent)" in svg          # тема через CSS-переменные
    assert "9,9" in svg and "4,7" in svg   # русская запятая
    assert "предел" in svg                 # пунктир пола подписан


def test_bars_valid():
    svg = bars(["янв", "фев", "мар"], [10.0, 20.5, 15.0], title="Бары", unit="шт")
    _valid(svg)
    assert "20,5" in svg


def test_lines_valid_with_gaps():
    svg = lines({"A": [("2026-01", 10.0), ("2026-03", 12.0)],   # дыра в феврале
                 "B": [("2026-01", 5.0), ("2026-02", 6.0), ("2026-03", 7.0)]},
                title="Линии")
    _valid(svg)
    assert svg.count("<path") == 2


def test_escaping():
    svg = bars(["<точка> & «кавычки»"], [1.0], title='Заголовок с "кавычками" & <тегом>')
    _valid(svg)
