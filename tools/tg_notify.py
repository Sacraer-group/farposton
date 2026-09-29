# -*- coding: utf-8 -*-
"""Отправка текста в Telegram (бот → личный чат сотрудника). Без сторонних библиотек.

Токен и chat_id — в prognoz/.env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Пустой
TELEGRAM_CHAT_ID определяется сам по getUpdates (сотрудник один раз пишет боту /start)
и, если передан --save, дописывается в .env. Отправляет только наш сервер: с сервера
клиента api.telegram.org недоступен.

    py prognoz/tools/tg_notify.py --text "проверка"
    py prognoz/tools/tg_notify.py --file analysis/output/ai_reports/discipline-2026-09-18.txt --save
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROGNOZ = Path(__file__).resolve().parents[1]
ENV_FILE = PROGNOZ / ".env"
API = "https://api.telegram.org/bot{token}/{method}"
CHUNK = 3900                      # лимит Telegram 4096 символов на сообщение


def read_env(path: Path = ENV_FILE) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def _call(token: str, method: str, payload: dict | None = None, timeout: int = 30) -> dict:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(payload or {}).encode()
    req = urllib.request.Request(url, data=data if payload else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Telegram {method}: HTTP {exc.code} {body}") from None


def detect_chat_id(token: str) -> str | None:
    """chat_id первого личного чата в getUpdates (после /start от сотрудника)."""
    updates = _call(token, "getUpdates").get("result", [])
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private" and chat.get("id"):
            return str(chat["id"])
    return None


def save_chat_id(chat_id: str, path: Path = ENV_FILE) -> None:
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for i, line in enumerate(lines):
        if line.startswith("TELEGRAM_CHAT_ID="):
            lines[i] = f"TELEGRAM_CHAT_ID={chat_id}"
            break
    else:
        lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + nl.join(lines).encode("utf-8"))


def send(text: str, *, token: str | None = None, chat_id: str | None = None,
         save: bool = False) -> list[int]:
    env = read_env()
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан (prognoz/.env)")
    if not chat_id:
        chat_id = detect_chat_id(token)
        if not chat_id:
            raise RuntimeError("TELEGRAM_CHAT_ID пуст и бот ещё не получал /start — "
                               "напишите боту любое сообщение и повторите")
        if save:
            save_chat_id(chat_id)
    ids: list[int] = []
    for start in range(0, max(len(text), 1), CHUNK):
        chunk = text[start:start + CHUNK]
        res = _call(token, "sendMessage", {"chat_id": chat_id, "text": chunk,
                                           "disable_web_page_preview": "true"})
        if not res.get("ok"):
            raise RuntimeError(f"Telegram sendMessage: {res}")
        ids.append(int(res["result"]["message_id"]))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--save", action="store_true", help="дописать найденный chat_id в .env")
    args = parser.parse_args()
    if args.file is not None:
        text = args.file.read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        parser.error("нужен --text или --file")
    ids = send(text, save=args.save)
    print(f"отправлено сообщений: {len(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
