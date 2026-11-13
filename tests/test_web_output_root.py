# -*- coding: utf-8 -*-
"""Выходы прогона читаются по WEB_OUTPUT_ROOT / APP_OUTPUT_ROOT (C3, 08.09).

До 08.09 вкладка «Сводки» и помощник искали ai_reports/ в analysis/output проекта,
а ночной цикл клиента пишет их по APP_OUTPUT_ROOT — у клиента список был пуст.
"""
from __future__ import annotations

import json
from pathlib import Path

from web import data, routes_reports
from web.ai import chat
from web.config import ROOT


def _write_report(root: Path, day: str, llm: bool = True) -> None:
    reports = root / "ai_reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{day}.json").write_text(
        json.dumps({"llm": llm, "generated_at": day}), encoding="utf-8")
    (reports / f"{day}.html").write_text(f"<p>{day}</p>", encoding="utf-8")


def test_output_root_prefers_web_then_app_then_project(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WEB_OUTPUT_ROOT", raising=False)
    monkeypatch.delenv("APP_OUTPUT_ROOT", raising=False)
    assert data.output_root() == ROOT / "analysis" / "output"
    assert data.ai_reports_dir() == ROOT / "analysis" / "output" / "ai_reports"
    assert data.facts_path() == ROOT / "analysis" / "output" / "facts.json"

    monkeypatch.setenv("APP_OUTPUT_ROOT", str(tmp_path / "forecast"))
    assert data.ai_reports_dir() == (tmp_path / "forecast").resolve() / "ai_reports"

    monkeypatch.setenv("WEB_OUTPUT_ROOT", str(tmp_path / "web"))
    assert data.ai_reports_dir() == (tmp_path / "web").resolve() / "ai_reports"


def test_reports_are_listed_from_output_root_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WEB_OUTPUT_ROOT", str(tmp_path))
    _write_report(tmp_path, "2026-09-06", llm=False)
    _write_report(tmp_path, "2026-09-07", llm=True)
    (tmp_path / "ai_reports" / "не-дата.json").write_text("{}", encoding="utf-8")
    routes_reports.list_reports.cache_clear()

    assert routes_reports.list_reports() == [
        {"date": "2026-09-07", "has_llm_text": True},
        {"date": "2026-09-06", "has_llm_text": False},
    ]
    assert routes_reports.latest_report() == {"date": "2026-09-07", "html": "<p>2026-09-07</p>"}
    payload = routes_reports.report_payload("2026-09-06")
    assert payload["json"] == {"llm": False, "generated_at": "2026-09-06"}
    assert chat.latest_report_json()[0] == "2026-09-07"
    routes_reports.list_reports.cache_clear()


def test_reports_are_empty_when_output_root_has_no_reports(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("WEB_OUTPUT_ROOT", str(tmp_path))
    routes_reports.list_reports.cache_clear()

    assert routes_reports.list_reports() == []
    assert chat.latest_report_json() == (None, "{}")
    assert chat.compact_facts() == "{}"
    routes_reports.list_reports.cache_clear()
