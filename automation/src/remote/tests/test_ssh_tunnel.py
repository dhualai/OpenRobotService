import pytest

from automation.src.remote import SSHForward, SSHTunnel, SSHTunnelConfig, free_port


def test_build_command_contains_forwarding():
    tunnel = SSHTunnel(
        SSHTunnelConfig(
            host="example.test",
            user="tester",
            ssh_port=8802,
            key_path="/tmp/key",
            remote_host="127.0.0.1",
            remote_port=9400,
            local_port=19400,
        )
    )

    command = tunnel.build_command(19400)

    assert command[0] == "ssh"
    assert "-L" in command
    assert "127.0.0.1:19400:127.0.0.1:9400" in command
    assert "tester@example.test" in command
    assert "/tmp/key" in command


def test_from_env_reads_prefixed_values(monkeypatch):
    monkeypatch.setenv("REAL_SSH_HOST", "example.test")
    monkeypatch.setenv("REAL_SSH_USER", "tester")
    monkeypatch.setenv("REAL_SSH_PORT", "8802")
    monkeypatch.setenv("REAL_REMOTE_API_PORT", "9400")
    monkeypatch.setenv("REAL_LOCAL_API_PORT", "19400")

    tunnel = SSHTunnel.from_env("REAL")

    assert tunnel.config.host == "example.test"
    assert tunnel.config.user == "tester"
    assert tunnel.config.ssh_port == 8802
    assert tunnel.config.remote_port == 9400
    assert tunnel.config.local_port == 19400


def test_build_command_contains_multiple_forwardings():
    tunnel = SSHTunnel(
        SSHTunnelConfig(
            host="example.test",
            user="tester",
            forwards=(
                SSHForward(local_port=19400, remote_port=9400),
                SSHForward(local_port=19411, remote_port=9411),
                SSHForward(local_port=19402, remote_port=3306),
            ),
        )
    )

    command = tunnel.build_command()

    assert command.count("-L") == 3
    assert "127.0.0.1:19400:127.0.0.1:9400" in command
    assert "127.0.0.1:19411:127.0.0.1:9411" in command
    assert "127.0.0.1:19402:127.0.0.1:3306" in command


def test_start_reports_early_exit_stderr(monkeypatch):
    class FakeProcess:
        returncode = 255

        def __init__(self, command, stdout, stderr, text):
            stderr.write("Could not open /tmp/ci-key: permission denied\n")
            stderr.flush()

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        "automation.src.remote.ssh_tunnel.subprocess.Popen",
        FakeProcess,
    )
    tunnel = SSHTunnel(
        SSHTunnelConfig(
            host="example.test",
            user="tester",
            key_path="/tmp/ci-key",
            forwards=(SSHForward(local_port=19400, remote_port=9400),),
        )
    )

    with pytest.raises(RuntimeError, match="permission denied") as exc_info:
        tunnel.start()

    message = str(exc_info.value)
    assert "code 255" in message
    assert "/tmp/ci-key" not in message
    assert "<ssh-key>" in message


def test_free_port_returns_valid_port():
    port = free_port()
    assert 0 < port < 65536
