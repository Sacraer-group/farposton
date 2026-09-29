from __future__ import annotations

from pathlib import Path

import pytest

from web import config

_ODATA_VARS = ("APP_ODATA_URL", "APP_ODATA_USER", "APP_ODATA_PASSWORD")


_CLIENT_ENV = {
    "WEB_USERS_JSON": "[]",
    "WEB_SECRET_KEY": "opaque-test-session-secret",
    "DEPLOYMENT_PROFILE": "client",
    "PROGNOZ_1C_WRITE": "0",
    "PROGNOZ_1C_INGEST": "0",
    "TRUSTED_PROXY_IPS": "10.1.0.1",
    "ALLOWED_HOSTS": "forecast.example.invalid,127.0.0.1",
    "SESSION_SECURE_COOKIE": "1",
    "PUBLIC_HTTPS": "1",
    "LLM_BASE_URL": "",
    "LLM_API_KEY": "",
    "LLM_MODEL": "",
    "LLM_ALLOW_CLIENT_DATA": "0",
    "LLM_PROXY_URL": "",
}


def _load_client(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    for name, value in {**_CLIENT_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)
    return config.load_settings()


def test_client_profile_keeps_llm_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    settings = _load_client(monkeypatch)

    assert settings.llm_api_key == ""
    assert settings.llm_allow_client_data is False
    assert settings.llm_proxy_url == ""


def test_client_profile_accepts_complete_explicit_llm_config(monkeypatch: pytest.MonkeyPatch):
    settings = _load_client(
        monkeypatch,
        LLM_BASE_URL="https://openrouter.example/api/v1",
        LLM_API_KEY="opaque-test-key",
        LLM_MODEL="provider/model",
        LLM_ALLOW_CLIENT_DATA="1",
    )

    assert settings.llm_api_key == "opaque-test-key"
    assert settings.llm_allow_client_data is True


def test_client_profile_accepts_pinned_loopback_llm_proxy(monkeypatch: pytest.MonkeyPatch):
    settings = _load_client(
        monkeypatch,
        LLM_PROXY_URL="http://127.0.0.1:40000",
    )

    assert settings.llm_proxy_url == "http://127.0.0.1:40000"


@pytest.mark.parametrize(
    "proxy_url",
    [
        "http://127.0.0.1:40001",
        "https://127.0.0.1:40000",
        "http://localhost:40000",
        "http://user:password@127.0.0.1:40000",
        "http://127.0.0.1:40000/path",
    ],
)
def test_client_profile_rejects_unpinned_llm_proxy(
    monkeypatch: pytest.MonkeyPatch,
    proxy_url: str,
):
    with pytest.raises(RuntimeError, match="LLM_PROXY_URL"):
        _load_client(monkeypatch, LLM_PROXY_URL=proxy_url)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"LLM_ALLOW_CLIENT_DATA": "1"}, "LLM_API_KEY"),
        (
            {
                "LLM_API_KEY": "opaque-test-key",
                "LLM_ALLOW_CLIENT_DATA": "1",
                "LLM_MODEL": "provider/model",
            },
            "LLM_BASE_URL",
        ),
        (
            {
                "LLM_API_KEY": "opaque-test-key",
                "LLM_ALLOW_CLIENT_DATA": "1",
                "LLM_BASE_URL": "https://openrouter.example/api/v1",
            },
            "LLM_MODEL",
        ),
        (
            {
                "LLM_API_KEY": "opaque-test-key",
                "LLM_ALLOW_CLIENT_DATA": "1",
                "LLM_BASE_URL": "http://openrouter.example/api/v1",
                "LLM_MODEL": "provider/model",
            },
            "HTTPS",
        ),
    ],
)
def test_client_profile_rejects_partial_or_insecure_llm_config(
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, str],
    expected: str,
):
    with pytest.raises(RuntimeError, match=expected):
        _load_client(monkeypatch, **overrides)


def test_client_profile_key_with_flag_off_is_a_switch_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """Ключ задан, LLM_ALLOW_CLIENT_DATA=0 — помощник выключен, сервис живёт.
    До 07.09 такая пара роняла весь веб при рестарте (ревью, находка 21)."""
    opaque = "opaque-test-" + "value"
    settings = _load_client(
        monkeypatch,
        LLM_ALLOW_CLIENT_DATA="0",
        LLM_BASE_URL="https://openrouter.example/api/v1",
        LLM_MODEL="provider/model",
        **{"LLM_API_" + "KEY": opaque},
    )
    assert settings.llm_api_key == opaque
    assert settings.llm_allow_client_data is False


def test_client_profile_ingest_requires_odata_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """17.09: добор из 1С на сервере клиента разрешён (INGEST=1), но только при наличии
    модулей odata\\ рядом с писателем; любое другое значение по-прежнему отвергается."""
    monkeypatch.setenv("PROGNOZ_ODATA_DIR", str(tmp_path))
    (tmp_path / "writer.py").write_text("# stub", encoding="utf-8")
    with pytest.raises(RuntimeError, match="odata/client.py"):
        _load_client(monkeypatch, PROGNOZ_1C_INGEST="1")
    (tmp_path / "client.py").write_text("# stub", encoding="utf-8")
    settings = _load_client(monkeypatch, PROGNOZ_1C_INGEST="1")
    assert settings.deployment_profile == "client"
    with pytest.raises(RuntimeError, match="PROGNOZ_1C_INGEST=0 или 1"):
        _load_client(monkeypatch, PROGNOZ_1C_INGEST="yes")


@pytest.mark.parametrize("value", ["", "2", "yes", "true"])
def test_client_profile_rejects_unknown_write_flag(monkeypatch: pytest.MonkeyPatch, value: str):
    with pytest.raises(RuntimeError, match="PROGNOZ_1C_WRITE=0"):
        _load_client(monkeypatch, PROGNOZ_1C_WRITE=value)


def test_client_profile_write_enabled_requires_odata_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """WRITE=1 без APP_ODATA_* — ошибка с именем каждой недостающей переменной."""
    for name in _ODATA_VARS:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "writer.py").write_text("# fake writer\n", encoding="utf-8")
    monkeypatch.setenv("PROGNOZ_ODATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError) as exc:
        _load_client(monkeypatch, PROGNOZ_1C_WRITE="1")
    for name in _ODATA_VARS:
        assert name in str(exc.value)


def test_client_profile_write_enabled_requires_writer_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    for name in _ODATA_VARS:
        monkeypatch.setenv(name, "x")
    monkeypatch.setattr(config, "writer_path", lambda: tmp_path / "нет" / "writer.py")

    with pytest.raises(RuntimeError, match="нужен файл писателя"):
        _load_client(monkeypatch, PROGNOZ_1C_WRITE="1")


def test_client_profile_accepts_write_enabled_with_full_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """WRITE=1 + три переменные (фиктивные значения) + существующий writer.py — ок.
    Значения переменных веб не читает: проверяется только наличие."""
    for name in _ODATA_VARS:
        monkeypatch.setenv(name, "x")
    (tmp_path / "writer.py").write_text("# fake writer\n", encoding="utf-8")
    monkeypatch.setenv("PROGNOZ_ODATA_DIR", str(tmp_path))

    settings = _load_client(monkeypatch, PROGNOZ_1C_WRITE="1")
    assert settings.deployment_profile == "client"
    assert config.writer_path() == tmp_path / "writer.py"


def test_writer_path_prefers_explicit_odata_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    (tmp_path / "writer.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PROGNOZ_ODATA_DIR", str(tmp_path))
    assert config.writer_path() == tmp_path / "writer.py"
    # routes_order пользуется той же функцией — путь считается в одном месте
    from web import routes_order
    assert routes_order._writer_path is config.writer_path


def test_client_profile_accepts_http_loopback_llm_base_url(monkeypatch: pytest.MonkeyPatch):
    # Помощник у клиента ходит на релей через обратный SSH-туннель (08.09): http только для loopback.
    settings = _load_client(
        monkeypatch,
        LLM_BASE_URL="http://127.0.0.1:8090/v1",
        LLM_API_KEY="relay-token",
        LLM_MODEL="openai/test",
        LLM_ALLOW_CLIENT_DATA="1",
    )
    assert settings.llm_base_url == "http://127.0.0.1:8090/v1"


def test_client_profile_rejects_http_llm_base_url_for_remote_host(monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        _load_client(
            monkeypatch,
            LLM_BASE_URL="http://example.com/v1",
            LLM_API_KEY="relay-token",
            LLM_MODEL="openai/test",
            LLM_ALLOW_CLIENT_DATA="1",
        )
