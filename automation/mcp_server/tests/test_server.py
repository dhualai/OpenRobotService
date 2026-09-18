import pytest

mcp = pytest.importorskip("mcp")

from automation.mcp_server import server


def test_fastmcp_server_registers_runtime():
    assert server.mcp is not None
    assert callable(server.main)