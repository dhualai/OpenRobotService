"""SSH/SFTP 封装（paramiko）。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


class SshClient:
    """私钥优先，密码兜底。"""

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: str = "",
        private_key_path: str = "",
        connect_timeout: float = 8.0,
    ):
        self.host = host
        self.port = int(port or 22)
        self.username = username
        self.password = password or ""
        self.private_key_path = (private_key_path or "").strip()
        self.connect_timeout = float(connect_timeout or 8.0)
        self._client = None

    def connect(self) -> None:
        import paramiko

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.connect_timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.private_key_path:
            p = Path(self.private_key_path)
            if not p.is_file():
                raise FileNotFoundError(f"SSH 私钥不存在: {self.private_key_path}")
            kwargs["key_filename"] = str(p)
        elif self.password:
            kwargs["password"] = self.password
        else:
            raise ValueError("SSH 未配置私钥或密码")
        client.connect(**kwargs)
        self._client = client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> "SshClient":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def run(self, cmd: str, timeout: float = 120.0) -> Tuple[int, str, str]:
        if self._client is None:
            raise RuntimeError("SSH 未连接")
        stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, out, err

    def download(self, remote_path: str, local_path: str) -> None:
        if self._client is None:
            raise RuntimeError("SSH 未连接")
        sftp = self._client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        finally:
            sftp.close()
