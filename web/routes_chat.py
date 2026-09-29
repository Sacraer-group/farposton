# -*- coding: utf-8 -*-
"""API вкладки «Помощник»: диалог с LLM по данным прогноза, ответ — SSE-стримом.

POST /api/chat  {messages:[{role,content}]} → text/event-stream:
    data: {"tool": "get_order"}  — помощник пошёл за данными (служебный чанк)
    data: {"delta": "..."}       — куски текста ответа
    data: {"done": true}         — финал (всегда приходит, даже после ошибки)
Любая беда (нет ключа, лимит, упал LLM) — не HTTP-ошибка, а один чанк
с человеческим текстом + done: страница показывает его как обычный ответ.

GET /api/chat/status → {llm_ok, model} — страница по нему решает, включать ли ввод.

Подключается оркестратором: app.include_router(routes_chat.router).
"""
from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from typing import AsyncIterator, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from web import analytics
from web.access import principal_from_session
from web.auth import require_auth
from web.ai import chat as chat_ctx
from web.ai import llm

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

# Один одновременный вызов LLM на весь сервис: пилот, ключ общий, очередь честнее
_llm_semaphore = asyncio.Semaphore(1)
_limiter = chat_ctx.HourlyLimiter()
DATA_PERMISSION_MESSAGE = (
    "Помощник не подключён: внешний LLM не настроен либо не разрешена передача "
    "данных клиента (LLM_ALLOW_CLIENT_DATA=1). Остальные вкладки работают без ИИ."
)


@lru_cache(maxsize=1)
def _settings():
    """Настройки лениво: модуль импортируется и без .env (llm тогда «не подключён»)."""
    from web.config import load_settings  # noqa: PLC0415
    try:
        return load_settings()
    except RuntimeError:
        return None


def _assistant_enabled(settings) -> bool:
    return bool(
        llm.is_configured(settings)
        and getattr(settings, "llm_allow_client_data", False)
    )


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8 * 1024)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(
        default_factory=list, max_length=chat_ctx.MAX_HISTORY_MESSAGES,
    )


def _rate_limit_key(username: str) -> str:
    """Stable per-account key: re-login must not reset the hourly limit."""
    return "user:" + username.strip().casefold()


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


async def chat_events(history: list[dict], session_key: str,
                      point_scope: str | None = None) -> AsyncIterator[str]:
    """Генератор SSE-чанков ответа помощника (вынесен отдельно — его зовут тесты)."""
    settings = _settings()
    if not _assistant_enabled(settings):
        yield _sse({"delta": DATA_PERMISSION_MESSAGE})
        yield _sse({"done": True})
        return

    wait_s = _limiter.check(session_key)
    if wait_s:
        minutes = wait_s // 60 + 1
        yield _sse({"delta": (f"Лимит помощника: не больше {chat_ctx.RATE_LIMIT_MESSAGES} "
                              f"сообщений в час. Попробуйте минут через {minutes}.")})
        yield _sse({"done": True})
        return

    trimmed = chat_ctx.trim_history(history)
    if not trimmed:
        yield _sse({"delta": "Пустое сообщение — напишите вопрос про прогноз, точность или заявку."})
        yield _sse({"done": True})
        return

    messages = [{"role": "system",
                 "content": chat_ctx.build_runtime_system_message(point_scope)}] + trimmed
    try:
        async with _llm_semaphore:
            got_text = False
            # Петля tool-use: {"tool": имя} — служебные чанки, {"delta"} — текст ответа
            async for event in chat_ctx.run_with_tools(
                    messages, settings, point_scope=point_scope):
                if "delta" in event:
                    got_text = True
                yield _sse(event)
            if not got_text:  # ответ оказался пуст — нестриминговый фолбэк без tools
                text = await llm.chat(messages, settings, max_tokens=chat_ctx.MAX_TOKENS)
                yield _sse({"delta": text or "Помощник вернул пустой ответ — попробуйте переформулировать."})
    except Exception as exc:
        logger.exception("Помощник: ошибка вызова LLM")
        if isinstance(exc, RuntimeError) and str(exc).startswith("LLM API ответил 402"):
            yield _sse({"delta": "OpenCode Go отклонил запрос (402). Проверьте подписку или лимит."})
        elif isinstance(exc, RuntimeError) and str(exc) == "OpenCode Go недоступен из региона этой машины":
            yield _sse({"delta": str(exc) + "."})
        else:
            yield _sse({"delta": (f"Помощник временно недоступен ({type(exc).__name__}). "
                                  "Числа на остальных вкладках работают без ИИ — загляните туда, "
                                  "а вопрос повторите позже.")})
    yield _sse({"done": True})


@router.post("/chat")
async def api_chat(body: ChatBody, request: Request):
    """Диалог с помощником: тело {messages:[{role,content}]}, ответ — SSE-стрим."""
    history = [{"role": m.role, "content": m.content} for m in body.messages]
    principal = principal_from_session(request)
    # Ключ стабилен для аккаунта: повторный вход не обходит лимит общего API-ключа.
    session_key = _rate_limit_key(principal.username)
    point_scope = None if principal.is_admin else analytics.point_name_for_key(
        principal.point_key or ""
    )
    return StreamingResponse(
        chat_events(history, session_key, point_scope),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/status")
async def api_chat_status():
    """Статус помощника: {llm_ok, model} — страница гасит ввод при llm_ok=false."""
    settings = _settings()
    return JSONResponse({
        "llm_ok": _assistant_enabled(settings),
        "model": (settings.llm_model or None) if settings else None,
    })
