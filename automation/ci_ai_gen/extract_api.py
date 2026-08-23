"""Extract FastAPI OpenAPI spec (interface inventory) from backend code.

Usage:
    python -m automation.ci_ai_gen.extract_api --out test-gen/spec
    python -m automation.ci_ai_gen.extract_api --out test-gen/spec --url http://127.0.0.1:8000/api/v1/openapi.json
    python -m automation.ci_ai_gen.extract_api --out test-gen/spec --backend-path backend

Resolution order:
    1. --url: fetch spec from a running service
    2. --app-module: import the FastAPI app directly and call app.openapi()
    3. --backend-path: add path to sys.path then import "app"

Writes openapi.json plus a compact endpoint inventory (endpoints.json)
used as LLM input to keep token usage low.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def fetch_from_url(url: str, timeout: float = 30.0) -> dict:
    import requests

    resp = requests.get(url, timeout=timeout, verify=False)
    resp.raise_for_status()
    return resp.json()


def _stub_backend_db() -> None:
    """Stub app.core.db so the backend app can be imported without a live DB.

    Used when AI_EXTRACT_SKIP_DB=1 (local spec extraction); CI runs with a
    real MySQL service and never takes this path. Only openapi() is called
    afterwards, no queries are executed against the stubbed engine.
    """
    import sys
    import types
    from unittest.mock import MagicMock

    if "app.core.db" in sys.modules:
        return
    mod = types.ModuleType("app.core.db")
    for name in ("engine", "SessionLocal", "async_engine", "AsyncSessionLocal"):
        setattr(mod, name, MagicMock())

    async def _get_db():
        yield None

    mod.get_db = MagicMock()
    mod.get_async_db = _get_db
    sys.modules["app.core.db"] = mod


def fetch_from_app(app_module: str) -> dict:
    if os.getenv("AI_EXTRACT_SKIP_DB", "").strip().lower() in ("1", "true", "yes"):
        _stub_backend_db()
    module = __import__(app_module, fromlist=["app"])
    spec = module.app.openapi()
    if not spec or "paths" not in spec:
        raise RuntimeError(f"openapi() returned invalid spec from {app_module}")
    return spec


def _resolve_fields(schema: dict, components: dict) -> List[str]:
    """Resolve a schema (possibly a $ref / allOf / oneOf) into property names."""
    if not schema:
        return []
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        comp = components.get("schemas", {}).get(name, {})
        return _resolve_fields(comp, components)
    props = schema.get("properties", {})
    if props:
        return list(props.keys())
    for key in ("allOf", "oneOf", "anyOf"):
        for sub in schema.get(key, []) or []:
            fields = _resolve_fields(sub, components)
            if fields:
                return fields
    if schema.get("type") == "array":
        return _resolve_fields(schema.get("items") or {}, components)
    return []


def endpoint_summary(spec: dict) -> List[Dict[str, object]]:
    """Compact per-endpoint inventory: method, path, params, body fields."""
    components = spec.get("components", {})
    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            params = []
            for p in op.get("parameters", []):
                schema = p.get("schema") or {}
                params.append({"name": p.get("name"), "in": p.get("in"),
                               "required": p.get("required", False),
                               "type": schema.get("type"),
                               "enum": schema.get("enum")})
            request_body = None
            rb = op.get("requestBody", {})
            content = rb.get("content", {})
            if content:
                schema = list(content.values())[0].get("schema") or {}
                request_body = {
                    "required": rb.get("required", False),
                    "schema_ref": schema.get("$ref") or schema.get("type"),
                    "fields": _resolve_fields(schema, components),
                }
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "summary": op.get("summary", ""),
                "params": params,
                "request_body": request_body,
                "responses": sorted(r for r in op.get("responses", {})),
            })
    return endpoints


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract FastAPI OpenAPI spec")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--url", default="", help="running service openapi.json URL")
    parser.add_argument("--app-module", default="app", help="module exposing `app` (fallback)")
    parser.add_argument("--backend-path", default="backend",
                        help="project path containing the backend package (fallback)")
    parser.add_argument("--prd", default="", help="PRD document path to copy into output dir")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = None
    if args.url:
        spec = fetch_from_url(args.url)
    else:
        backend_path = Path(args.backend_path).resolve()
        if str(backend_path) not in sys.path:
            sys.path.insert(0, str(backend_path))
        spec = fetch_from_app(args.app_module)

    spec_path = out_dir / "openapi.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    endpoints = endpoint_summary(spec)
    (out_dir / "endpoints.json").write_text(
        json.dumps({"count": len(endpoints), "endpoints": endpoints},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    prd_path = None
    if args.prd:
        src = Path(args.prd)
        if src.is_dir():
            candidates = sorted(src.glob("*.md"))
            src = candidates[0] if candidates else src
        if src.exists():
            prd_path = out_dir / "prd.md"
            prd_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            print(f"[extract_api] WARNING: PRD not found at {args.prd}")

    print(f"[extract_api] spec={spec_path} endpoints={len(endpoints)} "
          f"prd={'yes' if prd_path else 'no'} "
          f"at {time.strftime('%H:%M:%S')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
