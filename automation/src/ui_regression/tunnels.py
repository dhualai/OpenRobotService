"""Manage UI regression SSH port forwards."""

from __future__ import annotations

from typing import Callable

from automation.src.remote import SSHForward, SSHTunnel, SSHTunnelConfig

from .config import UiRegressionConfig


TunnelFactory = Callable[[SSHTunnelConfig], SSHTunnel]


class UiTunnelManager:
    """Start all UI regression forwards through one SSH session."""

    def __init__(
        self,
        config: UiRegressionConfig,
        tunnel_factory: TunnelFactory = SSHTunnel,
    ):
        self.config = config
        self._tunnel_factory = tunnel_factory
        self.tunnel: SSHTunnel | None = None
        self.db_local_port: int | None = None

    def start(self) -> tuple[str, str]:
        self._require_ssh_config()
        try:
            self.tunnel = self._tunnel_factory(self._tunnel_config())
            self.tunnel.start()
        except Exception:
            self.stop()
            raise

        self.db_local_port = (
            self.config.db_local_port if self.config.db_cleanup_enabled else None
        )
        return self.tunnel.local_url_at(0), self.tunnel.local_url_at(1)

    def stop(self) -> None:
        if self.tunnel is not None:
            self.tunnel.stop()
        self.tunnel = None
        self.db_local_port = None

    def _tunnel_config(self) -> SSHTunnelConfig:
        forwards = [
            SSHForward(
                local_host="127.0.0.1",
                local_port=self.config.backend_local_port,
                remote_host="127.0.0.1",
                remote_port=self.config.backend_remote_port,
            ),
            SSHForward(
                local_host="127.0.0.1",
                local_port=self.config.ai_local_port,
                remote_host="127.0.0.1",
                remote_port=self.config.ai_remote_port,
            ),
        ]
        if self.config.db_cleanup_enabled:
            forwards.append(
                SSHForward(
                    local_host="127.0.0.1",
                    local_port=self.config.db_local_port,
                    remote_host="127.0.0.1",
                    remote_port=self.config.db_remote_port,
                )
            )
        return SSHTunnelConfig(
            host=self.config.ssh_host,
            user=self.config.ssh_user,
            ssh_port=self.config.ssh_port,
            key_path=self.config.ssh_key,
            connect_timeout=self.config.tunnel_timeout,
            forwards=tuple(forwards),
        )

    def _require_ssh_config(self) -> None:
        if not self.config.ssh_host or not self.config.ssh_user:
            raise ValueError(
                "UI_REGRESSION_SSH_HOST and UI_REGRESSION_SSH_USER are required"
            )

    def __enter__(self) -> "UiTunnelManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
