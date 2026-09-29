"""Daily operational evidence. No network and no implicit missing-value zeroes."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=10), "Etc/GMT-10")
SOURCES = ("receipts", "sales", "orders", "writeoffs")
STATUS = {
    "complete": "загрузка подтверждена", "pending": "загрузка неполная",
    "unknown": "полнота не подтверждена", "error": "ошибка чтения источника",
    "present": "найден", "unposted": "не проведён по данным выгрузки",
    "missing": "не найден в полной выгрузке", "not_observed": "не найден; полнота не подтверждена",
    "not_required": "не требуется по регламенту", "not_assessable": "проверить невозможно",
}


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(LOCAL_TZ)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def number(value):
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite operational quantity")
    return round(result, 3)


def total(rows, field):
    values = [r.get(field) for r in rows]
    return number(sum(values)) if values and all(v is not None for v in values) else None


def validate_input(bundle: dict) -> None:
    day = date.fromisoformat(bundle["trade_date"])
    datetime.fromisoformat(bundle["generated_at"]).astimezone(LOCAL_TZ)
    if datetime.fromisoformat(bundle["generated_at"]).tzinfo is None:
        raise ValueError("generated_at requires timezone")
    if not bundle.get("points"):
        raise ValueError("Point registry is empty")
    known = set()
    account_keys = set()
    for point in bundle["points"]:
        if not point.get("point_key") or point["point_key"] in known:
            raise ValueError("Missing or duplicate point")
        known.add(point["point_key"])
        for account_key in [point["point_key"], *point.get("account_point_keys", [])]:
            if account_key in account_keys:
                raise ValueError("Ambiguous account point mapping")
            account_keys.add(account_key)
        for key in ("active_from", "active_to"):
            if point.get(key):
                date.fromisoformat(point[key])
        if point.get("active_from") and point.get("active_to") and point["active_from"] > point["active_to"]:
            raise ValueError("Invalid point interval")
        if point.get("activation_pending") and point.get("active_from"):
            raise ValueError("Pending point cannot have active_from")
        for source in SOURCES:
            s = point.get("sources", {}).get(source, {})
            if s.get("status", "unknown") not in {"complete", "pending", "unknown", "error"}:
                raise ValueError("Invalid source status")
            if s.get("status") == "complete":
                if s.get("trade_date") != day.isoformat() or not s.get("snapshot_id"):
                    raise ValueError("Complete source requires matching day and snapshot identity")
                if datetime.fromisoformat(s["loaded_at"]).tzinfo is None:
                    raise ValueError("Source timestamp requires timezone")
                if datetime.fromisoformat(s["loaded_at"]) > datetime.fromisoformat(bundle["generated_at"]):
                    raise ValueError("Source timestamp is later than report generation")
        for doc in point.get("documents", {}).values():
            for field in ("required", "present", "posted"):
                if doc.get(field) is not None and type(doc[field]) is not bool:
                    raise ValueError("Document flags must be boolean or null")
        seen = set()
        for row in point.get("rows", []):
            if not row.get("sku_key") or row["sku_key"] in seen:
                raise ValueError("Missing or duplicate SKU")
            seen.add(row["sku_key"])
            for key in ("forecast", "edited", "order", "sales", "returns", "writeoff", "writeoff_rub"):
                number(row.get(key))


def build_report(bundle: dict) -> dict:
    validate_input(bundle)
    day = bundle["trade_date"]
    points = []
    for source_point in sorted(bundle["points"], key=lambda p: p["point_key"]):
        if source_point.get("active_from", day) > day or source_point.get("active_to", day) < day:
            continue
        p = copy.deepcopy(source_point)
        rows = p.get("rows", [])
        sources = {k: p.get("sources", {}).get(k, {"status": "unknown"}) for k in SOURCES}
        changed = [r for r in rows if r.get("edited") is not None and r.get("forecast") is not None
                   and r["edited"] != r["forecast"]]
        comparable = [r for r in rows if r.get("forecast") is not None and r.get("sales") is not None]
        forecast_rows = [r for r in rows if r.get("forecast") is not None]
        sold = total(rows, "sales")
        matched_sales = sum(max(0, r["sales"]) for r in comparable)
        missing_regulation = []
        docs = []
        for k in ("sales", "orders", "writeoffs"):
            ev = p.get("documents", {}).get(k, {})
            required = ev.get("required")
            present = ev.get("present")
            if required is None:
                missing_regulation.append(k)
            if present is True:
                state = "unposted" if ev.get("posted") is False else "present"
            elif required is False:
                state = "not_required"
            elif required is True and present is False:
                state = "missing" if sources[k]["status"] == "complete" else "not_observed"
            else:
                state = "not_assessable"
            docs.append({"kind": k, "state": state, "required": required,
                         "source_at": sources[k].get("loaded_at"),
                         "snapshot_id": sources[k].get("snapshot_id"),
                         "remindable": required is True and state in {"missing", "unposted"}
                         and sources[k]["status"] == "complete"})
        early = [{"sku": r.get("sku", r["sku_key"]), "last_sale_at": r.get("last_sale_at"),
                  "basis": "оценка по последней положительной продаже; не факт нулевого остатка"}
                 for r in rows if r.get("early_stop_estimate") and r.get("last_sale_at")]
        effective = [dict(r, effective=r.get("edited") if r.get("edited") is not None
                          else r.get("forecast")) for r in forecast_rows]
        p.update(sources=sources, documents=docs, early_stop=early,
                 regulation_missing=missing_regulation,
                 metrics={"forecast": total(forecast_rows, "forecast"),
                          "web_order": total(effective, "effective"),
                          "confirmed_unchanged": bool(p.get("confirmation", {}).get("unchanged")) and not changed,
                          "order": total(rows, "order"), "sales": sold,
                          "returns": total(rows, "returns"),
                          "writeoff": total(rows, "writeoff"), "writeoff_rub": total(rows, "writeoff_rub"),
                          "edit_count": len(changed),
                          "edit_up": number(sum(max(0, r["edited"] - r["forecast"]) for r in changed)),
                          "edit_down": number(sum(max(0, r["forecast"] - r["edited"]) for r in changed)),
                          "comparable_rows": len(comparable), "rows": len(rows),
                          "coverage_pct": round(100 * len(comparable) / len(rows), 1) if rows else None,
                          "sales_wape_pct": round(100 * sum(abs(r["forecast"]-r["sales"]) for r in comparable)
                                                  / matched_sales, 1) if matched_sales else None})
        p["sources_complete"] = all(s["status"] == "complete" for s in sources.values())
        p["observed_totals"] = {}
        for key in ("order", "sales", "writeoff", "writeoff_rub"):
            values = [r[key] for r in rows if r.get(key) is not None]
            p["observed_totals"][key] = {"value": number(sum(values)) if values else None, "rows": len(values)}
        p["complete"] = p["sources_complete"] and not missing_regulation and all(
            d["state"] in {"present", "not_required"} for d in docs)
        if not rows:
            for source, fields in (("receipts", ("sales", "returns")), ("writeoffs", ("writeoff", "writeoff_rub"))):
                if sources[source]["status"] == "complete":
                    for field in fields:
                        p["metrics"][field] = 0.0
        points.append(p)
    if not points:
        raise ValueError("No points for requested day")
    report = {"schema_version": 2, "trade_date": day, "generated_at": bundle["generated_at"],
              "timezone": "Etc/GMT-10", "points": points,
              "complete": all(p["complete"] for p in points)}
    # Generation time is metadata, not a reason to redeliver identical evidence.
    report["version"] = digest({k: v for k, v in report.items() if k != "generated_at"})[:20]
    return report


def scope_report(report: dict, point_key: str | None) -> dict:
    if point_key is None:
        return copy.deepcopy(report)
    points = [copy.deepcopy(p) for p in report["points"] if p["point_key"] == point_key or point_key in p.get("account_point_keys", [])]
    if len(points) > 1:
        raise ValueError("Ambiguous account scope")
    if not points:
        raise KeyError("No report for this point")
    scoped = {k: report[k] for k in ("schema_version", "trade_date", "generated_at", "timezone")}
    scoped.update(points=points, complete=points[0]["complete"])
    scoped["version"] = digest({k: v for k, v in scoped.items() if k != "generated_at"})[:20]
    return scoped


def fmt(value):
    return "нет данных" if value is None else f"{value:g}"


def freshness_notice(p: dict) -> str:
    stale = []
    for key, label in (("receipts", "чеки"), ("sales", "документы продаж")):
        item = p.get("source_freshness", {}).get(key, {})
        if item.get("rows_on_report_date") == 0:
            stale.append(f"{label}: последняя дата {item.get('latest_date') or 'неизвестна'}")
    if not stale:
        return ""
    return "За отчётный день записи не найдены: " + "; ".join(stale) + ". Отсутствие записей само по себе не подтверждает нулевые продажи. Проверить загрузку."


def partial_totals_notice(p: dict) -> str:
    parts = []
    for key, label, unit in (("order", "заказ", "шт."), ("sales", "продажи нетто", "шт."),
                             ("writeoff", "списания", "шт."), ("writeoff_rub", "сумма списаний", "₽")):
        found = p.get("observed_totals", {}).get(key, {})
        if p["metrics"].get(key) is None and found.get("rows", 0):
            parts.append(f"{label} {fmt(found['value'])} {unit} ({found['rows']} поз.)")
    return ("По полученным строкам: " + "; ".join(parts) + ". Полный итог не подтверждён.") if parts else ""


def render_point(p: dict) -> str:
    m = p["metrics"]
    lines = [p["name"], "Данные: " + ("загрузка и документы проверены" if p["complete"] else "предварительные / неполные")]
    if freshness_notice(p):
        lines.append(freshness_notice(p))
    if partial_totals_notice(p):
        lines.append(partial_totals_notice(p))
    if p.get("activation_pending"):
        lines.append("Подключение точки ожидает подтверждённой даты запуска и проверки выгрузки.")
    lines += [f"Прогноз: {fmt(m['forecast'])} шт.",
              f"Вариант в сервисе: {fmt(m['web_order'])} шт.; заказ из документов: {fmt(m['order'])} шт.",
              f"Правки: {m['edit_count']} поз.; добавлено {fmt(m['edit_up'])}, убрано {fmt(m['edit_down'])} шт.",
              "Заказ без изменений: " + ("подтверждён" if m["confirmed_unchanged"] else "подтверждение отсутствует"),
              f"Продажи нетто: {fmt(m['sales'])} шт.; возвраты: {fmt(m['returns'])} шт.",
              f"Списания: {fmt(m['writeoff'])} шт.; сумма документов: {fmt(m['writeoff_rub'])} ₽.",
              f"Сопоставимо {m['comparable_rows']} из {m['rows']} позиций ({fmt(m['coverage_pct'])}%).",
              f"Построчное отклонение от продаж: {fmt(m['sales_wape_pct'])}%; не оценка полного спроса."]
    if p.get("forecast_error"):
        lines.append(p["forecast_error"])
    if p.get("forecast_provenance") == "legacy_unverified":
        lines.append("Источник прогноза: старый журнал без подтверждённого неизменяемого архива; сравнение предварительное.")
    if p.get("early_stop_status"):
        lines.append("Данные об окончании продаж: " + p["early_stop_status"] + ".")
    for label, key in (("Чеки", "receipts"), ("Продажи", "sales"), ("Заказы", "orders"), ("Списания", "writeoffs")):
        s = p["sources"][key]
        lines.append(f"{label}: {STATUS[s['status']]}; обновлено {s.get('loaded_at') or 'неизвестно'}.")
    labels = {"sales": "продаж", "orders": "заказа", "writeoffs": "списания"}
    for d in p["documents"]:
        lines.append(f"Документ {labels[d['kind']]}: {STATUS[d['state']]}.")
    if p["early_stop"]:
        lines.append("Раннее окончание — предположение, требует проверки остатков:")
        lines.extend(f"• {r['sku']}: последняя продажа {r['last_sale_at']}" for r in p["early_stop"])
    else:
        lines.append("Раннее окончание: подтверждённых сведений нет.")
    if p.get("reconciliation"):
        lines.append(p["reconciliation"])
    lines.append("Ответственный: " + p.get("owner", "не настроен"))
    issues = [d for d in p["documents"] if d["state"] in {"missing", "unposted"}]
    if issues:
        lines.append("Действие: проверить документы «" + ", ".join(labels[d["kind"]] for d in issues) + "».")
    elif not p["complete"]:
        lines.append("Действие команде: проверить полноту и обновление выгрузки.")
    else:
        lines.append("Действие: проверить отмеченные расхождения и раннее окончание.")
    return "\n".join(lines)


def render_text(report: dict) -> str:
    title = f"Отчёт за {report['trade_date']} · UTC+10"
    return title + "\n" + f"Обновлено {report['generated_at']}. Версия {report['version']}." + "\n\n" + "\n\n".join(
        ((p.get("narrative") + "\n") if p.get("narrative") else "") + render_point(p) for p in report["points"])


def render_html(report: dict) -> str:
    # Scope the report BEFORE rendering; all source strings remain escaped.
    esc = lambda value: html.escape(str(value))
    cards = []
    for p in report['points']:
        m = p['metrics']
        def value(key):
            if m.get(key) is not None:
                return esc(fmt(m[key]))
            observed = p.get('observed_totals', {}).get(key, {})
            return esc(fmt(observed['value'])) + ' †' if observed.get('rows') else 'нет данных'
        cells = ''.join('<div><dt>' + label + '</dt><dd>' + value(key) + '</dd></div>'
                        for key, label in (('forecast', 'Прогноз'), ('web_order', 'Вариант заказа в сервисе'),
                                           ('order', 'Заказ из документов'), ('sales', 'Продажи нетто'),
                                           ('writeoff', 'Списания')))
        if m.get('edit_count'):
            edits = f"Правки: {m['edit_count']} поз., +{fmt(m['edit_up'])} / −{fmt(m['edit_down'])}."
        else:
            edits = 'Без правок — подтверждено.' if m.get('confirmed_unchanged') else 'Подтверждения заказа без правок нет.'
        labels = {'sales': 'продажи', 'orders': 'заказ', 'writeoffs': 'списания'}
        docs = '; '.join(labels[d['kind']] + ': ' + STATUS[d['state']] for d in p['documents'])
        early = ('Возможное раннее окончание: ' + '; '.join(
            str(r['sku']) + ' — последняя продажа ' + str(r['last_sale_at']) for r in p['early_stop'][:3])
                 + '. Это оценка, а не подтверждённое время исчерпания остатка.') if p['early_stop'] else 'Время окончания товаров не подтверждено.'
        state = 'Данные подтверждены' if p['complete'] else 'Предварительные данные'
        cards.append('<section><h2>' + esc(p['name']) + '</h2><p class="status">' + state + '</p>'
                     + '<dl>' + cells + '</dl><p>' + esc(edits) + '</p><p><strong>Документы:</strong> '
                     + esc(docs) + '</p><p>' + esc(early) + '</p><p><strong>Ответственный:</strong> '
                     + esc(p.get('owner') or 'не настроен') + '</p>'
                     + ('<p class="notice">' + esc(freshness_notice(p)) + '</p>' if freshness_notice(p) else '')
                     + '<details><summary>Подробности и проверка данных</summary><pre>'
                     + esc(render_point(p)) + '</pre></details></section>')
    day = date.fromisoformat(report['trade_date']).strftime('%d.%m.%Y')
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Утренняя сводка</title><style>'
            'body{font:16px/1.5 system-ui,sans-serif;color:#172b3a;background:#f4f6f8;margin:0;padding:20px}'
            'main{max-width:1000px;margin:auto}h1{font-size:26px}h2{font-size:20px;margin:0}'
            'section{background:white;border:1px solid #d8e0e7;border-radius:12px;padding:20px;margin:16px 0}'
            'dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px}'
            'dt{font-size:14px;color:#43566a}dd{font-size:22px;font-weight:650;margin:4px 0}'
            'p{margin:10px 0}.status,.notice{color:#695000}summary{cursor:pointer;font-weight:600}'
            'pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}small{color:#43566a}'
            '@media(max-width:480px){body{padding:10px}section{padding:14px}dl{grid-template-columns:1fr 1fr}}'
            '</style></head><body><main><h1>Сводка за ' + esc(day) + '</h1>'
            '<p>Часовой пояс — UTC+10. † Сумма по загруженным строкам; полнота не подтверждена.</p>'
            '<p>Вариант заказа в сервисе не равен подтверждённому заказу в 1С. '
            'Списания приведены в единицах исходной выгрузки.</p>'
            + ''.join(cards) + '<small>Сформировано: ' + esc(report['generated_at'])
            + '</small></main></body></html>')


def persist(report: dict, root: Path) -> Path:
    """Publish complete revision atomically. Old reports are never overwritten."""
    day = date.fromisoformat(report["trade_date"]).isoformat()
    parent = root / "v2" / day
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / report["version"]
    if target.exists():
        return target
    stage = Path(tempfile.mkdtemp(prefix=".pending-", dir=parent))
    (stage / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (stage / "report.md").write_text(render_text(report), encoding="utf-8")
    (stage / "report.html").write_text(render_html(report), encoding="utf-8")
    try:
        os.rename(stage, target)
    except OSError:
        if not target.exists():
            raise
        # A concurrent identical writer won; leave its revision intact.
    return target


def load_latest(root: Path, day: str | None = None) -> dict:
    if day is not None:
        day = date.fromisoformat(day).isoformat()
    files = list((root / "v2" / day).glob("*/report.json")) if day else list((root / "v2").glob("*/*/report.json"))
    reports = []
    for path in files:
        if path.parent.name.startswith("."):
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        reports.append(value)
    if not reports:
        raise KeyError("No operational report")
    return max(reports, key=lambda r: (r["trade_date"], datetime.fromisoformat(r["generated_at"])))
