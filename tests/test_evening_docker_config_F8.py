from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_F8_dockerfile_evening_exists_and_has_requirements():
    p = ROOT / "Dockerfile.evening"
    assert p.exists()
    text = p.read_text()
    assert "USER appuser" in text or "USER 10001" in text
    assert "HEALTHCHECK" in text
    assert "/api/evening/events" in text
    assert "EXPOSE 18447" in text


def test_F8_compose_evening_port_and_volume():
    p = ROOT / "compose.evening.yaml"
    assert p.exists()
    text = p.read_text()
    assert "127.0.0.1:18447:18447" in text
    assert "evening_state" in text
    assert "EVENING_DB_PATH" in text


def test_F8_dockerignore_blocks_sensitive_and_host_artifacts():
    p = ROOT / ".dockerignore"
    assert p.exists()
    text = p.read_text()
    for token in [".git", ".venv", ".opencode", ".demo", ".env", "2gis"]:
        assert token in text


def test_F8_docker_docs_exist():
    assert (ROOT / "DOCKER-EVENING.md").exists()
