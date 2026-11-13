"""LLM assembles approved factual phrases; identity and numbers stay local."""
from __future__ import annotations

import json
import os

from prognoz.report import llm_client


def add_narrative(report: dict) -> None:
    if os.getenv("REPORT_LLM_AGGREGATES_ALLOWED") != "1":
        return
    env = dict(os.environ)
    # Separate consent; do not enable arbitrary client-data use in other features.
    env["LLM_ALLOW_CLIENT_DATA"] = "1"
    try:
        settings = llm_client.llm_settings(env)
    except (ValueError, TypeError):
        return
    if not settings:
        return
    for p in report["points"]:
        phrases = {
            "data": "Данные обновлены полностью." if p["complete"] else "Часть данных ещё не подтверждена; выводы предварительные.",
            "forecast": "Прогноз доступен для сравнения с продажами." if p["metrics"]["forecast"] is not None else "Прогноз на отчётную дату не найден.",
            "documents": "Есть документы, требующие проверки." if any(d["state"] in {"missing", "unposted"} for d in p["documents"])
                         else "Состояние документов приведено ниже.",
        }
        try:
            reply = llm_client.chat([
                {"role": "system", "content": "Составь краткое вступление к оперативному отчёту. Верни JSON-массив из двух или трёх ключей подходящих фраз в порядке важности. Только ключи из phrases; без новых утверждений."},
                {"role": "user", "content": json.dumps({"point": "POINT", "metrics": p["metrics"], "phrases": phrases}, ensure_ascii=False)}
            ], settings=dict(settings, model=settings.get("model_cheap") or settings["model"]), max_tokens=500, retries=0)
            keys = json.loads(reply) if reply else None
            if isinstance(keys, list) and 2 <= len(keys) <= 3 and all(isinstance(k, str) and k in phrases for k in keys) and len(set(keys)) == len(keys):
                p["narrative"] = " ".join(phrases[k] for k in keys)
        except (ValueError, TypeError, OSError, KeyError):
            continue
