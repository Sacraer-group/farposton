"""Telegram delivery with a durable per-part ledger. Disabled unless opted in."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime
from .operational import STATUS, LOCAL_TZ, freshness_notice, partial_totals_notice

def split_text(text: str, limit: int = 3500) -> list[str]:
    # Telegram counts UTF-16 units; reserve room for numbering and surrogate pairs.
    parts, current, size = [], [], 0
    for char in text:
        length = len(char.encode("utf-16-le")) // 2
        if size + length > limit:
            parts.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += length
    if current:
        parts.append("".join(current))
    return parts


def _sum_metric(points: list[dict], key: str):
    values = [p["metrics"].get(key) for p in points]
    return sum(values) if values and all(v is not None for v in values) else None


def _fmt(value) -> str:
    return "нет данных" if value is None else f"{value:g}"


def _compact_point(point: dict) -> str:
    metrics = point["metrics"]
    docs = {d["kind"]: d["state"] for d in point.get("documents", [])}
    labels = {"sales": "продажи", "orders": "заказ", "writeoffs": "списания"}
    problems = [f"{labels.get(kind, kind)}: {STATUS[state]}" for kind, state in docs.items()
                if state not in {"present", "not_required"}]
    documents = "; ".join(problems) if problems else ("в порядке" if docs else "не проверены")
    early = "сведений нет"
    if point.get("early_stop"):
        early = "предположение; последняя продажа: " + "; ".join(
            f"{row['sku']} {row['last_sale_at']}" for row in point["early_stop"])
    if metrics.get("edit_count"):
        edits = (f"правки {_fmt(metrics['edit_count'])} поз., "
                 f"+{_fmt(metrics.get('edit_up'))}/−{_fmt(metrics.get('edit_down'))}")
    elif metrics.get("confirmed_unchanged"):
        edits = "без правок подтверждено"
    else:
        edits = "правки не подтверждены"
    text = (f"{point['name']} — " + ("данные подтверждены" if point["complete"] else "данные неполные")
            + f"\nПрогноз {_fmt(metrics.get('forecast'))}; в сервисе {_fmt(metrics.get('web_order'))}"
            + f" ({edits}); заказ из документов {_fmt(metrics.get('order'))}."
            + f"\nПродажи нетто {_fmt(metrics.get('sales'))}; возвраты {_fmt(metrics.get('returns'))}; "
            + f"списания {_fmt(metrics.get('writeoff'))} / {_fmt(metrics.get('writeoff_rub'))} ₽."
            + f"\nСопоставимо {_fmt(metrics.get('coverage_pct'))}%. Раннее окончание: {early}."
            + f"\nДокументы: {documents}.")
    if problems or not point["complete"]:
        text += "\nОтветственный: " + (point.get("owner") or "не настроен") + "; проверить документы и полноту выгрузки."
    if point.get("activation_pending"):
        text += "\nДата подключения точки не подтверждена."
    if freshness_notice(point):
        text += "\n" + freshness_notice(point)
    if partial_totals_notice(point):
        text += "\n" + partial_totals_notice(point)
    if point.get("forecast_error"):
        text += "\n" + point["forecast_error"]
    if point.get("forecast_provenance") == "legacy_unverified":
        text += "\nВерсия прогноза не подтверждена архивом."
    stamps = [source.get("loaded_at") for source in point.get("sources", {}).values()]
    oldest = min((datetime.fromisoformat(s).astimezone(LOCAL_TZ) for s in stamps if s), default=None)
    text += "\nИсточники от: " + (oldest.strftime("%d.%m %H:%M") if oldest else "неизвестно")
    if any(not stamp for stamp in stamps):
        text += " (есть источники без времени)"
    if point.get("narrative"):
        text += "\n" + point["narrative"]
    return text


def report_messages(report: dict, web_url: str = "") -> list[str]:
    """Keep point blocks intact and state shared limitations only once."""
    points = report["points"]
    complete = sum(bool(p["complete"]) for p in points)
    stamp = datetime.fromisoformat(report['generated_at']).astimezone(LOCAL_TZ)
    title = f"Итоги за {report['trade_date']}\nСформировано {stamp:%d.%m %H:%M} ВДК. Проверено полностью: {complete}/{len(points)} точек."
    shared = []
    no_early_evidence = bool(points) and all(not p.get('early_stop') for p in points)
    common_unknown_docs = bool(points) and all(
        {d['kind'] for d in p.get('documents', []) if d['state'] == 'not_assessable'} == {'sales', 'writeoffs'}
        and all(d['state'] in {'present', 'not_assessable', 'not_required'} for d in p.get('documents', []))
        for p in points)
    if any(not p['complete'] or partial_totals_notice(p) for p in points):
        shared.append('Отчёт предварительный. «—» означает нет данных, не ноль; «*» — сумма только полученных строк.')
    stale = [p.get('source_freshness', {}).get('receipts', {}) for p in points]
    if points and all(s.get('rows_on_report_date') == 0 for s in stale):
        dates = sorted({s.get('latest_date') or 'неизвестна' for s in stale})
        shared.append('Чеков за отчётный день нет у всех точек; последняя дата ' + ', '.join(dates) + '. Это не подтверждает нулевые продажи. Команде сервиса: проверить загрузку.')
    blocks = [title, *shared]
    if common_unknown_docs:
        blocks.append('По всем точкам: документы продаж и списаний проверить невозможно. Сначала нужна подтверждённая выгрузка.')
    if no_early_evidence:
        blocks.append('Раннее окончание товаров: подтвердить позиции и время пока невозможно.')
    for p in points:
        m = p['metrics']
        def quantity(key):
            if m.get(key) is not None:
                return _fmt(m[key])
            found = p.get('observed_totals', {}).get(key, {})
            return _fmt(found['value']) + '*' if found.get('rows', 0) else '—'
        edits = (f"{m['edit_count']} поз., +{_fmt(m['edit_up'])}/−{_fmt(m['edit_down'])}"
                 if m.get('edit_count') else 'без правок, подтверждено' if m.get('confirmed_unchanged') else 'подтверждения нет')
        lines = [p['name'], f"Прогноз {quantity('forecast')} → в сервисе {quantity('web_order')} ({edits}).",
                 f"Заказ по документам {quantity('order')}; продано {quantity('sales')}; возвраты {quantity('returns')}.",
                 f"Списано {quantity('writeoff')} шт. / {quantity('writeoff_rub')} ₽. Сопоставимо {_fmt(m.get('coverage_pct'))}% позиций."]
        early = p.get('early_stop', [])
        if not no_early_evidence:
            lines.append('Раннее окончание: ' + ('предположение, проверить остатки; ' + '; '.join(f"{r['sku']} — последняя продажа {r['last_sale_at']}" for r in early) if early else 'проверить невозможно — нет подтверждённых сведений.'))
        labels = {'sales': 'продажи', 'orders': 'заказ', 'writeoffs': 'списания'}
        docs = p.get('documents', [])
        issues = [d for d in docs if d['state'] not in {'present', 'not_required'}]
        if not common_unknown_docs:
            lines.append('Документы: ' + ('; '.join(f"{labels[d['kind']]} — {STATUS[d['state']]}" for d in issues) if issues else 'проверены' if docs else 'не проверены') + '.')
        if any(d['state'] in {'missing', 'unposted'} for d in issues):
            lines.append('Действие: проверить перечисленные документы. Ответственный: ' + (p.get('owner') or 'не назначен') + '.')
        if p.get('activation_pending'):
            lines.append('Дата подключения точки не подтверждена.')
        if p.get('forecast_error'):
            lines.append(p['forecast_error'])
        if p.get('forecast_provenance') == 'legacy_unverified':
            lines.append('Версия прогноза не подтверждена архивом.')
        if not (points and all(s.get('rows_on_report_date') == 0 for s in stale)) and freshness_notice(p):
            lines.append(freshness_notice(p))
        stamps = [s.get('loaded_at') for s in p.get('sources', {}).values()]
        oldest = min((datetime.fromisoformat(s).astimezone(LOCAL_TZ) for s in stamps if s), default=None)
        lines.append('Источники от: ' + (oldest.strftime('%d.%m %H:%M') if oldest else 'неизвестно')
                     + (' (время известно не для всех)' if any(not s for s in stamps) else '') + '.')
        blocks.append('\n'.join(lines))
    if web_url:
        if not web_url.startswith("https://"):
            raise ValueError("Report link must use HTTPS")
        blocks.append('Подробности: ' + web_url.rstrip('/') + '/reports')
    messages = []
    for block in blocks:
        block = re.sub(r'@(?=[A-Za-z0-9_])', '＠', block)
        for part in split_text(block):
            if messages and len((messages[-1] + '\n\n' + part).encode('utf-16-le')) // 2 <= 3500:
                messages[-1] += '\n\n' + part
            else:
                messages.append(part)
    return messages


class DeliveryError(Exception):
    def __init__(self, state: str, retry_after: int = 0):
        super().__init__(state)  # Never retain an exception containing the token URL.
        self.state, self.retry_after = state, retry_after


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_message(token: str, recipient: str, text: str) -> str:
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise DeliveryError("failed")
    payload = json.dumps({"chat_id": recipient, "text": text,
                          "link_preview_options": {"is_disabled": True}}).encode()
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage",
                                     data=payload, headers={"Content-Type": "application/json"})
    try:
        handlers = [_NoRedirect()]
        proxy = os.environ.get("TELEGRAM_HTTPS_PROXY", "")
        if proxy:
            if proxy != "http://127.0.0.1:18889":
                raise DeliveryError("failed")
            handlers.append(urllib.request.ProxyHandler({"https": proxy}))
        with urllib.request.build_opener(*handlers).open(request, timeout=20) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code == 429:
            try:
                wait = max(1, int(json.loads(exc.read()).get("parameters", {}).get("retry_after", 60)))
            except (ValueError, TypeError):
                wait = 60
            raise DeliveryError("retry", wait) from None
        raise DeliveryError("unknown" if code >= 500 else "failed") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise DeliveryError("unknown") from None
    if result.get("ok") and isinstance(result.get("result"), dict) and "message_id" in result["result"]:
        return str(result["result"]["message_id"])
    if result.get("error_code") == 429:
        raise DeliveryError("retry", max(1, int(result.get("parameters", {}).get("retry_after", 60))))
    raise DeliveryError("failed" if result.get("ok") is False else "unknown")


def connect_ledger(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS delivery (
        key TEXT PRIMARY KEY, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        updated REAL NOT NULL, retry_at REAL NOT NULL DEFAULT 0, message_id TEXT)""")
    con.commit()
    return con


def deliver(messages: list[str], *, identity: str, recipient: str, ledger: Path,
            token: str = "", enabled: bool = False, sender=send_message) -> dict:
    if not enabled:
        return {"state": "dry_run", "parts": len(messages)}
    if not token or not recipient:
        return {"state": "disabled", "parts": len(messages)}
    results = []
    con = connect_ledger(ledger)
    try:
        for index, message in enumerate(messages):
            key = hashlib.sha256(f"{identity}|{recipient}|{index}".encode()).hexdigest()
            now = time.time()
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT state,attempts,updated,retry_at FROM delivery WHERE key=?", (key,)).fetchone()
            if row and (row[0] in {"sent", "pending", "unknown", "failed"} or row[1] >= 3 or row[3] > now):
                state = row[0]
                if state == "pending" and now - row[2] > 120:
                    state = "unknown"
                    con.execute("UPDATE delivery SET state='unknown' WHERE key=?", (key,))
                con.commit()
                results.append(state)
                if state != "sent":
                    break
                continue
            con.execute("INSERT INTO delivery(key,state,attempts,updated) VALUES(?, 'pending',1,?) "
                        "ON CONFLICT(key) DO UPDATE SET state='pending',attempts=attempts+1,updated=excluded.updated",
                        (key, now))
            con.commit()  # Claim persists before network I/O; uncertain delivery is never blindly replayed.
            try:
                message_id = sender(token, recipient, message)
                state, retry_at = "sent", 0
            except DeliveryError as exc:
                message_id, state, retry_at = None, exc.state, now + exc.retry_after
            except Exception:
                message_id, state, retry_at = None, "unknown", 0
            with con:
                con.execute("UPDATE delivery SET state=?,updated=?,retry_at=?,message_id=? WHERE key=?",
                            (state, time.time(), retry_at, message_id, key))
            results.append(state)
            if state != "sent":
                break
    finally:
        con.close()
    return {"state": "sent" if len(results) == len(messages) and all(x == "sent" for x in results) else results[-1],
            "parts": len(messages), "confirmed": results.count("sent")}


def destination(cfg: dict, env=None) -> tuple[str, bool]:
    env = os.environ if env is None else env
    mode = cfg.get("delivery_mode", "pilot")
    if mode == "group":
        days = set(cfg.get("pilot_successful_dates", []))
        if len(days) < 3 or not cfg.get("group_approved_at"):
            raise ValueError("Group delivery requires three successful pilot dates and explicit approval")
        return env.get("TELEGRAM_GROUP_CHAT_ID", ""), True
    if mode != "pilot":
        raise ValueError("Unknown delivery mode")
    return env.get("TELEGRAM_PILOT_CHAT_ID", ""), False
