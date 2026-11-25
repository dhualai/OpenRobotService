from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("SESSION_SECRET", "test-session-secret-value-32b")
os.environ.setdefault("TOPO_CONTENT_KEY", secrets.token_urlsafe(32))
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("OPENAPI_ENABLED", "true")

env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import re

_live = os.environ.get("DATABASE_URL") or "postgresql+psycopg://topo:topo@127.0.0.1:5432/topo"
os.environ["DATABASE_URL"] = os.environ.get("TOPO_TEST_DATABASE_URL") or re.sub(
    r"/[^/]+$", "/topo_test", _live.split("?")[0]
)

sys.path.insert(0, str(ROOT / "server"))

from app.infra.crypto import ContentCrypto  # noqa: E402
from app.infra.db import configure  # noqa: E402
from app.infra.models import (  # noqa: E402
    Base,
    Board,
    Document,
    Grant,
    Module,
    ModuleVersion,
    ProductRelease,
    ProductTag,
    ReleaseCover,
    User,
    UserProduct,
)
from app.services.auth import hash_password  # noqa: E402
from app.settings import get_settings  # noqa: E402


SECRET_TITLE = "SECRET_KERNEL_NAME"
SECRET_BODY = "SECRET_MODULE_PLAINTEXT_BODY"
PUBLIC_BODY = "public module body"


def _pg_up(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text

        e = create_engine(url, pool_pre_ping=True)
        with e.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def crypto():
    return ContentCrypto(os.environ["TOPO_CONTENT_KEY"])


@pytest.fixture(scope="session")
def db_engine():
    url = os.environ["DATABASE_URL"]
    if not _pg_up(url):
        pytest.skip("postgres not running on 127.0.0.1 — start docker compose")
    get_settings.cache_clear()
    configure(url)
    from app.infra.db import engine as eng

    assert eng is not None
    from sqlalchemy import text

    from app.infra.schema import apply_schema

    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.drop_all(eng)
    apply_schema(eng)
    yield eng
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db(db_engine):
    from app.infra.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def seed(db, crypto):
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    db.add(User(username="admin", password_hash=hash_password("admin-secret"), role="admin"))
    db.add(User(username="eng", password_hash=hash_password("eng-secret"), role="engineer"))
    db.add(User(username="guest", password_hash=hash_password("guest-secret"), role="guest"))
    db.add(User(username="pm1", password_hash=hash_password("pm-secret"), role="pm"))
    for u, prod in (("admin", "usp"), ("eng", "usp"), ("guest", "usp"), ("pm1", "usp")):
        db.add(UserProduct(username=u, product=prod))
    db.add(Grant(username="eng", product="usp", kind="module", resource_id="secret_mod", perm="write"))
    db.add(
        Board(
            product="usp",
            rel_path="board.json",
            doc={
                "node_w": 158,
                "node_h": 46,
                "nodes": [
                    {"id": "biz", "x": 0, "y": 0},
                    {"id": "secret_mod", "x": 10, "y": 10},
                    {"id": "usp_dev", "x": 200, "y": 0},
                    {"id": "usp_stable", "x": 200, "y": 60},
                    {"id": "usp_v100", "x": 400, "y": 0},
                ],
                "groups": [
                    {
                        "id": "usp_algo",
                        "x": 180,
                        "y": -20,
                        "w": 200,
                        "h": 140,
                        "title": "USP-ALGORITHM",
                        "members": ["usp_dev", "usp_stable"],
                    }
                ],
                "edges": [{"from": "biz", "to": "secret_mod"}],
            },
        )
    )
    db.add(
        Module(
            id="biz",
            product="usp",
            board_rel="board.json",
            kind="feature",
            encrypted=False,
            title_blob=crypto.encrypt("业务分配", aad="module.title:usp:biz"),
            current_version="v1.0",
        )
    )
    db.add(
        Module(
            id="secret_mod",
            product="usp",
            board_rel="board.json",
            kind="feature",
            encrypted=True,
            title_blob=crypto.encrypt(SECRET_TITLE, aad="module.title:usp:secret_mod"),
            current_version="v1.0",
        )
    )
    db.add(ModuleVersion(product="usp", module_id="biz", semver="v1.0", lifecycle_band="dev", expected_hours=24))
    db.add(ModuleVersion(product="usp", module_id="secret_mod", semver="v1.0", lifecycle_band="dev", expected_hours=8))
    for mid, title, kind in (
        ("usp_dev", "DEV", "product_release"),
        ("usp_stable", "STABLE", "product_release"),
        ("usp_algo", "USP-ALGORITHM", "product_hub"),
        ("usp_v100", "v1.0.0", "product_release"),
    ):
        db.add(
            Module(
                id=mid,
                product="usp",
                board_rel="board.json",
                kind=kind,
                encrypted=False,
                title_blob=crypto.encrypt(title, aad=f"module.title:usp:{mid}"),
                current_version="v1.0",
            )
        )
    db.add(
        Document(
            product="usp",
            module_id="biz",
            version="v1.0",
            body_blob=crypto.encrypt(PUBLIC_BODY, aad="document:usp:biz:v1.0"),
            content_hash=crypto.content_hash(PUBLIC_BODY),
        )
    )
    db.add(
        Document(
            product="usp",
            module_id="secret_mod",
            version="v1.0",
            body_blob=crypto.encrypt(SECRET_BODY, aad="document:usp:secret_mod:v1.0"),
            content_hash=crypto.content_hash(SECRET_BODY),
        )
    )
    db.add(ProductRelease(id="usp_v100", product="usp", semver="v1.0.0", sort=0))
    db.add(ProductTag(product="usp", tag="dev", release_id="usp_v100"))
    db.add(ReleaseCover(release_id="usp_v100", module_id="biz", module_version="v1.0"))
    db.commit()
    return {"crypto": crypto}


@pytest.fixture()
def client(seed, db_engine):
    from app.settings import get_settings

    get_settings.cache_clear()
    from app.main import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as c:
        yield c


def login(client, user: str, password: str):
    """统一账号通道：带摇人吧 token（测试期由下面的 fake 校验，不连外部服务）。"""
    client.headers.update({"Authorization": f"Bearer test-token-{user}"})
    r = client.get("/api/v1/me")
    assert r.status_code == 200, r.text
    return r


# ---- 账号统一：测试期 mock 摇人吧 /api/auth/me ----
# 真实环境由 app.services.ors_auth.verify_ors_token 调摇人吧后端校验；
# 单测在此按 token 内的用户名返回权限，避免依赖外部服务。
# 注意：conftest 的 seed/各用例若已预建 User 行，provision 会原样沿用其 role/Grant
# （A 方案），所以这里的权限只影响「首次登录的新用户」。
_ORS_TEST_PERMS = {
    "admin": ("admin",),
    "eng": ("frontend:kb:write",),
    "pm1": ("frontend:kb:write",),
    "guest": (),
}


def _install_fake_ors_auth() -> None:
    from app.services import ors_auth

    # 保留真实实现，供 tests/test_ors_auth.py 恢复后单测
    ors_auth._real_verify_ors_token = ors_auth.verify_ors_token

    def _fake(token: str):
        username = (token or "").removeprefix("test-token-").strip()
        if not username:
            return None
        return ors_auth.OrsUser(
            username=username,
            name=username,
            permissions=_ORS_TEST_PERMS.get(username, ("frontend:kb:write",)),
        )

    ors_auth.verify_ors_token = _fake


_install_fake_ors_auth()
