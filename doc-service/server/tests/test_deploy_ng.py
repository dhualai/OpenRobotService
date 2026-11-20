from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
COMPOSE = (DEPLOY / "docker-compose.yml").read_text()
UPDATE = (DEPLOY / "update.sh").read_text()


def test_ng_compose_isolated_from_legacy_stack():
    assert not any(line.startswith("name:") for line in COMPOSE.splitlines())
    assert 'version: "2.4"' in COMPOSE
    assert "container_name: topo-ng-api" in COMPOSE
    assert "container_name: topo-ng-web" in COMPOSE
    assert "container_name: topo-ng-db" in COMPOSE
    assert "container_name: topo-portal" not in COMPOSE
    assert "/opt/topo-portal" not in COMPOSE
    assert "topo-app-data" not in COMPOSE
    assert "topo-ng-db:local" in COMPOSE
    assert "image: postgres:" not in COMPOSE
    assert "5432:5432" not in COMPOSE
    assert "8776" in COMPOSE
    assert "9122" in COMPOSE
    live = []
    for line in COMPOSE.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if "8766" in s or "9022" in s:
            live.append(s)
    assert live == []


def test_ng_update_excludes_secrets_and_has_no_postgres_client():
    assert "--exclude='.env'" in UPDATE
    assert "--exclude='secrets/'" in UPDATE
    for bad in ("pg_dump", "pg_restore", "psql", "board.json"):
        assert bad not in UPDATE
    assert "alembic/" in UPDATE
    assert "alembic.ini" in UPDATE


def test_ng_push_update_requires_ack_and_default_ssh_is_not_legacy():
    text = (DEPLOY / "push-update-container.sh").read_text()
    assert "topo_ng_require_deploy_ack" in text
    assert "topo_ng_refuse_legacy" in text
    assert "9122" in text
    assert "--ssh-port 9022" not in text


def test_ng_package_includes_alembic():
    text = (ROOT / "package-release.sh").read_text()
    assert "server/alembic/" in text
    assert "alembic.ini" in text


def test_ng_data_push_refuses():
    text = (DEPLOY / "push-data-container.sh").read_text()
    assert "exit 1" in text
    assert "Will not overwrite the database volume" in text


def test_ng_gitignore_dist_and_install_tarballs():
    gi = (ROOT / ".gitignore").read_text()
    assert "dist/" in gi
    assert "deploy/install/*.tar.gz" in gi
