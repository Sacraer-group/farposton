#!/usr/bin/python3
"""Deploy a validated image; keep production state volumes and the prior image."""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

COMPOSE = "/etc/farposton/compose.yaml"
ENV_FILE = "/home/tech/farposton/.env"
STATE = Path("/var/lib/farposton/deployment.json")
MARKER = Path("/home/tech/farposton/.deployed-commit")
VOLUME = "farposton_forecast-demo"
CONTAINER = "farposton-farposton-1"
PUBLIC = "https://farposton.renosaza.com"


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=900, **kwargs).stdout


def image_info(image):
    return json.loads(run(["docker", "image", "inspect", image]))[0]


def running_release():
    info = json.loads(run(["docker", "container", "inspect", CONTAINER]))[0]
    revision = (info["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
    if not revision and MARKER.exists():
        value = MARKER.read_text().strip()
        revision = value if re.fullmatch(r"[0-9a-f]{40}", value) else None
    return {"image": info["Image"], "revision": revision}


def compose(project, image, port, volume, *args):
    env = os.environ.copy()
    env.update(FARPOSTON_IMAGE=image, FARPOSTON_BIND_PORT=str(port), FARPOSTON_STATE_VOLUME=volume)
    return run(["docker", "compose", "--project-name", project, "--env-file", ENV_FILE,
                "-f", COMPOSE, *args], env=env)


def json_get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("Readiness HTTP status is not 200")
        return json.load(response)


def check_ready(base, evening=True):
    last = None
    for _ in range(30):
        try:
            # The packaged synthetic snapshot is dated, so ok:false is not process failure.
            if not isinstance(json_get(base + "/health"), dict):
                raise RuntimeError("Invalid health response")
            if not isinstance(json_get(base + "/api/meta"), dict):
                raise RuntimeError("Invalid metadata response")
            if evening:
                events = json_get(base + "/api/evening/events")
                if events.get("synthetic") is not False or not isinstance(events.get("items"), list):
                    raise RuntimeError("Real evening events are unavailable")
                for path in ("/places", "/evening"):
                    with urllib.request.urlopen(base + path, timeout=5) as response:
                        if response.status != 200:
                            raise RuntimeError("Page is unavailable")
            return
        except (OSError, ValueError, RuntimeError) as exc:
            last = exc
            time.sleep(1)
    raise RuntimeError("Application readiness failed") from last


def read_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"current": None, "previous": None}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(STATE)
    revision = (state.get("current") or {}).get("revision")
    if revision:
        temporary = MARKER.with_suffix(".tmp")
        temporary.write_text(revision + "\n")
        temporary.replace(MARKER)
    elif MARKER.exists():
        MARKER.unlink()


def verify_candidate(image):
    volume = "farposton-candidate-" + uuid.uuid4().hex
    run(["docker", "volume", "create", "--label", "farposton.owner=ci-candidate", volume])
    try:
        compose("farposton-candidate", image, 18450, volume, "up", "-d", "--no-build", "--force-recreate")
        check_ready("http://127.0.0.1:18450")
    finally:
        try:
            compose("farposton-candidate", image, 18450, volume, "down")
        finally:
            run(["docker", "volume", "rm", volume])


def activate(release, evening=True):
    compose("farposton", release["image"], 8445, VOLUME, "up", "-d", "--no-build", "--force-recreate")
    if running_release()["image"] != release["image"]:
        raise RuntimeError("Running image does not match the tested image")
    check_ready("http://127.0.0.1:8445", evening=evening)
    # Public proxy/TLS are part of a successful release, not just Docker startup.
    check_ready(PUBLIC, evening=False)


def deploy(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid deployment SHA")
    run(["docker", "load"], stdin=sys.stdin.buffer)
    info = image_info("farposton:release-" + sha)
    if (info["Config"].get("Labels") or {}).get("org.opencontainers.image.revision") != sha:
        raise ValueError("Image revision does not match the requested SHA")
    previous = running_release()
    old_state = read_state()
    current = {"image": info["Id"], "revision": sha}
    if previous["image"] == current["image"] and (old_state.get("current") or {}).get("image") == current["image"]:
        check_ready("http://127.0.0.1:8445")
        check_ready(PUBLIC, evening=False)
        print(json.dumps({"already_deployed": sha, "image": current["image"]}))
        return
    verify_candidate(current["image"])
    try:
        activate(current)
        save_state({"current": current, "previous": previous})
    except Exception:
        activate(previous, evening=False)
        save_state(old_state if old_state.get("current") else {"current": previous, "previous": None})
        raise
    print(json.dumps({"deployed": sha, "image": current["image"]}))


def rollback():
    state = read_state()
    previous = state.get("previous")
    if not previous or not re.fullmatch(r"sha256:[0-9a-f]{64}", previous.get("image", "")):
        raise ValueError("No previous image is recorded")
    image_info(previous["image"])
    current = running_release()
    try:
        activate(previous, evening=False)
        save_state({"current": previous, "previous": current})
    except Exception:
        activate(current, evening=False)
        save_state(state)
        raise
    print(json.dumps({"rolled_back": previous.get("revision"), "image": previous["image"]}))


def interrupted(signum, frame):
    raise RuntimeError("Deployment interrupted")


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if not (args in (["status"], ["rollback"]) or
            (len(args) == 2 and args[0] == "deploy" and re.fullmatch(r"[0-9a-f]{40}", args[1]))):
        raise ValueError("Usage: farposton-release deploy <SHA> | status | rollback")
    if os.geteuid() != 0:
        raise PermissionError("The deployment agent must run through its restricted sudo rule")
    os.umask(0o077)
    if args == ["status"]:
        print(json.dumps({**read_state(), "running": running_release()}, sort_keys=True))
        return
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    # A dropped SSH session must use the same rollback path as other failures.
    for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupted)
    with (STATE.parent / "deployment.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deploy(args[1]) if args[0] == "deploy" else rollback()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        sys.exit(1)
