# -*- coding: utf-8 -*-
"""Клиент Chat Completions, Responses и OpenCode Go Messages API на httpx.

Настройки — из web.config.load_settings(): LLM_BASE_URL / LLM_API_KEY / LLM_MODEL /
LLM_TIMEOUT_S / LLM_PROXY_URL. Прокси применяется только к запросам LLM и не
меняет сетевое поведение остальных частей веб-сервиса. Ключа может не быть
(пилот стартует без ИИ) — модуль обязан жить без него и даже без установленного
httpx: импорт httpx ленивый, внутри функций.
"""
from __future__ import annotations

import json
import hashlib
import re
from typing import AsyncIterator
from urllib.parse import urlsplit

from web.config import WebSettings, load_settings

# Единый текст «помощник выключен» — его показывают и /api/chat, и статус на странице
NO_KEY_MESSAGE = (
    "Помощник не подключён: нет ключа ИИ (LLM_API_KEY). "
    "Числа на остальных вкладках считает конвейер — они работают без ИИ."
)


def is_configured(settings: WebSettings | None) -> bool:
    """LLM включён, только когда заданы все три: base_url, ключ и модель."""
    return bool(settings and settings.llm_api_key
                and settings.llm_base_url and settings.llm_model)


def _endpoint(settings: WebSettings) -> str:
    base_url = settings.llm_base_url.rstrip("/")
    parsed = urlsplit(base_url)
    # http допустим только для loopback: у клиента помощник ходит на наш релей через обратный
    # SSH-туннель (127.0.0.1:8090), снаружи OpenRouter отвечает 403 (08.09.2026).
    loopback = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    scheme = parsed.scheme.lower()
    if not parsed.netloc or not (scheme == "https" or (scheme == "http" and loopback)):
        raise RuntimeError("LLM_BASE_URL должен быть HTTPS-адресом (http — только для 127.0.0.1/localhost, туннель)")
    return base_url + ("/messages" if _uses_messages(settings) else
                       "/responses" if _uses_responses(settings) else "/chat/completions")


def _uses_messages(settings: WebSettings) -> bool:
    parsed = urlsplit(getattr(settings, "llm_base_url", ""))
    return (parsed.hostname == "opencode.ai"
            and parsed.path.rstrip("/") == "/zen/go/v1"
            and str(getattr(settings, "llm_model", "")).lower() == "qwen3.8-flash")


def _uses_responses(settings: WebSettings) -> bool:
    """OpenCode serves GPT models through Responses, not Chat Completions."""
    parsed = urlsplit(getattr(settings, "llm_base_url", ""))
    return (parsed.hostname == "opencode.ai"
            and parsed.path.rstrip("/") in {"/zen/v1", "/zen/go/v1", "/inference/openai/v1"}
            and str(settings.llm_model).lower().startswith("gpt-"))


def _headers(settings: WebSettings, messages: list[dict]) -> dict:
    headers = {"Authorization": f"Bearer {settings.llm_api_key}",
               "Content-Type": "application/json"}
    if urlsplit(settings.llm_base_url).path.rstrip("/") == "/zen/go/v1":
        first_question = next((str(m.get("content", "")) for m in messages
                               if m.get("role") == "user"), "")
        headers["x-opencode-session"] = hashlib.sha256(first_question.encode()).hexdigest()
        headers["User-Agent"] = "forecast-analytics-demo/1.0"
    if _uses_messages(settings):
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _client_options(settings: WebSettings, timeout) -> dict:
    """Параметры HTTP-клиента: только явный LLM-прокси, без системного proxy env."""
    return {
        "timeout": timeout,
        "proxy": settings.llm_proxy_url or None,
        "trust_env": False,
    }


def _is_gpt5_model(model: str) -> bool:
    """Recognise direct and provider-prefixed GPT-5 model identifiers.

    OpenRouter uses names such as ``openai/gpt-5.6-luna`` while direct OpenAI
    calls use ``gpt-5.6-luna``.  Payload rules must be based on the model part,
    not on the optional provider prefix.
    """
    model_id = str(model or "").strip().lower().rsplit("/", 1)[-1]
    return model_id.startswith("gpt-5")


def _provider_error(response) -> RuntimeError:
    """Build a diagnostic error without copying a provider body into logs."""
    if response.status_code == 403:
        try:
            error = response.json().get("error", {})
            if isinstance(error, dict) and error.get("code") == "unsupported_country_region_territory":
                return RuntimeError("OpenCode Go недоступен из региона этой машины")
        except (ValueError, AttributeError):
            pass
    request_id = ""
    for name in ("x-request-id", "request-id", "x-openrouter-request-id"):
        candidate = str(response.headers.get(name, "")).strip()
        if candidate:
            request_id = re.sub(r"[^A-Za-z0-9._:-]", "", candidate)[:80]
            break
    suffix = f" (request_id={request_id})" if request_id else ""
    return RuntimeError(f"LLM API ответил {response.status_code}{suffix}")


def _payload(messages: list[dict], settings: WebSettings,
             max_tokens: int, stream: bool, tools: list[dict] | None = None) -> dict:
    if _uses_messages(settings):
        system = "\n\n".join(str(m.get("content") or "") for m in messages
                             if m.get("role") == "system")
        convo = []
        for message in messages:
            if message.get("role") == "system":
                continue
            if "_messages_output" in message:
                convo.append({"role": "assistant", "content": message["_messages_output"]})
            elif message.get("role") == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"],
                         "content": message["content"]}
                if convo and convo[-1]["role"] == "user" and isinstance(convo[-1]["content"], list):
                    convo[-1]["content"].append(block)
                else:
                    convo.append({"role": "user", "content": [block]})
            elif message.get("role") == "assistant" and message.get("tool_calls"):
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message["tool_calls"]:
                    function = call["function"]
                    blocks.append({"type": "tool_use", "id": call["id"],
                                   "name": function["name"],
                                   "input": json.loads(function.get("arguments") or "{}")})
                convo.append({"role": "assistant", "content": blocks})
            else:
                convo.append({"role": message["role"], "content": message.get("content") or ""})
        payload = {"model": settings.llm_model, "max_tokens": max_tokens,
                   "stream": stream, "messages": convo}
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": tool["function"]["name"],
                 "description": tool["function"].get("description", ""),
                 "input_schema": tool["function"]["parameters"]}
                for tool in tools]
            if stream:
                payload["tool_choice"] = {"type": "none"}
        return payload
    if _uses_responses(settings):
        input_items = []
        for message in messages:
            if "_response_output" in message:
                input_items.extend(message["_response_output"])
            elif message.get("role") == "tool":
                input_items.append({"type": "function_call_output",
                                    "call_id": message["tool_call_id"],
                                    "output": message["content"]})
            else:
                input_items.append({"role": message["role"],
                                    "content": message.get("content") or ""})
        payload = {"model": settings.llm_model, "input": input_items,
                   "max_output_tokens": max(max_tokens, 4000), "stream": stream,
                   "store": False, "reasoning": {"effort": "low"}}
        if tools:
            payload["tools"] = [{"type": "function", **tool["function"], "strict": False}
                                for tool in tools]
        return payload
    p = {"model": settings.llm_model, "messages": messages, "stream": stream}
    # GPT-5.x принимает только max_completion_tokens (на max_tokens отвечает 400);
    # для остальных OpenAI-совместимых провайдеров оставляем классическое поле.
    gpt5 = _is_gpt5_model(settings.llm_model)
    if gpt5:
        p["max_completion_tokens"] = max_tokens
    else:
        p["max_tokens"] = max_tokens
    if tools:
        p["tools"] = tools
        if gpt5:
            # GPT-5.x в /chat/completions не совмещает tools с reasoning:
            # «To use function tools … set reasoning_effort to 'none'» (ответ 400 API)
            p["reasoning_effort"] = "none"
    return p


async def stream_chat(messages: list[dict], settings: WebSettings,
                      max_tokens: int = 1200,
                      tools: list[dict] | None = None) -> AsyncIterator[str]:
    """Генератор текстовых дельт из /chat/completions со stream:true (SSE).

    Бросает RuntimeError при выключенном LLM или не-200 ответе API —
    вызывающий превращает ошибку в человеческий текст в потоке.
    """
    if not is_configured(settings):
        raise RuntimeError(NO_KEY_MESSAGE)
    import httpx  # noqa: PLC0415 — ленивый импорт: без ключа httpx не нужен вовсе

    timeout = httpx.Timeout(float(settings.llm_timeout_s), connect=10.0)
    async with httpx.AsyncClient(**_client_options(settings, timeout)) as client:
        async with client.stream("POST", _endpoint(settings), headers=_headers(settings, messages),
                                 json=_payload(messages, settings, max_tokens, True, tools)) as resp:
            if resp.status_code != 200:
                raise _provider_error(resp)
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue  # рваная строка SSE — пропускаем, не роняем стрим
                if _uses_responses(settings):
                    if chunk.get("type") == "response.incomplete":
                        raise RuntimeError("Модель не завершила ответ; попробуйте короче")
                    if chunk.get("type") == "response.output_text.delta" and chunk.get("delta"):
                        yield chunk["delta"]
                    continue
                if _uses_messages(settings):
                    if chunk.get("type") == "error":
                        raise RuntimeError("LLM API прервал поток")
                    delta = chunk.get("delta") or {}
                    if chunk.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        yield delta.get("text", "")
                    continue
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if delta:
                    yield delta


async def chat_raw(messages: list[dict], settings: WebSettings,
                   tools: list[dict] | None = None,
                   max_tokens: int = 1200) -> dict:
    """Нестриминговый вызов для петли tool-use: возвращает ПЕРВЫЙ choice целиком —
    {"message": {..., "tool_calls": [...]}, "finish_reason": "tool_calls"|"stop"|...}.

    Бросает RuntimeError при выключенном LLM или не-200 ответе API.
    """
    if not is_configured(settings):
        raise RuntimeError(NO_KEY_MESSAGE)
    import httpx  # noqa: PLC0415

    timeout = httpx.Timeout(float(settings.llm_timeout_s), connect=10.0)
    async with httpx.AsyncClient(**_client_options(settings, timeout)) as client:
        resp = await client.post(
            _endpoint(settings), headers=_headers(settings, messages),
            json=_payload(messages, settings, max_tokens, False, tools))
        if resp.status_code != 200:
            raise _provider_error(resp)
        data = resp.json()
        if _uses_messages(settings):
            content = data.get("content") or []
            tool_calls = [
                {"id": part["id"], "type": "function",
                 "function": {"name": part["name"],
                              "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False)}}
                for part in content if part.get("type") == "tool_use"]
            text = "".join(part.get("text", "") for part in content
                           if part.get("type") == "text")
            if data.get("stop_reason") == "max_tokens":
                raise RuntimeError("Модель не завершила ответ; попробуйте короче")
            return {"message": {"role": "assistant", "content": text,
                                "tool_calls": tool_calls},
                    "_messages_output": content}
        if _uses_responses(settings):
            output = data.get("output") or []
            tool_calls = [{"id": item.get("call_id"), "type": "function",
                           "function": {"name": item.get("name"),
                                        "arguments": item.get("arguments")}}
                          for item in output if item.get("type") == "function_call"]
            return {"message": {"role": "assistant",
                                "content": _response_text(data),
                                "tool_calls": tool_calls},
                    "_response_output": output}
        choices = data.get("choices") or []
        if not choices:
            return {"message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop"}
        return choices[0]


async def chat(messages: list[dict], settings: WebSettings | None = None,
               max_tokens: int = 1200) -> str:
    """Нестриминговый фолбэк: один запрос — весь текст ответа строкой.

    Без ключа НЕ бросает, а возвращает NO_KEY_MESSAGE (удобно звать напрямую).
    """
    if settings is None:
        try:
            settings = load_settings()
        except RuntimeError:   # .env ещё не заполнен — помощник просто «не подключён»
            settings = None
    if not is_configured(settings):
        return NO_KEY_MESSAGE
    import httpx  # noqa: PLC0415

    timeout = httpx.Timeout(float(settings.llm_timeout_s), connect=10.0)
    async with httpx.AsyncClient(**_client_options(settings, timeout)) as client:
        resp = await client.post(_endpoint(settings), headers=_headers(settings, messages),
                                 json=_payload(messages, settings, max_tokens, False))
        if resp.status_code != 200:
            raise _provider_error(resp)
        data = resp.json()
        if _uses_messages(settings):
            if data.get("stop_reason") == "max_tokens":
                raise RuntimeError("Модель не завершила ответ; попробуйте короче")
            return "".join(part.get("text", "") for part in data.get("content") or []
                           if part.get("type") == "text")
        if _uses_responses(settings):
            return _response_text(data)
        choices = data.get("choices") or []
        return ((choices[0].get("message") or {}).get("content") or "") if choices else ""


def _response_text(data: dict) -> str:
    if data.get("status") == "incomplete":
        raise RuntimeError("Модель не завершила ответ; попробуйте короче")
    return "".join(part.get("text", "")
                   for item in data.get("output") or [] if item.get("type") == "message"
                   for part in item.get("content") or [] if part.get("type") == "output_text")
