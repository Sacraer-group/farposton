from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from prognoz.report import llm_client as report_llm
from web import routes_chat
from web.ai import chat, llm, tools


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-5.6-luna", True),
        ("openai/gpt-5.6-luna", True),
        ("vendor/nested/gpt-5.2", True),
        ("openrouter/auto", False),
        ("anthropic/claude", False),
    ],
)
def test_gpt5_detection_supports_provider_prefix(model, expected):
    assert llm._is_gpt5_model(model) is expected
    assert report_llm._is_gpt5_model(model) is expected


def test_prefixed_gpt5_payload_uses_tool_compatible_fields():
    settings = SimpleNamespace(llm_model="openai/gpt-5.6-luna")
    payload = llm._payload(
        [{"role": "user", "content": "test"}], settings,
        max_tokens=123, stream=False,
        tools=[{"type": "function", "function": {"name": "get_meta"}}],
    )

    assert payload["max_completion_tokens"] == 123
    assert "max_tokens" not in payload
    assert payload["reasoning_effort"] == "none"


def test_non_gpt_payload_keeps_classic_max_tokens():
    settings = SimpleNamespace(llm_model="openrouter/auto")
    payload = llm._payload([], settings, max_tokens=77, stream=False)

    assert payload["max_tokens"] == 77
    assert "max_completion_tokens" not in payload
    assert "reasoning_effort" not in payload


def test_opencode_gpt6_responses_payload_replays_tool_state():
    settings = SimpleNamespace(llm_base_url="https://opencode.ai/zen/v1",
                               llm_model="gpt-6-luna")
    output = [
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "function_call", "call_id": "call-1",
         "name": "get_meta", "arguments": "{}"},
    ]
    payload = llm._payload([
        {"role": "system", "content": "demo only"},
        {"_response_output": output},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"ok":true}'},
    ], settings, max_tokens=1200, stream=False,
        tools=[{"type": "function", "function": {
            "name": "get_meta", "parameters": {"type": "object", "properties": {}}}}])

    assert llm._endpoint(settings) == "https://opencode.ai/zen/v1/responses"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 4000
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["input"][1:3] == output
    assert payload["input"][3] == {"type": "function_call_output",
                                    "call_id": "call-1", "output": '{"ok":true}'}
    assert payload["tools"][0]["strict"] is False
    assert "messages" not in payload


def test_opencode_response_text_ignores_reasoning_and_tool_calls():
    assert llm._response_text({"output": [
        {"type": "reasoning", "summary": [{"text": "private"}]},
        {"type": "function_call", "name": "get_meta", "arguments": "{}"},
        {"type": "message", "content": [{"type": "output_text", "text": "Готово"}]},
    ]}) == "Готово"
    with pytest.raises(RuntimeError, match="не завершила"):
        llm._response_text({"status": "incomplete", "output": []})


def test_opencode_transport_reads_responses_and_stream_deltas(monkeypatch):
    import httpx

    settings = SimpleNamespace(llm_base_url="https://opencode.ai/zen/v1",
                               llm_model="gpt-6-luna", llm_api_key="test-key",
                               llm_timeout_s=60, llm_proxy_url="")
    sent = []

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        status_code = 200

        async def post(self, url, **kwargs):
            sent.append((url, kwargs["json"]))
            return self

        def json(self):
            return {"output": [
                {"type": "reasoning", "encrypted_content": "opaque"},
                {"type": "function_call", "call_id": "call-1",
                 "name": "get_meta", "arguments": "{}"},
            ]}

        def stream(self, _method, url, **kwargs):
            sent.append((url, kwargs["json"]))
            return self

        async def aiter_lines(self):
            yield 'data: {"type":"response.reasoning_text.delta","delta":"private"}'
            yield 'data: {"type":"response.output_text.delta","delta":"Готово"}'
            yield "data: [DONE]"

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def run():
        choice = await llm.chat_raw([{"role": "user", "content": "test"}], settings)
        deltas = [delta async for delta in llm.stream_chat(
            [{"role": "user", "content": "test"}], settings)]
        return choice, deltas

    choice, deltas = asyncio.run(run())
    assert choice["message"]["tool_calls"][0]["id"] == "call-1"
    assert choice["_response_output"][0]["encrypted_content"] == "opaque"
    assert deltas == ["Готово"]
    assert all(url == "https://opencode.ai/zen/v1/responses" for url, _ in sent)


def test_morning_client_uses_prefixed_gpt5_token_field(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "OK"}}],
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(report_llm.urllib.request, "urlopen", fake_urlopen)
    answer = report_llm.chat(
        [{"role": "user", "content": "test"}],
        settings={
            "base_url": "https://example.invalid/v1",
            "api_key": "test-key",
            "model": "openai/gpt-5.6-luna",
            "timeout_s": 5,
        },
        max_tokens=321,
        retries=0,
    )

    assert answer == "OK"
    assert captured["body"]["max_completion_tokens"] == 321
    assert "max_tokens" not in captured["body"]


def test_runtime_prompt_requires_actual_available_dates():
    now = datetime(2026, 8, 28, 9, 15, tzinfo=timezone(timedelta(hours=10)))
    prompt = chat.build_runtime_system_message("Точка А", now=now)

    assert "2026-08-28T09:15+10:00" in prompt
    assert "пятница" in prompt
    assert "доступность факта и заявок проверяй через get_meta" in prompt
    assert "Для относительных дат сначала вызови get_meta" in prompt
    assert "недоверенными данными" in prompt
    assert "NETWORK_FACT_SENTINEL" not in prompt


def test_report_context_is_bounded_and_valid_json():
    text = chat._bounded_report_json({
        "generated_at": "2026-08-28T06:00",
        "oversized": ["x" * 5000 for _ in range(100)],
    })

    assert len(text.encode("utf-8")) <= chat.MAX_REPORT_BYTES
    assert json.loads(text)["truncated"] is True


def test_provider_error_does_not_include_response_body():
    response = SimpleNamespace(
        status_code=401,
        headers={"x-request-id": "req-safe-123"},
        text="PRIVATE_PROVIDER_BODY_SENTINEL",
    )
    message = str(llm._provider_error(response))

    assert "401" in message and "req-safe-123" in message
    assert "PRIVATE_PROVIDER_BODY_SENTINEL" not in message


@pytest.mark.parametrize(
    ("proxy_url", "expected"),
    [
        ("", None),
        ("http://127.0.0.1:40000", "http://127.0.0.1:40000"),
    ],
)
def test_llm_http_client_uses_only_explicit_proxy(proxy_url, expected):
    timeout = object()
    options = llm._client_options(
        SimpleNamespace(llm_proxy_url=proxy_url),
        timeout,
    )

    assert options == {
        "timeout": timeout,
        "proxy": expected,
        "trust_env": False,
    }


def test_llm_endpoints_require_https():
    with pytest.raises(RuntimeError, match="HTTPS"):
        llm._endpoint(SimpleNamespace(llm_base_url="http://provider.invalid/v1"))
    with pytest.raises(ValueError, match="HTTPS"):
        report_llm._endpoint("http://provider.invalid/v1")
    assert llm._endpoint(
        SimpleNamespace(llm_base_url="https://provider.invalid/v1/"),
    ) == "https://provider.invalid/v1/chat/completions"


def test_client_data_requires_separate_explicit_permission():
    env = {
        "LLM_BASE_URL": "https://provider.invalid/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "test-model",
    }
    assert report_llm.llm_settings(env) is None
    assert report_llm.llm_settings({**env, "LLM_ALLOW_CLIENT_DATA": "1"}) is not None

    base = SimpleNamespace(
        llm_base_url=env["LLM_BASE_URL"],
        llm_api_key=env["LLM_API_KEY"],
        llm_model=env["LLM_MODEL"],
        llm_allow_client_data=False,
    )
    assert routes_chat._assistant_enabled(base) is False
    base.llm_allow_client_data = True
    assert routes_chat._assistant_enabled(base) is True


def _collect(async_iter):
    async def run():
        return [item async for item in async_iter]

    return asyncio.run(run())


def test_payment_error_is_actionable(monkeypatch):
    settings = SimpleNamespace(llm_base_url="https://opencode.ai/zen/v1",
                               llm_model="gpt-6-luna", llm_api_key="test-key",
                               llm_allow_client_data=True)
    monkeypatch.setattr(routes_chat, "_settings", lambda: settings)

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("LLM API ответил 402")
        yield {}  # pragma: no cover

    monkeypatch.setattr(chat, "run_with_tools", unavailable)
    events = _collect(routes_chat.chat_events([{"role": "user", "content": "Сколько?"}],
                                              "test-payment"))
    assert "недостаточно средств" in events[0]
    assert '"done": true' in events[-1]


def test_tool_calls_are_honoured_even_without_finish_reason(monkeypatch):
    replies = [
        {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_meta", "arguments": "{}"},
                }],
            },
            "finish_reason": "stop",
        },
        {
            "message": {"role": "assistant", "content": "Готово"},
            "finish_reason": "stop",
        },
    ]
    executed = []

    async def fake_chat_raw(*_args, **_kwargs):
        return replies.pop(0)

    monkeypatch.setattr(llm, "chat_raw", fake_chat_raw)
    monkeypatch.setattr(
        tools, "execute_tool",
        lambda name, args, point_scope=None: executed.append(
            (name, args, point_scope)) or '{"ok":true}',
    )

    events = _collect(chat.run_with_tools(
        [{"role": "user", "content": "test"}],
        SimpleNamespace(), point_scope="Точка А",
    ))

    assert executed == [("get_meta", {}, "Точка А")]
    assert {"tool": "get_meta"} in events
    assert events[-1] == {"delta": "Готово"}


def test_responses_tool_loop_replays_provider_output(monkeypatch):
    output = [
        {"type": "reasoning", "encrypted_content": "opaque"},
        {"type": "function_call", "call_id": "call-1",
         "name": "get_meta", "arguments": "{}"},
    ]
    seen = []

    async def fake_chat_raw(messages, *_args, **_kwargs):
        seen.append(list(messages))
        if len(seen) == 1:
            return {"message": {"role": "assistant", "tool_calls": [{
                "id": "call-1", "function": {"name": "get_meta", "arguments": "{}"},
            }]}, "_response_output": output}
        return {"message": {"role": "assistant", "content": "Готово"}}

    monkeypatch.setattr(llm, "chat_raw", fake_chat_raw)
    monkeypatch.setattr(tools, "execute_tool", lambda *_args, **_kwargs: '{"ok":true}')

    events = _collect(chat.run_with_tools(
        [{"role": "user", "content": "test"}], SimpleNamespace(),
    ))

    assert events == [{"tool": "get_meta"}, {"delta": "Готово"}]
    assert seen[1][1] == {"_response_output": output}
    assert seen[1][2] == {"role": "tool", "tool_call_id": "call-1",
                          "content": '{"ok":true}'}


def test_tool_call_limit_never_leaves_orphan_call_ids(monkeypatch):
    raw_calls = [{
        "id": f"call-{i}",
        "type": "function",
        "function": {"name": "get_meta", "arguments": "{}"},
    } for i in range(chat.MAX_TOOL_CALLS + 1)]
    captured = {}

    async def fake_chat_raw(*_args, **_kwargs):
        return {
            "message": {"role": "assistant", "content": None,
                        "tool_calls": raw_calls},
            "finish_reason": "tool_calls",
        }

    async def fake_stream(messages, *_args, **_kwargs):
        captured["messages"] = messages
        yield "OK"

    monkeypatch.setattr(llm, "chat_raw", fake_chat_raw)
    monkeypatch.setattr(llm, "stream_chat", fake_stream)
    monkeypatch.setattr(tools, "execute_tool", lambda *_args, **_kwargs: '{"ok":true}')

    events = _collect(chat.run_with_tools(
        [{"role": "user", "content": "test"}], SimpleNamespace(),
    ))

    assistant = [m for m in captured["messages"] if m["role"] == "assistant"][-1]
    tool_results = [m for m in captured["messages"] if m["role"] == "tool"]
    sent_ids = {tc["id"] for tc in assistant["tool_calls"]}
    result_ids = {m["tool_call_id"] for m in tool_results}
    assert len(sent_ids) == chat.MAX_TOOL_CALLS
    assert sent_ids == result_ids
    assert events[-1] == {"delta": "OK"}


def test_duplicate_provider_tool_call_ids_are_normalized(monkeypatch):
    replies = [{
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "duplicate",
                "type": "function",
                "function": {"name": "get_meta", "arguments": "{}"},
            }, {
                "id": "duplicate",
                "type": "function",
                "function": {"name": "get_meta", "arguments": "{}"},
            }],
        },
        "finish_reason": "tool_calls",
    }, {
        "message": {"role": "assistant", "content": "OK"},
        "finish_reason": "stop",
    }]
    captured = {}

    async def fake_chat_raw(messages, *_args, **_kwargs):
        if len(replies) == 1:
            captured["messages"] = messages
        return replies.pop(0)

    monkeypatch.setattr(llm, "chat_raw", fake_chat_raw)
    monkeypatch.setattr(tools, "execute_tool", lambda *_args, **_kwargs: '{"ok":true}')
    _collect(chat.run_with_tools(
        [{"role": "user", "content": "test"}], SimpleNamespace(),
    ))

    assistant = [m for m in captured["messages"] if m["role"] == "assistant"][-1]
    ids = [tc["id"] for tc in assistant["tool_calls"]]
    assert len(ids) == len(set(ids)) == 2


def test_single_tool_call_respects_wall_timeout(monkeypatch):
    async def fake_chat_raw(*_args, **_kwargs):
        return {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "slow-call",
                    "type": "function",
                    "function": {"name": "get_meta", "arguments": "{}"},
                }],
            },
            "finish_reason": "tool_calls",
        }

    async def fake_stream(messages, *_args, **_kwargs):
        tool_message = [m for m in messages if m["role"] == "tool"][-1]
        assert "лимит времени" in tool_message["content"]
        yield "timeout handled"

    async def slow_to_thread(*_args, **_kwargs):
        await asyncio.sleep(0.2)

    monkeypatch.setattr(chat, "TOOL_WALL_TIMEOUT_S", 0.01)
    monkeypatch.setattr(chat.asyncio, "to_thread", slow_to_thread)
    monkeypatch.setattr(llm, "chat_raw", fake_chat_raw)
    monkeypatch.setattr(llm, "stream_chat", fake_stream)

    events = _collect(chat.run_with_tools(
        [{"role": "user", "content": "test"}], SimpleNamespace(),
    ))
    assert events[-1] == {"delta": "timeout handled"}


def test_get_meta_includes_local_calendar(monkeypatch):
    fixed = datetime(2026, 8, 28, 7, 5, tzinfo=timezone(timedelta(hours=10)))
    monkeypatch.setattr(tools, "_local_now", lambda: fixed)

    def fake_fn(module, name):
        if (module, name) == ("web.data", "freshness"):
            return lambda: {"freshness": []}
        if (module, name) == ("web.data", "forecast_dates"):
            return lambda: ["2026-08-29", "2026-08-30", "2026-08-31"]
        return None

    monkeypatch.setattr(tools, "_fn", fake_fn)
    result = json.loads(tools.execute_tool("get_meta", {}))

    assert result["current_date_local"] == "2026-08-28"
    assert result["current_datetime_local"] == "2026-08-28T07:05+10:00"
    assert result["forecast_dates_recent"] == [
        "2026-08-29", "2026-08-30", "2026-08-31",
    ]


def test_manager_tool_fails_closed_on_foreign_rows(monkeypatch):
    def fake_sales(granularity=None, point=None, preset=None):
        return {
            "point": point,
            "rows": [
                {"point": "Точка А", "sold": 10},
                {"point": "Точка Б", "sold": 20},
            ],
        }

    monkeypatch.setattr(
        tools, "_fn",
        lambda module, name: fake_sales
        if (module, name) == ("web.analytics", "sales_series") else None,
    )
    result = json.loads(tools.execute_tool(
        "get_sales", {"point": "Точка Б"}, point_scope="Точка А",
    ))
    assert "error" in result
    assert "безопасно ограничить" in result["error"]


def test_manager_tool_fails_closed_if_downstream_loses_point_parameter(monkeypatch):
    def unsafe_sales(granularity=None, preset=None):
        return {"total": 999}

    monkeypatch.setattr(
        tools, "_fn",
        lambda module, name: unsafe_sales
        if (module, name) == ("web.analytics", "sales_series") else None,
    )
    result = json.loads(tools.execute_tool(
        "get_sales", {}, point_scope="Точка А",
    ))
    assert result == tools.UNAVAILABLE


def test_manager_tool_rejects_kwargs_only_downstream(monkeypatch):
    captured = {}

    def unsafe_sales(**kwargs):
        captured.update(kwargs)
        return {"total": 999}

    monkeypatch.setattr(
        tools, "_fn",
        lambda module, name: unsafe_sales
        if (module, name) == ("web.analytics", "sales_series") else None,
    )
    result = json.loads(tools.execute_tool(
        "get_sales", {}, point_scope="Точка А",
    ))

    assert result == tools.UNAVAILABLE
    assert captured == {}


def test_manager_order_rejects_foreign_point_in_metadata(monkeypatch):
    def unsafe_order(target_date=None, point=None):
        return {
            "rows": [],
            "meta": {"points": [point, "Точка Б"], "network_total": 999},
        }

    def fake_fn(module, name):
        if (module, name) == ("web.order", "get_order"):
            return unsafe_order
        if (module, name) == ("web.data", "forecast_dates"):
            return lambda: ["2026-08-28"]
        return None

    monkeypatch.setattr(tools, "_fn", fake_fn)
    result = json.loads(tools.execute_tool(
        "get_order", {}, point_scope="Точка А",
    ))

    assert result == tools.SCOPE_UNAVAILABLE


@pytest.mark.parametrize(
    ("tool_name", "module", "function_name"),
    [
        ("get_sales", "web.analytics", "sales_series"),
        ("get_accuracy", "web.accuracy", "summary"),
        ("get_writeoffs", "web.analytics", "writeoffs"),
        ("get_shelf_top", "web.analytics", "shelf_top"),
        ("get_comparison", "web.comparison", "summary"),
    ],
)
def test_manager_aggregate_tools_require_exact_scope_marker(
        monkeypatch, tool_name, module, function_name):
    def fake(point=None, **_kwargs):
        return {"point": "", "network_total": 999, "rows": []}

    monkeypatch.setattr(
        tools, "_fn",
        lambda got_module, got_name: fake
        if (got_module, got_name) == (module, function_name) else None,
    )
    result = json.loads(tools.execute_tool(
        tool_name, {}, point_scope="Точка А",
    ))

    assert result == tools.SCOPE_UNAVAILABLE


@pytest.mark.parametrize(
    ("tool_name", "module", "function_name"),
    [
        ("get_sales", "web.analytics", "sales_series"),
        ("get_accuracy", "web.accuracy", "summary"),
        ("get_writeoffs", "web.analytics", "writeoffs"),
        ("get_shelf_top", "web.analytics", "shelf_top"),
        ("get_comparison", "web.comparison", "summary"),
    ],
)
def test_manager_aggregate_tools_drop_unknown_network_fields(
        monkeypatch, tool_name, module, function_name):
    def fake(point=None, **_kwargs):
        return {
            "point": point,
            "network_total": 999,
            "rows": [],
            "by_point": [],
            "deficit": [],
            "writeoff": [],
            "daily": [],
        }

    monkeypatch.setattr(
        tools, "_fn",
        lambda got_module, got_name: fake
        if (got_module, got_name) == (module, function_name) else None,
    )
    result = json.loads(tools.execute_tool(
        tool_name, {}, point_scope="Точка А",
    ))

    assert result.get("point") == "Точка А"
    assert "network_total" not in result


def test_order_tool_returns_allowlisted_recomputed_metadata(monkeypatch):
    def safe_order(target_date=None, point=None):
        return {
            "rows": [{
                "point": point, "name": "Тест", "group": "Группа",
                "qty": 3, "qty_user": 4,
            }],
            "meta": {
                "engine": "ml", "version": "v1", "max_sale_date": "2026-08-27",
                "excluded_in_window": ["2026-08-20", "not-a-date"],
                "has_baseline": True, "network_total": 999,
            },
        }

    def fake_fn(module, name):
        if (module, name) == ("web.order", "get_order"):
            return safe_order
        if (module, name) == ("web.data", "forecast_dates"):
            return lambda: ["2026-08-28"]
        return None

    monkeypatch.setattr(tools, "_fn", fake_fn)
    result = json.loads(tools.execute_tool(
        "get_order", {}, point_scope="Точка А",
    ))

    assert result["meta"] == {
        "target_date": "2026-08-28",
        "engine": "ml",
        "version": "v1",
        "max_sale_date": "2026-08-27",
        "excluded_in_window": ["2026-08-20"],
        "n_rows": 1,
        "has_baseline": True,
        "edited_count": 1,
    }
    assert "network_total" not in result["meta"]


def test_blocking_tool_gate_returns_busy_without_second_execution(monkeypatch):
    monkeypatch.setattr(chat, "_TOOL_EXECUTION_GATE", threading.BoundedSemaphore(1))
    calls = []
    fake_tools = SimpleNamespace(
        execute_tool=lambda *args, **kwargs: calls.append((args, kwargs)) or "{}",
    )

    assert chat._TOOL_EXECUTION_GATE.acquire(blocking=False)
    try:
        result = json.loads(chat._execute_tool_guarded(
            fake_tools, "get_meta", {}, "Точка А",
        ))
    finally:
        chat._TOOL_EXECUTION_GATE.release()

    assert "занят" in result["error"]
    assert calls == []


def test_chat_input_and_rate_key_are_bounded():
    with pytest.raises(ValidationError):
        routes_chat.ChatMessage(role="system", content="not allowed")
    with pytest.raises(ValidationError):
        routes_chat.ChatMessage(role="user", content="x" * (8 * 1024 + 1))
    with pytest.raises(ValidationError):
        routes_chat.ChatBody(messages=[
            routes_chat.ChatMessage(role="user", content="x")
            for _ in range(chat.MAX_HISTORY_MESSAGES + 1)
        ])

    assert routes_chat._rate_limit_key("Manager-A") == "user:manager-a"
    assert routes_chat._rate_limit_key(" Manager-A ") == "user:manager-a"
