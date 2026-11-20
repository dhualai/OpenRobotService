"""Tests for the local UI regression Gateway."""

from __future__ import annotations

import json

import httpx
import pytest

from automation.src.ui_regression.config import UiRegressionConfig
from automation.src.ui_regression.gateway import create_app


def _config(tmp_path) -> UiRegressionConfig:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return UiRegressionConfig(
        frontend_dist=dist,
        backend_url="http://backend.test",
        ai_url="http://ai.test",
    )


@pytest.mark.asyncio
async def test_spa_routes_fall_back_to_index(tmp_path):
    config = _config(tmp_path)
    app = create_app(
        config,
        backend_client=httpx.AsyncClient(base_url=config.backend_url),
        ai_client=httpx.AsyncClient(base_url=config.ai_url),
        check_upstreams_on_startup=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.test",
    ) as client:
        root = await client.get("/")
        route = await client.get("/tasks/123")
        asset = await client.get("/assets/app.js")

    assert root.status_code == 200
    assert "app" in root.text
    assert route.status_code == 200
    assert "app" in route.text
    assert asset.status_code == 200
    assert "console.log" in asset.text


@pytest.mark.asyncio
async def test_backend_proxy_preserves_method_body_and_auth(tmp_path):
    config = _config(tmp_path)
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"items": []},
            headers={"x-upstream": "backend"},
        )

    backend = httpx.AsyncClient(
        base_url=config.backend_url,
        transport=httpx.MockTransport(handler),
    )
    ai = httpx.AsyncClient(base_url=config.ai_url)
    app = create_app(
        config,
        backend_client=backend,
        ai_client=ai,
        check_upstreams_on_startup=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.test",
    ) as client:
        response = await client.post(
            "/api/tasks/filter",
            json={"page": 1},
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.headers["x-upstream"] == "backend"
    assert seen == {
        "path": "/api/tasks/filter",
        "method": "POST",
        "body": {"page": 1},
        "authorization": "Bearer test-token",
    }


@pytest.mark.asyncio
async def test_ai_proxy_streams_sse(tmp_path):
    config = _config(tmp_path)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ai/qa/ask/stream"
        return httpx.Response(
            200,
            text='event: status\ndata: {"stage":"review"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    backend = httpx.AsyncClient(base_url=config.backend_url)
    ai = httpx.AsyncClient(
        base_url=config.ai_url,
        transport=httpx.MockTransport(handler),
    )
    app = create_app(
        config,
        backend_client=backend,
        ai_client=ai,
        check_upstreams_on_startup=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.test",
    ) as client:
        response = await client.post("/api/ai/qa/ask/stream", json={})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "stage" in response.text
    assert "review" in response.text


@pytest.mark.asyncio
async def test_gateway_health_reports_upstreams(tmp_path):
    config = _config(tmp_path)
    app = create_app(
        config,
        backend_client=httpx.AsyncClient(base_url=config.backend_url),
        ai_client=httpx.AsyncClient(base_url=config.ai_url),
        check_upstreams_on_startup=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://gateway.test",
    ) as client:
        response = await client.get("/_gateway/health")

    assert response.status_code == 200
    assert response.json()["backend_url"] == config.backend_url
    assert response.json()["ai_url"] == config.ai_url
