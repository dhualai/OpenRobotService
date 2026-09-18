from automation.src.remote import SSHTunnel, SSHTunnelConfig, free_port


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


def test_free_port_returns_valid_port():
    port = free_port()
    assert 0 < port < 65536