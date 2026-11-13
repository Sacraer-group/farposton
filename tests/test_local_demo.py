from datetime import date
import hashlib
import tarfile

import pytest

from web.access import demo_date
from web.routes_order import _business_today
from demo import local


def test_fixture_clock_is_local_no_auth_only(monkeypatch):
    monkeypatch.setenv("WEB_DEMO_DATE", "2026-09-29")
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")
    assert demo_date() == _business_today() == date(2026, 9, 29)
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "client")
    assert demo_date() is None
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "0")
    assert demo_date() is None


def test_fixture_clock_requires_valid_date(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_PROFILE", "local")
    monkeypatch.setenv("WEB_DEMO_NO_AUTH", "1")
    monkeypatch.setenv("WEB_DEMO_DATE", "invalid")
    with pytest.raises(ValueError):
        demo_date()


def test_extract_verifies_digest_and_never_overwrites(tmp_path, monkeypatch):
    archive_path = tmp_path / "fixture.tar.gz"
    source = tmp_path / "source"
    source.mkdir()
    (source / "fact.txt").write_text("synthetic")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source, arcname="forecast")
    target = tmp_path / "demo" / "forecast"
    monkeypatch.setattr(local, "ROOT", target)
    monkeypatch.setattr(local, "ARCHIVE", archive_path)
    monkeypatch.setattr(local, "ARCHIVE_SHA256", "invalid")
    with pytest.raises(RuntimeError, match="checksum"):
        local.prepare()
    assert not target.exists()
    monkeypatch.setattr(local, "ARCHIVE_SHA256", hashlib.sha256(archive_path.read_bytes()).hexdigest())
    local.prepare()
    assert (target / "fact.txt").read_text() == "synthetic"
    with pytest.raises(FileExistsError):
        local.prepare()


def test_llm_demo_is_opt_in_and_uses_private_shared_key(tmp_path, monkeypatch):
    project = tmp_path / "forecast"
    data = project / ".demo" / "forecast"
    data.mkdir(parents=True)
    (data / "prognoz_web.duckdb").touch()
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "opencode.key").write_text("test-key")
    monkeypatch.setattr(local, "PROJECT", project)
    monkeypatch.setattr(local, "ROOT", data)
    monkeypatch.setenv("LLM_API_KEY", "production-key")
    assert local.environment()["LLM_API_KEY"] == ""
    env = local.environment("glm-5.3")
    assert env["LLM_API_KEY"] == "test-key"
    assert env["LLM_BASE_URL"] == "https://opencode.ai/zen/go/v1"
    assert env["WEB_HOST"] == "127.0.0.1" and env["PROGNOZ_1C_WRITE"] == "0"
    with pytest.raises(ValueError):
        local.environment("unapproved")
