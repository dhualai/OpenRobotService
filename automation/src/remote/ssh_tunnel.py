"""Reusable SSH tunnel adapter for remote test environments."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Optional


def free_port() -> int:
    """Ask the OS for an unused local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True)
class SSHForward:
    """One local-to-remote TCP forwarding rule."""

    local_host: str = "127.0.0.1"
    local_port: int = 0
    remote_host: str = "127.0.0.1"
    remote_port: int = 0


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
    forwards: tuple[SSHForward, ...] = ()


class SSHTunnel:
    """Context manager that forwards one or more remote TCP ports to localhost."""

    def __init__(self, config: SSHTunnelConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.local_port: Optional[int] = None
        self.local_ports: tuple[int, ...] = ()
        self._resolved_forwards: tuple[SSHForward, ...] = ()
        self._stderr_file: Optional[IO[str]] = None
        self._last_stderr = ""

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

    def _configured_forwards(self) -> tuple[SSHForward, ...]:
        if self.config.forwards:
            return self.config.forwards
        return (
            SSHForward(
                local_host=self.config.local_host,
                local_port=self.config.local_port,
                remote_host=self.config.remote_host,
                remote_port=self.config.remote_port,
            ),
        )

    def _resolve_forwards(self) -> tuple[SSHForward, ...]:
        return tuple(
            replace(forward, local_port=forward.local_port or free_port())
            for forward in self._configured_forwards()
        )

    def _build_command(self, forwards: tuple[SSHForward, ...]) -> list[str]:
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
            f"ConnectTimeout={max(1, int(self.config.connect_timeout))}",
            "-o",
            "NumberOfPasswordPrompts=0",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
        ]
        for forward in forwards:
            command.extend(
                [
                    "-L",
                    (
                        f"{forward.local_host}:{forward.local_port}:"
                        f"{forward.remote_host}:{forward.remote_port}"
                    ),
                ]
            )
        if self.config.key_path:
            command.extend(["-i", self.config.key_path])
        command.append(f"{self.config.user}@{self.config.host}")
        return command

    def build_command(self, local_port: int | None = None) -> list[str]:
        forwards = self._configured_forwards()
        if local_port is not None:
            if len(forwards) != 1:
                raise ValueError(
                    "local_port override is only supported for a single forward"
                )
            forwards = (replace(forwards[0], local_port=local_port),)
        return self._build_command(forwards)

    def start(self) -> int:
        """Start the tunnel and return the selected local port."""
        if (
            self.process is not None
            and self.process.poll() is None
            and self.local_port is not None
        ):
            return self.local_port

        self._resolved_forwards = self._resolve_forwards()
        self.local_ports = tuple(
            forward.local_port for forward in self._resolved_forwards
        )
        self.local_port = self.local_ports[0]
        command = self._build_command(self._resolved_forwards)
        self._last_stderr = ""
        self._stderr_file = tempfile.TemporaryFile(
            mode="w+",
            encoding="utf-8",
            errors="replace",
        )
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
                text=True,
            )
        except Exception:
            self._close_stderr()
            raise

        deadline = time.monotonic() + self.config.connect_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return_code = self.process.returncode
                stderr = self._stderr_text()
                self.stop()
                detail = f": {stderr}" if stderr else ""
                raise RuntimeError(
                    f"SSH tunnel exited early with code {return_code}{detail}"
                )
            if self._all_ports_open():
                return self.local_port
            time.sleep(0.25)

        stderr = self._stderr_text()
        self.stop()
        ports = ", ".join(str(port) for port in self.local_ports)
        message = (
            f"SSH tunnel did not become ready within "
            f"{self.config.connect_timeout:.0f}s (local ports: {ports})"
        )
        if stderr:
            message = f"{message}\nSSH stderr:\n{stderr}"
        raise TimeoutError(message)

    def stop(self) -> None:
        if self.process is None:
            self._close_stderr()
            return
        if self.process.poll() is None:
            self._last_stderr = self._stderr_text() or self._last_stderr
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._last_stderr = self._stderr_text() or self._last_stderr
        self._close_stderr()
        self.process = None
        self.local_port = None
        self.local_ports = ()
        self._resolved_forwards = ()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def local_url(self, scheme: str = "http") -> str:
        return self.local_url_at(0, scheme)

    def local_url_at(self, index: int, scheme: str = "http") -> str:
        if not self.local_ports:
            raise RuntimeError("SSH tunnel has not been started")
        forward = self._resolved_forwards[index]
        return f"{scheme}://{forward.local_host}:{forward.local_port}"

    def _port_open(self, forward: SSHForward) -> bool:
        try:
            with socket.create_connection(
                (forward.local_host, forward.local_port), timeout=1.0
            ):
                return True
        except OSError:
            return False

    def _all_ports_open(self) -> bool:
        return bool(self._resolved_forwards) and all(
            self._port_open(forward) for forward in self._resolved_forwards
        )

    def _stderr_text(self) -> str:
        if self._stderr_file is None:
            return self._last_stderr
        try:
            self._stderr_file.flush()
            self._stderr_file.seek(0)
            text = self._stderr_file.read()
            self._stderr_file.seek(0, os.SEEK_END)
        except (OSError, ValueError):
            return self._last_stderr
        if self.config.key_path:
            text = text.replace(self.config.key_path, "<ssh-key>")
        text = text.strip()
        if len(text) > 4000:
            text = text[-4000:]
        return text

    def _close_stderr(self) -> None:
        if self._stderr_file is None:
            return
        try:
            self._stderr_file.close()
        finally:
            self._stderr_file = None

    def __enter__(self) -> "SSHTunnel":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
