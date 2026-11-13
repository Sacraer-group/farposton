"""Local report CLI; send is explicit and disabled by default."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from prognoz.report import delivery, local_sources, operational as op


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".pending-", delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        temp = Path(f.name)
    os.replace(temp, path)


def reminder_messages(previous: dict, current: dict, *, group: bool, now: datetime) -> list[str]:
    if now.astimezone(op.LOCAL_TZ).hour < 10 or previous["trade_date"] != current["trade_date"]:
        return []
    old = {p["point_key"]: p for p in previous["points"]}
    result = []
    labels = {"sales": "продаж", "orders": "заказа", "writeoffs": "списания"}
    for p in current["points"]:
        before = old.get(p["point_key"])
        if not before:
            continue
        old_docs = {d["kind"]: d for d in before["documents"]}
        for doc in p["documents"]:
            earlier = old_docs.get(doc["kind"])
            if not earlier or earlier["state"] not in {"missing", "unposted", "not_observed"}:
                continue
            at, old_at = doc.get("source_at"), earlier.get("source_at")
            fresh = False
            if at:
                stamp = datetime.fromisoformat(at)
                fresh = (stamp.tzinfo is not None and stamp.astimezone(op.LOCAL_TZ).date() == now.astimezone(op.LOCAL_TZ).date()
                         and timedelta(0) <= now - stamp <= timedelta(hours=2)
                         and (not old_at or stamp > datetime.fromisoformat(old_at)))
            if not fresh:
                result.append(f"{p['name']}: свежих сведений для повторной проверки документа {labels[doc['kind']]} нет; последняя загрузка {at or 'неизвестна'}.")
            elif doc["state"] == "present":
                result.append(f"{p['name']}: вопрос по документу {labels[doc['kind']]} закрыт по новой выгрузке.")
            elif doc["remindable"]:
                mention = p.get("telegram_username", "")
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", mention):
                    mention = ""
                prefix = ("@" + mention + ", ") if group and mention else ""
                result.append(f"{prefix}{p['name']}: пожалуйста, проверьте документ {labels[doc['kind']]} за {current['trade_date']}: {op.STATUS[doc['state']]}. Повторно проверено по загрузке {at}.")
    return result


def watchdog_messages(report: dict | None, state: dict | None, *, day: str) -> list[str]:
    issues = []
    if not report or report.get("trade_date") != day:
        issues.append("Свежий отчёт не сформирован.")
    else:
        pending = [p["name"] for p in report["points"] if not p["complete"]]
        if pending:
            issues.append("Полнота загрузки не подтверждена: " + ", ".join(pending) + ".")
        if not state or state.get("version") != report["version"] or state.get("state") != "sent":
            issues.append("Доставка всех частей отчёта не подтверждена.")
    return [f"Контроль 08:00 UTC+10 · {day}\n" + "\n".join(issues)] if issues else []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["generate", "send", "watchdog", "remind"])
    parser.add_argument("--date", help="Previous trade day in UTC+10 by default")
    parser.add_argument("--input", type=Path, help="Local normalised evidence JSON (offline fixtures/import contract)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--send", action="store_true", help="Also requires TELEGRAM_ENABLED=1 and credentials in env")
    args = parser.parse_args(argv)
    if os.getenv("PROGNOZ_1C_WRITE") != "0":
        parser.error("PROGNOZ_1C_WRITE must be 0")
    now = op.now_local()
    day = date.fromisoformat(args.date).isoformat() if args.date else (now.date()-timedelta(days=1)).isoformat()
    root = args.output or local_sources.output_root() / "ai_reports"
    cfg = local_sources.read_config()
    try:
        previous = op.load_latest(root, day)
    except KeyError:
        previous = None
    report = previous
    if args.action in {"generate", "remind"}:
        bundle = json.loads(args.input.read_text(encoding="utf-8")) if args.input else local_sources.collect(day, generated_at=now)
        if bundle["trade_date"] != day:
            parser.error("Input date must match --date")
        report = op.build_report(bundle)
        # Reuse already-published evidence, including model wording, on identical reruns.
        if previous is None or report["version"] != previous["version"]:
            from prognoz.report.wording import add_narrative
            add_narrative(report)
            folder = op.persist(report, root)
            report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        else:
            report = previous
    state_path = root / "delivery-status" / (day + ".json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    enabled = args.send and os.getenv("TELEGRAM_ENABLED") == "1"
    if args.action == "watchdog":
        recipient, group = os.getenv("TELEGRAM_PILOT_CHAT_ID", ""), False
    elif enabled:
        recipient, group = delivery.destination(cfg)
    else:
        recipient, group = "", False
    recipient_hash = op.digest({"recipient": recipient, "mode": cfg.get("delivery_mode", "pilot")})
    if state and state.get("recipient_hash") != recipient_hash:
        state = None
    if args.action == "watchdog":
        if now.hour < 8:
            print("watchdog: before 08:00 UTC+10; skipped")
            return 0
        messages = watchdog_messages(report, state, day=day)
        recipient = os.getenv("TELEGRAM_PILOT_CHAT_ID", "")
        identity = "watchdog:" + day
    elif args.action == "remind":
        messages = reminder_messages(previous, report, group=group, now=now) if previous else []
        identity = "reminder:" + day
        batch_file = root / "reminder-batches" / f"{day}-{recipient_hash[:16]}.json"
        if batch_file.exists():
            messages = json.loads(batch_file.read_text(encoding="utf-8"))["messages"]
        elif messages and args.send and os.getenv("TELEGRAM_ENABLED") == "1":
            atomic_json(batch_file, {"messages": messages})
    else:
        if report is None:
            print("report_missing")
            return 2
        messages = delivery.report_messages(report, cfg.get("web_url", ""))
        identity = "report:" + day + ":" + report["version"]
    if not messages:
        print("nothing_to_send")
        return 0
    result = delivery.deliver(messages, identity=identity, recipient=recipient,
                              ledger=root / "delivery.sqlite", token=os.getenv("TELEGRAM_BOT_TOKEN", ""), enabled=enabled)
    if report is not None and args.action in {"generate", "send"} and enabled:
        atomic_json(state_path, dict(result, version=report["version"], trade_date=day,
                                     recipient_hash=recipient_hash, generated_at=op.now_local().isoformat()))
    print(json.dumps(dict(result, trade_date=day), ensure_ascii=False))
    return 0 if result["state"] in {"sent", "dry_run"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
