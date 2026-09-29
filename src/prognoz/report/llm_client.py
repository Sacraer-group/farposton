# -*- coding: utf-8 -*-
"""Клиент LLM по OpenAI-совместимому REST (OpenRouter сейчас, ProxyAPI потом).

Нарочно на stdlib (urllib): ночной конвейер живёт в системном питоне, и тянуть
в него httpx ради одного POST не за чем. Веб-сервис со стримингом чата держит
свой клиент в своём venv — контракт (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL) общий.

Ключ — только в prognoz/.env (в git не попадает), в лог не пишется. Смена
провайдера (OpenRouter → ProxyAPI клиента) — правка .env без правки кода.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

PROJ = Path(__file__).resolve().parents[3]          # …\prognoz
ENV_PATH = PROJ / ".env"


def load_env(path: Path = ENV_PATH) -> dict:
    """Плоский разбор KEY=VALUE (без зависимостей; кавычки по краям снимаются)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def llm_settings(env: dict | None = None) -> dict | None:
    """None, если LLM не настроен — вызывающий обязан уметь жить без текста."""
    env = env if env is not None else load_env()
    key = env.get("LLM_API_KEY", "")
    allow_client_data = str(env.get("LLM_ALLOW_CLIENT_DATA", "")).strip().casefold()
    if not key or allow_client_data not in {"1", "true", "yes", "on"}:
        return None
    return {
        "base_url": env.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        "api_key": key,
        "model": env.get("LLM_MODEL", ""),
        # утренней сводке хватает дешёвого яруса; пусто — берётся основная
        "model_cheap": env.get("LLM_MODEL_CHEAP", "") or env.get("LLM_MODEL", ""),
        "timeout_s": int(env.get("LLM_TIMEOUT_S", "60")),
    }


def _is_gpt5_model(model: str) -> bool:
    """Support both direct IDs and provider-prefixed IDs such as OpenRouter's."""
    model_id = str(model or "").strip().lower().rsplit("/", 1)[-1]
    return model_id.startswith("gpt-5")


def _endpoint(base_url: str) -> str:
    base_url = str(base_url or "").rstrip("/")
    parsed = urlsplit(base_url)
    # http допустим только для loopback: у клиента помощник ходит на наш релей через обратный
    # SSH-туннель (127.0.0.1:8090), снаружи OpenRouter отвечает 403 (08.09.2026).
    loopback = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    scheme = parsed.scheme.lower()
    if not parsed.netloc or not (scheme == "https" or (scheme == "http" and loopback)):
        raise ValueError("LLM_BASE_URL должен быть HTTPS-адресом (http — только для 127.0.0.1/localhost, туннель)")
    return base_url + "/chat/completions"


def chat(messages: list[dict], *, settings: dict | None = None,
         max_tokens: int = 2000, retries: int = 2) -> str | None:
    """Один ответ без стриминга. None — LLM недоступен/не настроен.

    Особенности GPT-5.x: temperature не поддерживается (reasoning-модель),
    лимит зовётся max_completion_tokens, и он тратится в том числе на
    внутренние размышления — потому бюджет 2000, а не 900."""
    st = settings or llm_settings()
    if st is None or not st["model"]:
        return None
    body = {"model": st["model"], "messages": messages}
    if _is_gpt5_model(st["model"]):
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _endpoint(st["base_url"]), data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {st['api_key']}"})
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=st["timeout_s"]) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, KeyError, json.JSONDecodeError) as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    print(f"LLM недоступен ({type(last).__name__}) — сводка выйдет без текста")
    return None
