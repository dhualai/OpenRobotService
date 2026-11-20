"""Tests for UI regression SSH forward management."""

from __future__ import annotations

import pytest

from automation.src.remote import SSHTunnelConfig
from automation.src.ui_regression.config import UiRegressionConfig
from automation.src.ui_regression.tunnels import UiTunnelManager


class FakeTunnel:
    def __init__(self, config: SSHTunnelConfig):
        self.config = config
        self.started = False
        self.stopped = False

    def start(self) -> int:
        self.started = True
        return self.config.forwards[0].local_port

    def stop(self) -> None:
        self.stopped = True

    def local_url_at(self, index: int) -> str:
        return f"http://127.0.0.1:{self.config.forwards[index].local_port}"


def test_manager_starts_all_forwards_in_one_tunnel():
    created: list[FakeTunnel] = []

    def factory(config: SSHTunnelConfig) -> FakeTunnel:
        tunnel = FakeTunnel(config)
        created.append(tunnel)
        return tunnel

    manager = UiTunnelManager(
        UiRegressionConfig(
            ssh_host="example.test",
            ssh_user="tester",
            ssh_port=8802,
            backend_remote_port=9400,
            backend_local_port=19400,
            ai_remote_port=9411,
            ai_local_port=19411,
        ),
        tunnel_factory=factory,
    )

    backend_url, ai_url = manager.start()

    assert backend_url == "http://127.0.0.1:19400"
    assert ai_url == "http://127.0.0.1:19411"
    assert len(created) == 1
    assert [item.remote_port for item in created[0].config.forwards] == [9400, 9411]
    assert all(item.started for item in created)

    manager.stop()
    assert all(item.stopped for item in created)


def test_manager_can_include_database_forward():
    created: list[FakeTunnel] = []

    def factory(config: SSHTunnelConfig) -> FakeTunnel:
        tunnel = FakeTunnel(config)
        created.append(tunnel)
        return tunnel

    manager = UiTunnelManager(
        UiRegressionConfig(
            ssh_host="example.test",
            ssh_user="tester",
            db_cleanup_enabled=True,
            db_remote_port=3306,
            db_local_port=19402,
        ),
        tunnel_factory=factory,
    )

    manager.start()

    assert len(created) == 1
    assert [item.remote_port for item in created[0].config.forwards] == [
        9400,
        9411,
        3306,
    ]
    assert manager.db_local_port == 19402


def test_manager_stops_tunnel_when_start_fails():
    created: list[FakeTunnel] = []

    class FailingTunnel(FakeTunnel):
        def start(self) -> int:
            raise RuntimeError("tunnel failed")

    def factory(config: SSHTunnelConfig) -> FakeTunnel:
        tunnel = FailingTunnel(config)
        created.append(tunnel)
        return tunnel

    manager = UiTunnelManager(
        UiRegressionConfig(ssh_host="example.test", ssh_user="tester"),
        tunnel_factory=factory,
    )

    with pytest.raises(RuntimeError, match="tunnel failed"):
        manager.start()

    assert created[0].stopped is True


def test_manager_requires_ssh_identity():
    manager = UiTunnelManager(UiRegressionConfig())
    with pytest.raises(ValueError, match="UI_REGRESSION_SSH_HOST"):
        manager.start()
