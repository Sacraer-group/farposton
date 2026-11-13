# -*- coding: utf-8 -*-
"""Снапшот обязательств: скоуп, константы движка, окно протокола.

Не считает ничего — снимает то, что уже определено кодом, и генерирует
analysis/config/scope.json. Раньше scope.json правился руками и устарел целиком
(лестница «24 даты» в нём была посчитана на двух); теперь он — производная кода,
и тест сверяет файл с живой генерацией.

Обязательства отличаются от измерений: они меняются решением, а не добором
данных. Измерения (WAPE, потери) в scope.json больше не живут — им место
в facts.json с датой и хешем данных.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from prognoz import scope
from prognoz.facts.registry import block
from prognoz.predict import daily

SRC = "facts.blocks.scope_block:compute"
CONFIG = Path(__file__).resolve().parents[4] / "analysis" / "config"

# Каноническое окно протокола: на нём заморожена лестница, к которой привязаны
# документы. Меняется только решением, с пересчётом dryrun_frozen.parquet.
PROTOCOL_WINDOW = (date(2026, 6, 25), date(2026, 7, 22))


def snapshot() -> dict:
    return {
        "_": "СГЕНЕРИРОВАНО py -m prognoz.facts (блок scope) — руками не править",
        "scope": {
            "group_like": scope.GROUP_LIKE,
            "group_not_like": scope.GROUP_NOT_LIKE,
            "group_not_like_bar": scope.GROUP_NOT_LIKE_BAR,
            "market_roles": list(scope.MARKET_ROLES),
            "headline_points": list(scope.HEADLINE_POINTS),
            "out_of_headline": list(scope.OUT_OF_HEADLINE),
            "comparable_full_period": list(scope.COMPARABLE_FULL_PERIOD),
        },
        "engine": {
            "version": daily.VERSION,
            "default_horizon_days": daily.HORIZON_DAYS,
            "horizon_days": list(daily.HORIZON_LAGS),
            "week_lags": list(daily.WEEK_LAGS),
            "active_days": daily.ACTIVE_DAYS,
            "shrink_k": daily.SHRINK_K,
            "u40_lock_share": daily.U40_LOCK_SHARE,
            "clip_to_human": list(daily.CLIP_TO_HUMAN),
            "step_rel": daily.STEP_REL,
            "step_abs": daily.STEP_ABS,
            "day_clip": list(daily.DAY_CLIP),
            "wd_clip": list(daily.WD_CLIP),
            "stale_days": daily.STALE_DAYS,
        },
        "protocol": {
            "ladder_window": [str(d) for d in PROTOCOL_WINDOW],
            "writeoff_share_formula": "списано / продано (не /(продано+списано))",
            "cost_ratio": 0.33,
            "cost_ratio_source": "демонстрационное допущение",
            "pilot": {"shadow": "2026-08-03 .. 2026-08-16",
                      "live": "2026-08-17 .. 2026-09-13"},
        },
    }


def write_scope_json() -> Path:
    CONFIG.mkdir(parents=True, exist_ok=True)
    out = CONFIG / "scope.json"
    out.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return out


@block("scope")
def compute(ctx, reg) -> None:
    snap = snapshot()
    write_scope_json()
    reg.add_many("scope", SRC, [
        {"id": "scope.group_like", "value": snap["scope"]["group_like"],
         "title": "скоуп: маска группы"},
        {"id": "scope.excludes_bar", "value": snap["scope"]["group_not_like_bar"],
         "title": "скоуп: дополнительный фильтр группы"},
        {"id": "engine.version", "value": snap["engine"]["version"],
         "title": "версия прод-движка"},
        {"id": "engine.horizon_days",
         "value": ",".join(map(str, snap["engine"]["horizon_days"])),
         "unit": "дн.", "title": "горизонты ночного выпуска"},
        {"id": "engine.u40_lock_share", "value": snap["engine"]["u40_lock_share"],
         "title": "порог доли уценки, выше которого КВП вверх блокируется"},
        {"id": "protocol.ladder_window",
         "value": " .. ".join(snap["protocol"]["ladder_window"]),
         "title": "каноническое окно лестницы точности"},
    ])
