"""Shared test fixtures: mock backend, auth, and shared infrastructure fixtures."""
import os
import allure
import pytest
from automation.config import load_config
from automation.config.models import ApiConfig, DatabaseConfig
from automation.src.clients.api_client import ApiClient
from automation.src.clients.mysql_client import MySQLClient
from automation.src.mocks.backend_mock import create_mock_transport
from automation.src.logger.handlers import AllureLogHandler
from automation.src.fixtures import (
    api_client,
    config,
    config_env,
    log_config,
    logger,
    mysql_client,
    qdrant_client,
    redis_client,
    setup_logger,
)
import logging

# --- 数据库集成测试基础设施（P1 层，marker: db） ---

# 知识生产闭环相关表，清理顺序遵循外键依赖：事件/子表在前，主表在后
KNOWLEDGE_DB_TABLES = (
    "knowledge_discussions",
    "knowledge_like_events",
    "knowledge_question_likes",
    "archive_review_decisions",
    "knowledge_publications",
    "knowledge_settlements",
)
# 测试数据统一前缀，防止误伤非测试数据
AUTO_T_PREFIX = "AUTO_T_"


class KnowledgeDbSession:
    """MySQLClient + 测试数据跟踪器。

    - client: MySQLClient（pymysql，execute/fetch_one/fetch_all）
    - track_task(task_id) / track_month(month): 登记需清理的主键
    - teardown 时按依赖顺序删除已登记数据，保证幂等可重跑
    """

    def __init__(self, client: MySQLClient):
        self.client = client
        self._task_ids: set[int] = set()
        self._months: set[str] = set()

    def track_task(self, task_id: int) -> int:
        self._task_ids.add(int(task_id))
        return int(task_id)

    def track_month(self, month: str) -> str:
        self._months.add(month)
        return month

    def new_task_id(self) -> int:
        """生成不冲突的测试 task_id（AUTO_T 负数区间，避开真实工单号）。"""
        row = self.client.fetch_one("SELECT COALESCE(MAX(task_id), 0) + 1 AS nid FROM knowledge_discussions")
        nid = int((row or {}).get("nid") or 1)
        nid = max(nid, 9_000_000_000)
        return self.track_task(nid)

    def cleanup(self) -> None:
        if not self._task_ids and not self._months:
            return
        id_list = ",".join(str(t) for t in self._task_ids) or "-1"
        for table in KNOWLEDGE_DB_TABLES:
            if table == "knowledge_settlements":
                if self._months:
                    month_list = ",".join(f"'{m}'" for m in self._months)
                    self.client.execute(f"DELETE FROM {table} WHERE month IN ({month_list})")
            else:
                self.client.execute(f"DELETE FROM {table} WHERE task_id IN ({id_list})")


@pytest.fixture
def knowledge_db() -> "KnowledgeDbSession":
    """知识库集成测试专用会话。

    - 库名可通过 AUTOMATION_DB_NAME 覆盖（约定独立测试库 helpdesk_test），
      未设置时回落到 config.yaml 的 database.database
    - 连接失败或目标库缺表时 skip，不影响其他 lane
    - 前置自检：sql_mode 必须含 STRICT_TRANS_TABLES（唯一约束/非空断言依赖）
    """
    cfg = load_config().database
    db_name = os.getenv("AUTOMATION_DB_NAME", cfg.database)
    if db_name != cfg.database:
        cfg = DatabaseConfig(
            host=cfg.host, port=cfg.port, user=cfg.user, password=cfg.password, database=db_name
        )
    client = MySQLClient(config=cfg)
    try:
        client.connect()
    except Exception as e:  # noqa: BLE001 - 环境不可用统一跳过
        pytest.skip(f"MySQL 不可用（{db_name}）: {e}")

    try:
        rows = client.fetch_all("SHOW TABLES")
    except Exception as e:  # noqa: BLE001
        client.close()
        pytest.skip(f"MySQL 元信息查询失败: {e}")
    existing = {list(r.values())[0] for r in rows}
    missing = [t for t in KNOWLEDGE_DB_TABLES if t not in existing]
    if missing:
        client.close()
        pytest.skip(f"目标库缺表，请先执行 alembic upgrade head: {missing}")

    mode_row = client.fetch_one("SELECT @@sql_mode AS m")
    if "STRICT_TRANS_TABLES" not in str((mode_row or {}).get("m") or ""):
        client.close()
        pytest.skip("测试库未启用 STRICT_TRANS_TABLES，约束断言不可信")

    session = KnowledgeDbSession(client)
    try:
        yield session
    finally:
        try:
            session.cleanup()
        finally:
            client.close()

@pytest.fixture
async def mock_api_client():
    use_mock = os.getenv("USE_MOCK", "1") != "0"
    if use_mock:
        client = ApiClient(
            config=ApiConfig(base_url="http://mock.local", timeout=30),
            transport=create_mock_transport(),
            raise_auth_errors=False,
        )
    else:
        client = ApiClient(config=load_config().api, raise_auth_errors=False)
    await client.connect()
    yield client
    await client.close()

@pytest.fixture
async def mock_auth_token(mock_api_client):
    r = await mock_api_client.post("/api/auth/login", json={"username":"testadmin","password":"admin123"})
    return r.json()["access_token"]

@pytest.fixture
def mock_auth_header(mock_auth_token):
    return {"Authorization": f"Bearer {mock_auth_token}"}

@pytest.fixture(autouse=True)
def allure_test_category(request):
    """按测试文件路径给 Allure 增加顶层分类，便于区分链路和单接口用例。"""
    normalized = str(request.node.fspath).replace("\\", "/")
    if normalized.endswith("/tests/ui/test_call_qa_to_ticket_close_regression.py"):
        allure.dynamic.parent_suite("场景用例")
        allure.dynamic.suite("UI完整业务链路")
    elif normalized.endswith("/tests/ui/test_real_safe_api_smoke.py"):
        allure.dynamic.parent_suite("单接口用例")
        allure.dynamic.suite("真实测试环境 Smoke")
    elif "/tests/business_chain/" in normalized or normalized.endswith("/tests/real/test_ticket_lifecycle_real.py"):
        allure.dynamic.parent_suite("场景用例")
        allure.dynamic.suite("真实后端工单生命周期" if normalized.endswith("/tests/real/test_ticket_lifecycle_real.py") else "完整业务链路")
    elif "/tests/" in normalized:
        module_name = getattr(request.node.module, "__name__", "单接口用例").split(".")[-1]
        allure.dynamic.parent_suite("单接口用例")
        allure.dynamic.suite(module_name)
    yield

@pytest.fixture(autouse=True)
def allure_flush():
    yield
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, AllureLogHandler):
            h.flush()
