from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_compose_does_not_publish_postgres():
    text = (ROOT / "packaging" / "compose.yml").read_text()
    assert "5432:5432" not in text
    assert "127.0.0.1:8767:8767" in text
    assert "DEBUG: \"false\"" in text or "DEBUG: \"false\"" in text.replace("'", '"')


def test_dev_compose_loopback_only():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "127.0.0.1:5432:5432" in text
    assert "0.0.0.0:5432" not in text


def test_gitignore_secrets():
    gi = (ROOT / ".gitignore").read_text()
    assert ".env" in gi
    assert "secrets/" in gi


def test_settings_refuse_placeholder_key(monkeypatch):
    monkeypatch.setenv("TOPO_CONTENT_KEY", "CHANGE_ME_PLACEHOLDER_KEY_32B___")
    monkeypatch.setenv("SESSION_SECRET", "CHANGE_ME_SESSION_32")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:5432/z")
    from app.settings import get_settings

    get_settings.cache_clear()
    try:
        get_settings()
        raise AssertionError("should refuse placeholder content key")
    except RuntimeError:
        pass
    finally:
        get_settings.cache_clear()
