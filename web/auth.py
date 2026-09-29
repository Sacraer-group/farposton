"""Авторизация: один общий пароль (хеш в .env) + cookie-сессия + анти-перебор.

- Пароль хранится ТОЛЬКО хешем (scrypt). В .env: WEB_PASSWORD_HASH="scrypt$<salt>$<dk>".
  Сгенерировать:  python -m web.auth --hash  (спросит пароль) или  python -m web.auth --hash "пароль".
- Сама cookie подписывается Starlette SessionMiddleware ключом WEB_SECRET_KEY (см. app.py).
  Здесь только кладём/читаем флаг в request.session и считаем неудачные попытки по IP.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from functools import lru_cache
from ipaddress import ip_address, ip_network

from fastapi import HTTPException, Request, status
from .access import Principal, demo_no_auth

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32


# --- пароль -------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, dk_hex = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=len(expected))
    return hmac.compare_digest(dk, expected)


def authenticate_user(username: str, password: str, users_json: str) -> Principal | None:
    try:
        users = json.loads(users_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(users, list):
        return None
    for item in users:
        if not isinstance(item, dict) or item.get('username') != username:
            continue
        role, point_key, stored = item.get('role'), item.get('point_key'), item.get('password_hash')
        if role not in {'admin', 'manager'} or not isinstance(stored, str):
            return None
        if role == 'manager' and (not isinstance(point_key, str) or not point_key):
            return None
        if verify_password(password, stored):
            return Principal(username=username, role=role, point_key=point_key if role == 'manager' else None)
    return None


# --- анти-перебор (in-memory, по IP) -----------------------------------------

class RateLimiter:
    """Скользящее окно неудачных попыток: по паре IP+логин и страховочно по IP.

    С появлением кабинетов (18.08) успех НЕ сбрасывает общий счётчик IP: иначе
    обладатель любого кабинета обнулял бы окно своим входом и перебирал чужой
    пароль без троттлинга. Сбрасывается только ведро конкретного логина; общий
    счётчик IP (потолок 3×max_fails) гаснет сам по окну.
    Сбрасывается при перезапуске процесса (для пилота ок)."""

    def __init__(self, max_fails: int, window_seconds: int) -> None:
        self.max_fails = max_fails
        self.window = window_seconds
        self._fails: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        fails = [t for t in self._fails.get(key, []) if now - t < self.window]
        if fails:
            self._fails[key] = fails
        else:
            self._fails.pop(key, None)
        return fails

    def retry_after(self, ip: str, user: str = "") -> int:
        """0 — можно пробовать; >0 — заблокировано, столько секунд ждать."""
        now = time.monotonic()
        per_user = self._prune(f"{ip}|{user}", now)
        per_ip = self._prune(ip, now)
        if len(per_user) < self.max_fails and len(per_ip) < self.max_fails * 3:
            return 0
        # после превышения — ждём, пока самая старая попытка выпадет из окна
        pool = per_user if len(per_user) >= self.max_fails else per_ip
        oldest = min(pool)
        return max(1, int(self.window - (now - oldest)))

    def record_fail(self, ip: str, user: str = "") -> None:
        now = time.monotonic()
        self._prune(f"{ip}|{user}", now)
        self._prune(ip, now)
        self._fails.setdefault(f"{ip}|{user}", []).append(now)
        self._fails.setdefault(ip, []).append(now)

    def reset(self, ip: str, user: str = "") -> None:
        """Успешный вход чистит только ведро этого логина, не общий счётчик IP."""
        self._fails.pop(f"{ip}|{user}", None)


# --- helpers ------------------------------------------------------------------

@lru_cache(maxsize=16)
def _proxy_networks(raw: str) -> tuple:
    networks = []
    for chunk in raw.replace(";", ",").split(","):
        value = chunk.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            # Ошибка конфигурации не должна превращать заголовок клиента в доверенный.
            continue
    return tuple(networks)


def _normal_ip(raw: str) -> str | None:
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _is_trusted(ip: str, networks: tuple) -> bool:
    try:
        addr = ip_address(ip)
    except ValueError:
        return False
    return any(addr in network for network in networks
               if addr.version == network.version)


def client_ip(request: Request, trusted_proxies: frozenset[str] | None = None) -> str:
    """Адрес сотрудника с безопасной поддержкой X-Forwarded-For.

    Заголовок читается только когда непосредственный TCP-узел входит в
    TRUSTED_PROXY_IPS. Цепочка обходится справа налево, поэтому подложенное
    клиентом левое значение не перекрывает адрес, который добавил nginx.
    """
    raw_peer = request.client.host if request.client else ""
    peer = _normal_ip(raw_peer)
    if peer is None:
        return raw_peer or "unknown"
    raw_trusted = (",".join(sorted(trusted_proxies)) if trusted_proxies is not None
                   else os.getenv("TRUSTED_PROXY_IPS", ""))
    networks = _proxy_networks(raw_trusted)
    if not networks or not _is_trusted(peer, networks):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer
    raw_hops = [chunk.strip() for chunk in forwarded.split(",")]
    if not raw_hops or len(raw_hops) > 20:
        return peer
    hops = [_normal_ip(chunk) for chunk in raw_hops]
    if any(hop is None for hop in hops):
        return peer
    chain = [*hops, peer]
    for hop in reversed(chain):
        if not _is_trusted(hop, networks):
            return hop
    return hops[0]


def session_is_valid(request: Request) -> bool:
    return demo_no_auth() or bool(request.session.get("auth_at"))


async def require_auth(request: Request) -> None:
    """Зависимость FastAPI: пускает только с валидной cookie-сессией.
    Срок жизни cookie контролирует SessionMiddleware (max_age)."""
    if not session_is_valid(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется вход",
            headers={"WWW-Authenticate": "Cookie"},
        )


def ip_allowed(request: Request, allowed_ips: frozenset[str]) -> bool:
    if not allowed_ips:
        return True
    return client_ip(request) in allowed_ips


# --- CLI: генерация хеша пароля и секретного ключа ----------------------------

def _main() -> None:
    import argparse
    import getpass
    import secrets

    parser = argparse.ArgumentParser(description="Генерация WEB_PASSWORD_HASH и WEB_SECRET_KEY для .env")
    parser.add_argument("--hash", nargs="?", const="", metavar="ПАРОЛЬ",
                        help="захешировать пароль (без аргумента — спросит скрыто)")
    parser.add_argument("--secret", action="store_true", help="сгенерировать WEB_SECRET_KEY")
    args = parser.parse_args()

    hashed = False
    if args.hash is not None:
        pwd = args.hash or getpass.getpass("Пароль для входа в веб-сервис: ")
        if not pwd:
            raise SystemExit("Пустой пароль недопустим.")
        print("WEB_PASSWORD_HASH=" + hash_password(pwd))
        hashed = True
    # секрет печатаем, если просили явно, ИЛИ заодно с хешем, ИЛИ когда не задано ничего
    if args.secret or hashed or args.hash is None:
        print("WEB_SECRET_KEY=" + secrets.token_hex(32))


if __name__ == "__main__":
    _main()
