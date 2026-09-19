"""Local SPA and API gateway for UI regression."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .config import UiRegressionConfig


HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _request_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


async def _check_upstream(
    client: httpx.AsyncClient,
    health_path: str,
    service_name: str,
) -> None:
    try:
        response = await client.get(health_path, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - startup diagnostics
        raise RuntimeError(f"{service_name} upstream is unreachable: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(
            f"{service_name} upstream health check failed: HTTP {response.status_code}"
        )


async def check_upstreams(
    backend_client: httpx.AsyncClient,
    ai_client: httpx.AsyncClient,
) -> None:
    """Fail early when either local tunnel is not connected."""

    await _check_upstream(backend_client, "/api/health", "backend")
    await _check_upstream(ai_client, "/health", "automation AI")


def _safe_static_file(root: Path, relative_path: str) -> Path | None:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def create_app(
    config: UiRegressionConfig,
    *,
    backend_client: httpx.AsyncClient | None = None,
    ai_client: httpx.AsyncClient | None = None,
    check_upstreams_on_startup: bool = True,
) -> FastAPI:
    """Create a same-origin Gateway for local SPA and remote test APIs."""

    owns_backend = backend_client is None
    owns_ai = ai_client is None
    backend = backend_client or httpx.AsyncClient(
        base_url=config.backend_url,
        timeout=config.upstream_timeout,
    )
    ai = ai_client or httpx.AsyncClient(
        base_url=config.ai_url,
        timeout=config.upstream_timeout,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if check_upstreams_on_startup:
            await check_upstreams(backend, ai)
        yield
        if owns_backend:
            await backend.aclose()
        if owns_ai:
            await ai.aclose()

    app = FastAPI(title="OpenRobotService UI Regression Gateway", lifespan=lifespan)

    @app.get("/_gateway/health")
    async def gateway_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "frontend_dist": str(config.frontend_dist),
            "backend_url": config.backend_url,
            "ai_url": config.ai_url,
        }

    @app.api_route(
        "/api/ai/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy_ai(request: Request, path: str) -> Response:
        return await _proxy(request, ai, f"/api/ai/{path}")

    @app.api_route(
        "/api/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def proxy_backend(request: Request, path: str) -> Response:
        return await _proxy(request, backend, f"/api/{path}")

    @app.get("/{path:path}")
    @app.head("/{path:path}")
    async def serve_spa(request: Request, path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        static_file = _safe_static_file(config.frontend_dist, path)
        if static_file is not None:
            return FileResponse(static_file)

        index_file = config.frontend_dist / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=503,
            content={"detail": f"frontend dist not found: {config.frontend_dist}"},
        )

    return app


async def _proxy(
    request: Request,
    client: httpx.AsyncClient,
    upstream_path: str,
) -> Response:
    upstream_request = client.build_request(
        request.method,
        upstream_path,
        params=request.query_params,
        headers=_request_headers(request),
        content=await request.body(),
    )
    upstream_response = await client.send(upstream_request, stream=True)
    return StreamingResponse(
        upstream_response.aiter_bytes(),
        status_code=upstream_response.status_code,
        headers=_response_headers(upstream_response),
        background=BackgroundTask(upstream_response.aclose),
    )


def build_app() -> FastAPI:
    """Zero-argument app factory for uvicorn."""

    return create_app(UiRegressionConfig.from_env())


if __name__ == "__main__":
    import uvicorn

    _config = UiRegressionConfig.from_env()
    uvicorn.run(
        "automation.src.ui_regression.gateway:build_app",
        factory=True,
        host=_config.host,
        port=_config.port,
    )
