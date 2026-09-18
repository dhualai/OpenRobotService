"""Reusable SSH tunnel adapter for remote test environments."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def free_port() -> int:
    """Ask the OS for an unused local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class SSHTunnelConfig:
    host: str
    user: str
    remote_host: str = "127.0.0.1"
    remote_port: int = 9400
    ssh_port: int = 22
    key_path: str = ""
    local_host: str = "127.0.0.1"
    local_port: int = 0
    connect_timeout: float = 20.0


class SSHTunnel:
    """Context manager that forwards a remote TCP port to localhost."""

    def __init__(self, config: SSHTunnelConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.local_port: Optional[int] = None

    @classmethod
    def from_env(cls, prefix: str = "REAL") -> "SSHTunnel":
        """Build a tunnel config from environment variables.

        Example variables:
            REAL_SSH_HOST, REAL_SSH_PORT, REAL_SSH_USER, REAL_SSH_KEY,
            REAL_REMOTE_API_HOST, REAL_REMOTE_API_PORT, REAL_LOCAL_API_PORT
        """
        return cls(
            SSHTunnelConfig(
                host=os.environ[f"{prefix}_SSH_HOST"],
                user=os.environ[f"{prefix}_SSH_USER"],
                ssh_port=int(os.getenv(f"{prefix}_SSH_PORT", "22")),
                key_path=os.getenv(f"{prefix}_SSH_KEY", ""),
                remote_host=os.getenv(f"{prefix}_REMOTE_API_HOST", "127.0.0.1"),
                remote_port=int(os.getenv(f"{prefix}_REMOTE_API_PORT", "9400")),
                local_port=int(os.getenv(f"{prefix}_LOCAL_API_PORT", "0")),
                connect_timeout=float(os.getenv(f"{prefix}_SSH_CONNECT_TIMEOUT", "20")),
            )
        )

    def build_command(self, local_port: int) -> list[str]:
        command = [
            "ssh",
            "-N",
            "-p",
            str(self.config.ssh_port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-L",
            f"{self.config.local_host}:{local_port}:{self.config.remote_host}:{self.config.remote_port}",
        ]
        if self.config.key_path:
            command.extend(["-i", self.config.key_path])
        command.append(f"{self.config.user}@{self.config.host}")
        return command

    def start(self) -> int:
        """Start the tunnel and return the selected local port."""
        if self.process is not None and self.process.poll() is None and self.local_port:
            return self.local_port

        self.local_port = self.config.local_port or free_port()
        command = self.build_command(self.local_port)
        self.process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        deadline = time.monotonic() + self.config.connect_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"SSH tunnel exited early: {stderr.strip()}")
            if self._port_open():
                return self.local_port
            time.sleep(0.25)
        self.stop()
        raise TimeoutError(
            f"SSH tunnel did not become ready within {self.config.connect_timeout:.0f}s"
        )

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.local_port = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def local_url(self, scheme: str = "http") -> str:
        if self.local_port is None:
            raise RuntimeError("SSH tunnel has not been started")
        return f"{scheme}://{self.config.local_host}:{self.local_port}"

    def _port_open(self) -> bool:
        if self.local_port is None:
            return False
        try:
            with socket.create_connection(
                (self.config.local_host, self.local_port), timeout=1.0
            ):
                return True
        except OSError:
            return False

    def __enter__(self) -> "SSHTunnel":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()