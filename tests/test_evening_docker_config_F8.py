from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_F8_dockerfile_evening_exists_and_has_requirements():
    p = ROOT / "Dockerfile.evening"
    assert p.exists()
    text = p.read_text()
    assert "USER appuser" in text or "USER 10001" in text
    assert "HEALTHCHECK" in text
    assert "/health" in text or "/api/evening/events" in text
    assert "EXPOSE 18447" in text


def test_F8_compose_evening_port_volume_and_real_mode_defaults():
    p = ROOT / "compose.evening.yaml"
    assert p.exists()
    text = p.read_text()
    assert "127.0.0.1:18447:18447" in text
    assert "evening_state" in text
    assert "EVENING_DB_PATH" in text
    assert "EVENING_DATA_MODE" in text
    assert "real" in text
    assert "EVENING_CATALOG_DIR" in text
    assert "EVENING_CATALOG_HOST_DIR" in text
    assert "create_host_path: false" in text
    assert "read_only: true" in text
    # single real service; demo is explicit docker run per docs
    assert "evening-demo" not in text
    assert "profiles:" not in text


def test_F8_dockerignore_blocks_sensitive_and_host_artifacts():
    p = ROOT / ".dockerignore"
    assert p.exists()
    text = p.read_text()
    for token in [".git", ".venv", ".opencode", ".demo", ".env"]:
        assert token in text
    # data/ must not be packed (real catalog mounted separately), but public events JSON is allowed
    assert "data/" in text
    assert "!evening/data/vladivostok_events.json" in text


def test_F8_docker_docs_exist_and_describe_modes():
    p = ROOT / "DOCKER-EVENING.md"
    assert p.exists()
    text = p.read_text().lower()
    assert "demo" in text
    assert "real" in text or "каталог" in text or "баз" in text
    assert "create_host_path" in text
    assert "docker run" in text or "docker-run" in text or "опционально" in text or "явн" in text
