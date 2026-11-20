"""FastMCP entry point for the OpenRobot test runtime.

The MCP SDK is optional at import time so the service layer and unit tests
remain usable in environments that have not installed the server dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    # MCP 1.x
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - version dependent
    try:
        # MCP 2.x renamed FastMCP to MCPServer while keeping the same basic
        # constructor, tool decorator, and run(transport=...) interface.
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        FastMCP = None  # type: ignore[assignment]

from automation.mcp_server import service

INSTRUCTIONS = """
OpenRobot is the automation runtime for the OpenRobotService web/API/AI stack.
Use run_scenario to submit a pytest scenario, get_run_status to poll it,
inspect_trace to inspect steps and artifacts, diagnose_run for failure
classification and remediation, diagnose_environment when a
tool fails or an environment is unavailable, run_ai_eval for AI quality
evaluation, and generate_ticket_cases to create reviewed candidate cases
from a production ticket. AI must explore first and must not replace the
deterministic pytest assertions used by CI.
""".strip()

if FastMCP is not None:
    mcp = FastMCP("openrobot-test-runtime", instructions=INSTRUCTIONS)

    @mcp.tool()
    def run_scenario(
        scenario: str,
        profile: str = "fast",
        env: str = "local",
        variables: Optional[Dict[str, Any]] = None,
        wait: bool = False,
        timeout: float = 1800.0,
        extra_args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Submit a named pytest scenario and return its run id."""
        return service.run_scenario(
            scenario=scenario,
            profile=profile,
            env=env,
            variables=variables,
            wait=wait,
            timeout=timeout,
            extra_args=extra_args,
        )

    @mcp.tool()
    def get_run_status(run_id: str) -> Dict[str, Any]:
        """Return status and metadata for one runtime run."""
        return service.get_run_status(run_id)

    @mcp.tool()
    def inspect_trace(run_id: str, step_limit: int = 100) -> Dict[str, Any]:
        """Return structured steps, status, artifacts, and paths for one run."""
        return service.inspect_trace(run_id, step_limit=step_limit)

    @mcp.tool()
    def diagnose_run(run_id: str) -> Dict[str, Any]:
        """Classify a failed run and return ordered remediation steps."""
        return service.diagnose_run(run_id)

    @mcp.tool()
    def diagnose_environment(
        env: str = "local",
        api_base_url: Optional[str] = None,
        ai_base_url: Optional[str] = None,
        check_external: bool = True,
    ) -> Dict[str, Any]:
        """Diagnose runtime, dependencies, API, AI, and infrastructure."""
        return service.diagnose_environment(
            env=env,
            api_base_url=api_base_url,
            ai_base_url=ai_base_url,
            check_external=check_external,
        )

    @mcp.tool()
    def run_ai_eval(
        suite: Optional[str] = None,
        profile: str = "pro",
        env: str = "local",
        wait: bool = False,
        timeout: float = 1800.0,
    ) -> Dict[str, Any]:
        """Submit the AI evaluation suite, optionally filtered by suite name."""
        return service.run_ai_eval(
            suite=suite,
            profile=profile,
            env=env,
            wait=wait,
            timeout=timeout,
        )

    @mcp.tool()
    async def generate_ticket_cases(
        ticket_id: int,
        title: str,
        description: str,
        task_type: str = "feature",
        status: str = "new",
        project_id: str = "Leo_test",
        project_name: str = "摇人吧服务号",
        tags: Optional[List[str]] = None,
        output_root: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate candidate functional cases for one production ticket."""
        return await service.generate_ticket_cases(
            ticket_id=ticket_id,
            title=title,
            description=description,
            task_type=task_type,
            status=status,
            project_id=project_id,
            project_name=project_name,
            tags=tags,
            output_root=Path(output_root) if output_root else None,
        )
else:
    mcp = None


def main(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8001) -> None:
    """Run the MCP server over stdio or SSE."""
    if mcp is None:
        raise RuntimeError(
            'The MCP SDK is required. Install it with: pip install "mcp>=1.26.0" '
            'or pip install -e automation/'
        )
    if transport.lower() == "sse":
        mcp.run(transport="sse", host=host, port=port)
    else:
        mcp.run(transport="stdio")