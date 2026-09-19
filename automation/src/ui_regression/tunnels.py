"""Manage the two SSH tunnels used by local UI regression."""

from __future__ import annotations

from typing import Callable

from automation.src.remote import SSHTunnel, SSHTunnelConfig

from .config import UiRegressionConfig


TunnelFactory = Callable[[SSHTunnelConfig], SSHTunnel]


class UiTunnelManager:
    """Start backend and automation-AI tunnels as one lifecycle unit."""

    def __init__(
        self,
        config: UiRegressionConfig,
        tunnel_factory: TunnelFactory = SSHTunnel,
    ):
        self.config = config
        self._tunnel_factory = tunnel_factory
        self.backend_tunnel: SSHTunnel | None = None
        self.ai_tunnel: SSHTunnel | None = None

    def start(self) -> tuple[str, str]:
        self._require_ssh_config()
        try:
            self.backend_tunnel = self._tunnel_factory(
                self._tunnel_config(self.config.backend_remote_port,
                                    self.config.backend_local_port)
            )
            self.backend_tunnel.start()

            self.ai_tunnel = self._tunnel_factory(
                self._tunnel_config(self.config.ai_remote_port,
                                    self.config.ai_local_port)
            )
            self.ai_tunnel.start()
        except Exception:
            self.stop()
            raise

        return self.backend_tunnel.local_url(), self.ai_tunnel.local_url()

    def stop(self) -> None:
        for tunnel in (self.ai_tunnel, self.backend_tunnel):
            if tunnel is not None:
                tunnel.stop()
        self.ai_tunnel = None
        self.backend_tunnel = None

    def _tunnel_config(self, remote_port: int, local_port: int) -> SSHTunnelConfig:
        return SSHTunnelConfig(
            host=self.config.ssh_host,
            user=self.config.ssh_user,
            ssh_port=self.config.ssh_port,
            key_path=self.config.ssh_key,
            remote_host="127.0.0.1",
            remote_port=remote_port,
            local_host="127.0.0.1",
            local_port=local_port,
            connect_timeout=self.config.tunnel_timeout,
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
