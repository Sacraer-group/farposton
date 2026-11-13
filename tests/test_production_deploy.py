"""Deployment safety checks; no Docker, production data or credentials are used."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "deploy" / "production"


def load(name):
    spec = importlib.util.spec_from_file_location("production_" + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", ["", "bash", "deploy", "deploy " + "a" * 39,
                                       "deploy " + "a" * 40 + ";id", "status ignored", "rollback x"])
def test_gate_rejects_shell_and_invalid_arguments(command):
    with pytest.raises(ValueError):
        load("ssh_gate").arguments(command)


def test_gate_executes_only_fixed_sudo_agent(monkeypatch):
    gate = load("ssh_gate")
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "deploy " + "a" * 40)
    calls = []
    monkeypatch.setattr(gate.os, "execv", lambda path, args: calls.append((path, args)))
    gate.main()
    assert calls == [("/usr/bin/sudo", ["sudo", "-n", "/usr/local/sbin/farposton-release", "deploy", "a" * 40])]


@pytest.fixture
def agent(monkeypatch, tmp_path):
    module = load("release")
    monkeypatch.setattr(module, "STATE", tmp_path / "deployment.json")
    monkeypatch.setattr(module, "MARKER", tmp_path / "deployed-commit")
    monkeypatch.setattr(module, "run", lambda *args, **kwargs: "")
    monkeypatch.setattr(module, "image_info", lambda image: {
        "Id": "sha256:" + "b" * 64,
        "Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}},
    })
    monkeypatch.setattr(module, "running_release", lambda: {"image": "sha256:" + "c" * 64, "revision": "d" * 40})
    return module


def test_candidate_failure_never_activates_or_writes_state(agent, monkeypatch):
    activated = []
    monkeypatch.setattr(agent, "verify_candidate", lambda image: (_ for _ in ()).throw(RuntimeError("candidate failed")))
    monkeypatch.setattr(agent, "activate", lambda *args, **kwargs: activated.append(args))
    with pytest.raises(RuntimeError, match="candidate failed"):
        agent.deploy("a" * 40)
    assert activated == []
    assert not agent.STATE.exists()


def test_success_tests_candidate_before_promotion_and_records_previous(agent, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "verify_candidate", lambda image: calls.append(("candidate", image)))
    monkeypatch.setattr(agent, "activate", lambda release, **kwargs: calls.append(("activate", release["image"])))
    agent.deploy("a" * 40)
    assert calls == [("candidate", "sha256:" + "b" * 64), ("activate", "sha256:" + "b" * 64)]
    assert agent.read_state() == {
        "current": {"image": "sha256:" + "b" * 64, "revision": "a" * 40},
        "previous": {"image": "sha256:" + "c" * 64, "revision": "d" * 40},
    }
    assert agent.MARKER.read_text().strip() == "a" * 40


def test_live_failure_restores_previous_image_and_state(agent, monkeypatch):
    calls = []
    monkeypatch.setattr(agent, "verify_candidate", lambda image: None)
    def activate(release, evening=True):
        calls.append((release["image"], evening))
        if evening:
            raise RuntimeError("live failed")
    monkeypatch.setattr(agent, "activate", activate)
    with pytest.raises(RuntimeError, match="live failed"):
        agent.deploy("a" * 40)
    assert calls == [("sha256:" + "b" * 64, True), ("sha256:" + "c" * 64, False)]
    assert agent.read_state()["current"]["image"] == "sha256:" + "c" * 64
    assert agent.MARKER.read_text().strip() == "d" * 40


def test_mismatched_image_label_is_rejected_before_candidate(agent, monkeypatch):
    monkeypatch.setattr(agent, "image_info", lambda image: {"Id": "wrong", "Config": {"Labels": {}}})
    monkeypatch.setattr(agent, "verify_candidate", lambda image: pytest.fail("must not stage an unverified image"))
    with pytest.raises(ValueError, match="revision"):
        agent.deploy("a" * 40)


def test_activation_preserves_production_volume_and_verifies_image(agent, monkeypatch):
    calls = []
    image = "sha256:" + "c" * 64
    monkeypatch.setattr(agent, "compose", lambda *args: calls.append(args))
    monkeypatch.setattr(agent, "check_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "json_get", lambda url: {})
    agent.activate({"image": image, "revision": "a" * 40})
    assert calls == [("farposton", image, 8445, "farposton_forecast-demo", "up", "-d", "--no-build", "--force-recreate")]


def test_failed_candidate_removes_only_its_own_temporary_volume(agent, monkeypatch):
    commands, compositions = [], []
    monkeypatch.setattr(agent, "run", lambda args, **kwargs: commands.append(args) or "")
    monkeypatch.setattr(agent, "compose", lambda *args: compositions.append(args))
    monkeypatch.setattr(agent, "check_ready", lambda base: (_ for _ in ()).throw(RuntimeError("not ready")))
    with pytest.raises(RuntimeError, match="not ready"):
        agent.verify_candidate("sha256:" + "b" * 64)
    created = commands[0][-1]
    assert created.startswith("farposton-candidate-")
    assert commands[-1] == ["docker", "volume", "rm", created]
    assert all(call[0] == "farposton-candidate" and call[3] == created for call in compositions)
    assert compositions[-1][-1] == "down"
    assert agent.VOLUME not in created


def test_rollback_rejects_missing_previous(agent):
    with pytest.raises(ValueError, match="previous"):
        agent.rollback()


def test_rollback_uses_recorded_previous_image(agent, monkeypatch):
    calls = []
    previous = {"image": "sha256:" + "b" * 64, "revision": "a" * 40}
    agent.save_state({"current": {"image": "sha256:" + "c" * 64, "revision": "d" * 40}, "previous": previous})
    monkeypatch.setattr(agent, "activate", lambda release, **kwargs: calls.append((release, kwargs)))
    agent.rollback()
    assert calls == [(previous, {"evening": False})]
    assert agent.read_state()["current"] == previous


def test_readiness_allows_dated_snapshot_but_checks_real_events(agent, monkeypatch):
    monkeypatch.setattr(agent, "json_get", lambda url: {"synthetic": False, "items": []} if url.endswith("/events") else {"ok": False})
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    agent.check_ready("http://candidate")


@pytest.mark.parametrize("args", [[], ["bash"], ["deploy", "a" * 39], ["rollback", "anything"]])
def test_root_agent_rejects_untrusted_arguments_before_execution(agent, args):
    with pytest.raises(ValueError, match="Usage"):
        agent.main(args)


def test_ssh_disconnect_enters_exception_rollback_path(agent):
    with pytest.raises(RuntimeError, match="interrupted"):
        agent.interrupted(1, None)


def test_rollback_failure_restores_current_image(agent, monkeypatch):
    current = {"image": "sha256:" + "c" * 64, "revision": "d" * 40}
    previous = {"image": "sha256:" + "b" * 64, "revision": "a" * 40}
    original = {"current": current, "previous": previous}
    agent.save_state(original)
    calls = []
    def activate(release, **kwargs):
        calls.append(release)
        if release == previous:
            raise RuntimeError("bad rollback target")
    monkeypatch.setattr(agent, "activate", activate)
    with pytest.raises(RuntimeError, match="bad rollback target"):
        agent.rollback()
    assert calls == [previous, current]
    assert agent.read_state() == original


def test_legacy_rollback_clears_new_revision_marker(agent, monkeypatch):
    current = {"image": "sha256:" + "c" * 64, "revision": "d" * 40}
    legacy = {"image": "sha256:" + "b" * 64, "revision": None}
    agent.save_state({"current": current, "previous": legacy})
    assert agent.MARKER.exists()
    monkeypatch.setattr(agent, "activate", lambda *args, **kwargs: None)
    agent.rollback()
    assert agent.read_state()["current"] == legacy
    assert not agent.MARKER.exists()


def test_repeat_successful_deploy_preserves_previous_rollback_target(agent, monkeypatch):
    current = {"image": "sha256:" + "b" * 64, "revision": "a" * 40}
    previous = {"image": "sha256:" + "c" * 64, "revision": "d" * 40}
    state = {"current": current, "previous": previous}
    agent.save_state(state)
    monkeypatch.setattr(agent, "running_release", lambda: current)
    monkeypatch.setattr(agent, "check_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "verify_candidate", lambda *args: pytest.fail("already deployed"))
    monkeypatch.setattr(agent, "activate", lambda *args, **kwargs: pytest.fail("already deployed"))
    agent.deploy("a" * 40)
    assert agent.read_state() == state
