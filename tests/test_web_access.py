import json
import asyncio
from pathlib import Path

from web.auth import authenticate_user, hash_password, require_auth, session_is_valid
from web.access import Principal, principal_from_session, request_point, require_admin, require_owned_point
from fastapi import HTTPException, Request


ROOT = Path(__file__).resolve().parents[1]


def test_demo_no_auth_is_explicit_and_local_only(monkeypatch):
    request = Request({"type": "http", "session": {}})
    monkeypatch.delenv("WEB_DEMO_NO_AUTH", raising=False)
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")
    assert not session_is_valid(request)
    try:
        asyncio.run(require_auth(request))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("normal mode must require login")

    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    assert session_is_valid(request)
    asyncio.run(require_auth(request))
    assert principal_from_session(request).is_admin
    assert require_admin(request).is_admin

    monkeypatch.setenv("DEPLOYMENT_PROFILE", "client")
    assert not session_is_valid(request)


def test_manager_is_pinned_to_own_point():
    manager = Principal(username='manager', role='manager', point_key='point-a')
    assert request_point(manager) == 'point-a'


def test_manager_cannot_select_or_edit_foreign_point():
    manager = Principal(username='manager', role='manager', point_key='point-a')
    try:
        request_point(manager, 'point-b')
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError('foreign point must be forbidden')
    try:
        require_owned_point(manager, 'point-b')
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError('foreign edit must be forbidden')


def test_admin_can_access_any_point():
    admin = Principal(username='admin', role='admin')
    assert request_point(admin, 'point-b') == 'point-b'
    require_owned_point(admin, 'point-b')


def test_developer_account_authenticates_as_full_admin():
    password = "synthetic-test-password"
    users_json = json.dumps([{
        "username": "developer",
        "role": "admin",
        "password_hash": hash_password(password),
    }])

    developer = authenticate_user("developer", password, users_json)

    assert developer is not None
    assert developer.username == "developer"
    assert developer.is_admin
    assert developer.point_key is None
    assert request_point(developer, "point-b") == "point-b"
    require_owned_point(developer, "point-b")


def test_example_configuration_contains_two_admins_and_six_point_managers():
    users = json.loads((ROOT / "web" / "users.example.json").read_text(
        encoding="utf-8"))

    assert len(users) == 8
    assert {u["username"] for u in users if u["role"] == "admin"} == {
        "admin", "developer",
    }
    managers = [u for u in users if u["role"] == "manager"]
    assert len(managers) == 6                      # 18.09: + Точка 6
    assert len({u["point_key"] for u in managers}) == 6
