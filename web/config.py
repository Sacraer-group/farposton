"""Загрузка настроек веб-сервиса прогноза из .env (лежит в корне проекта — prognoz/.env).

LLM-помощник (вкладка «Помощник») настраивается целиком через .env: LLM_BASE_URL /
LLM_API_KEY / LLM_MODEL / LLM_PROXY_URL. Пустой ключ = помощник выключен,
остальной сервис работает.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

# web/config.py → web/ → prognoz/  (корень проекта; здесь .env, logs/, analysis/)
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Снимок БД для веба готовит дневной прогон. Веб НИКОГДА не открывает рабочую
# prognoz.duckdb — её держит прогон, а duckdb не любит двух писателей/читателей.
_DB_SNAPSHOT_DEFAULT = "analysis/db/prognoz_web.duckdb"


@dataclass(frozen=True)
class WebSettings:
    users_json: str             # named accounts configured outside source
    host: str
    port: int
    password_hash: str          # формат scrypt: "scrypt$salt_hex$dk_hex" (см. auth.py)
    secret_key: str             # подпись cookie-сессии
    session_ttl_seconds: int
    llm_base_url: str           # пусто = LLM выключен
    llm_api_key: str            # пусто = LLM выключен
    llm_model: str
    llm_timeout_s: int
    llm_allow_client_data: bool # отдельное явное разрешение внешнему LLM
    llm_proxy_url: str          # отдельный прокси только для LLM; пусто = прямой выход
    db_snapshot: Path           # снимок DuckDB, который читает веб (read-only)
    tls_cert: Path | None
    tls_key: Path | None
    allowed_ips: frozenset[str]  # пусто = пускаем всех (защита на уровне файрвола/пароля)
    trusted_proxy_ips: frozenset[str]  # только эти узлы могут передавать X-Forwarded-For
    allowed_hosts: frozenset[str]
    session_secure_cookie: bool
    public_https: bool
    deployment_profile: str
    ratelimit_max_fails: int
    ratelimit_window_seconds: int
    log_level: str
    log_file: Path


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"В .env отсутствует обязательная переменная {name}. "
            f"Скопируйте .env.example в prognoz/.env и заполните значения "
            f"(пароль: python -m web.auth --hash)."
        )
    return value


def _parse_ips(raw: str) -> frozenset[str]:
    ips: set[str] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            ips.add(chunk)
    return frozenset(ips)


def _parse_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} должен быть 0/1, true/false, yes/no или on/off")


# Переменные подключения к OData 1С, без которых писатель заявок не поднимется.
# Проверяется только наличие — значения веб не читает и не выводит.
ODATA_ENV_VARS = ("APP_ODATA_URL", "APP_ODATA_USER", "APP_ODATA_PASSWORD")


def writer_path() -> Path:
    """Где лежит odata/writer.py: PROGNOZ_ODATA_DIR → <корень>/../odata (наша
    раскладка forecast/odata) → <корень>/odata (клиентская:
    C:\\Services\\forecast\\odata). Нет нигде — путь последнего кандидата
    (вызывающий сам решает, ошибка это или нет)."""
    env = os.getenv("PROGNOZ_ODATA_DIR", "").strip()
    candidates = ([Path(env)] if env else []) + [ROOT.parent / "odata", ROOT / "odata"]
    for c in candidates:
        if (c / "writer.py").exists():
            return c / "writer.py"
    return candidates[-1] / "writer.py"


def _one_c_write_errors() -> list[str]:
    """Профиль client: запись в 1С либо выключена (0), либо включена (1) при
    полном комплекте — переменные APP_ODATA_* заданы и файл писателя есть.
    Любое другое значение — ошибка конфигурации, как и раньше."""
    raw = os.getenv("PROGNOZ_1C_WRITE", "").strip()
    if raw == "0":
        return []
    if raw != "1":
        return ["PROGNOZ_1C_WRITE=0 (или 1 при полной настройке OData)"]
    errors = [f"{name} должен быть задан при PROGNOZ_1C_WRITE=1"
              for name in ODATA_ENV_VARS if not os.getenv(name, "").strip()]
    path = writer_path()
    if not path.is_file():
        errors.append(f"при PROGNOZ_1C_WRITE=1 нужен файл писателя {path}")
    return errors


def _validate_client_profile(settings: WebSettings) -> WebSettings:
    if settings.deployment_profile != "client":
        return settings
    errors = []
    errors.extend(_one_c_write_errors())
    ingest = os.getenv("PROGNOZ_1C_INGEST", "").strip()
    if ingest == "1":
        # 17.09: добор из 1С на сервере клиента — модули odata\ ставятся рядом с writer.py
        if not (writer_path().parent / "client.py").is_file():
            errors.append("при PROGNOZ_1C_INGEST=1 нужен odata/client.py рядом с писателем")
    elif ingest != "0":
        errors.append("PROGNOZ_1C_INGEST=0 или 1")
    if settings.llm_api_key or settings.llm_allow_client_data:
        # Ключ есть, разрешение = 0 — это штатный ВЫКЛЮЧАТЕЛЬ помощника, а не
        # ошибка конфигурации: сервис должен подняться с отключённым чатом.
        # До 07.09 такая пара валила весь веб при рестарте (ревью, находка 21).
        if settings.llm_allow_client_data and not settings.llm_api_key:
            errors.append("LLM_API_KEY должен быть задан при включённом LLM_ALLOW_CLIENT_DATA")
        if not settings.llm_base_url:
            errors.append("LLM_BASE_URL должен быть задан при включённом LLM")
        else:
            parsed = urlsplit(settings.llm_base_url)
            # http допустим только для loopback: помощник у клиента ходит на наш релей через
            # обратный SSH-туннель (127.0.0.1:8090) — снаружи OpenRouter отвечает 403 (08.09).
            loopback = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
            scheme = parsed.scheme.lower()
            if not parsed.netloc or not (scheme == "https" or (scheme == "http" and loopback)):
                errors.append("LLM_BASE_URL должен быть HTTPS-адресом (http — только для 127.0.0.1/localhost, туннель)")
        if not settings.llm_model:
            errors.append("LLM_MODEL должен быть задан при включённом LLM")
    if settings.llm_proxy_url:
        if settings.llm_proxy_url != "http://127.0.0.1:40000":
            errors.append("LLM_PROXY_URL на сервере клиента должен быть http://127.0.0.1:40000")
    if not settings.trusted_proxy_ips:
        errors.append("TRUSTED_PROXY_IPS должен содержать адрес nginx")
    else:
        invalid_proxies = []
        for value in settings.trusted_proxy_ips:
            try:
                ip_network(value, strict=False)
            except ValueError:
                invalid_proxies.append(value)
        if invalid_proxies:
            errors.append("TRUSTED_PROXY_IPS содержит не IP/CIDR")
    if not settings.session_secure_cookie:
        errors.append("SESSION_SECURE_COOKIE=1")
    if not settings.public_https:
        errors.append("PUBLIC_HTTPS=1")
    if not settings.allowed_hosts:
        errors.append("ALLOWED_HOSTS должен содержать домен и внутренний адрес")
    if errors:
        raise RuntimeError("Небезопасная клиентская конфигурация: " + "; ".join(errors))
    return settings


def _opt_path(env_name: str) -> Path | None:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / raw).resolve()


def db_snapshot_from_env() -> Path:
    """Путь к снимку БД. Отдельная функция: её зовёт и load_settings, и web.data
    (слой данных должен импортироваться без обязательных переменных .env)."""
    raw = os.getenv("WEB_DB_SNAPSHOT", "").strip() or _DB_SNAPSHOT_DEFAULT
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / raw).resolve()


def load_settings() -> WebSettings:
    settings = WebSettings(
        users_json=_require('WEB_USERS_JSON'),
        host=os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=int(os.getenv("WEB_PORT", "8445")),
        password_hash=os.getenv("WEB_PASSWORD_HASH", "").strip(),
        secret_key=_require("WEB_SECRET_KEY"),
        session_ttl_seconds=int(os.getenv("SESSION_TTL_HOURS", "12")) * 3600,
        llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_model=os.getenv("LLM_MODEL", "").strip(),
        llm_timeout_s=int(os.getenv("LLM_TIMEOUT_S", "60")),
        llm_allow_client_data=_parse_bool("LLM_ALLOW_CLIENT_DATA", False),
        llm_proxy_url=os.getenv("LLM_PROXY_URL", "").strip(),
        db_snapshot=db_snapshot_from_env(),
        tls_cert=_opt_path("TLS_CERT"),
        tls_key=_opt_path("TLS_KEY"),
        allowed_ips=_parse_ips(os.getenv("ALLOWED_IPS", "")),
        trusted_proxy_ips=_parse_ips(os.getenv("TRUSTED_PROXY_IPS", "")),
        allowed_hosts=_parse_ips(os.getenv("ALLOWED_HOSTS", "")),
        session_secure_cookie=_parse_bool("SESSION_SECURE_COOKIE", False),
        public_https=_parse_bool("PUBLIC_HTTPS", False),
        deployment_profile=os.getenv("DEPLOYMENT_PROFILE", "local").strip().lower() or "local",
        ratelimit_max_fails=int(os.getenv("RATELIMIT_MAX_FAILS", "5")),
        ratelimit_window_seconds=int(os.getenv("RATELIMIT_WINDOW_MINUTES", "15")) * 60,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=(ROOT / os.getenv("LOG_FILE", "logs/web.log")).resolve(),
    )
    if settings.deployment_profile not in {"local", "client"}:
        raise RuntimeError("DEPLOYMENT_PROFILE должен быть local или client")
    return _validate_client_profile(settings)
