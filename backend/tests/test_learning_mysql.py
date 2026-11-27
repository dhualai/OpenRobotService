"""InnoDB regressions for learning writes, enabled by LEARNING_MYSQL_TEST_URL."""

import asyncio
import os
from datetime import datetime, timezone
from importlib.util import find_spec
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Select, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.base import Base
from app.models.learning import LearningAnswer, LearningArticle, LearningArticleVersion, LearningQuestion
from app.modules.admin.api.learning import QuestionAction, SaveQuestion
from app.modules.admin.services import learning_service as admin_service
from app.modules.tasks.services import learning_learner_service as learner_service
from backend.tests.test_knowledge import actor
from backend.tests.test_learning import administrator, content, question


pytestmark = pytest.mark.skipif(
    not os.getenv("LEARNING_MYSQL_TEST_URL"),
    reason="Provide LEARNING_MYSQL_TEST_URL for a disposable MySQL server with CREATE/DROP DATABASE access.",
)


@pytest_asyncio.fixture
async def mysql_sessions():
    url = make_url(os.environ["LEARNING_MYSQL_TEST_URL"])
    if url.drivername in {"mysql", "mysql+pymysql"}:
        driver = "asyncmy" if find_spec("asyncmy") else "aiomysql"
        url = url.set(drivername=f"mysql+{driver}")
    if url.drivername not in {"mysql+asyncmy", "mysql+aiomysql"}:
        pytest.fail("LEARNING_MYSQL_TEST_URL must be a MySQL URL.", pytrace=False)

    schema = f"learning_lock_test_{uuid4().hex}"
    admin_engine = create_async_engine(url.set(database=""), poolclass=NullPool, isolation_level="AUTOCOMMIT")
    test_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4"))
        created = True

        test_engine = create_async_engine(
            url.set(database=schema), poolclass=NullPool, isolation_level="REPEATABLE READ",
        )
        table_names = (
            "companies", "departments", "users", "learning_articles", "learning_article_versions",
            "learning_questions", "learning_answers", "learning_wrongbook", "learning_audits", "tasks", "task_operation_logs",
        )
        async with test_engine.begin() as connection:
            await connection.execute(text("SET SESSION default_storage_engine = InnoDB"))
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, tables=[Base.metadata.tables[name] for name in table_names],
                ),
            )
        yield async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)
    finally:
        try:
            if test_engine is not None:
                await test_engine.dispose()
            if created:
                async with admin_engine.connect() as connection:
                    await connection.execute(text(f"DROP DATABASE `{schema}`"))
        finally:
            await admin_engine.dispose()


async def seed_published_question(sessions):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with sessions() as session:
        article = LearningArticle(
            title="充电故障排查", sources=[], active_version=1, enabled=True,
            created_by="reviewer", created_at=now, updated_at=now,
        )
        session.add(article)
        await session.flush()
        version = LearningArticleVersion(
            article_id=article.id, version=1, snapshot={"content": content().model_dump(), "sources": []},
            confirmed_by="reviewer", created_at=now,
        )
        item = LearningQuestion(
            article_id=article.id, article_version=1, payload=question(), review_status="approved", enabled=True,
            created_at=now, updated_at=now,
        )
        session.add_all([version, item])
        await session.commit()
        return article.id, item.id


def write_lock_table(statement):
    if isinstance(statement, Select) and "FOR UPDATE" in str(statement):
        return statement.get_final_froms()[0].name
    return None


def watch_first_write_lock(session, monkeypatch):
    first_lock = asyncio.get_running_loop().create_future()
    execute = session.execute

    async def capture(statement, *args, **kwargs):
        if not first_lock.done() and (table := write_lock_table(statement)) is not None:
            first_lock.set_result(table)
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", capture)
    return first_lock


async def cancel_tasks(tasks):
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_learner_submit_and_admin_disable_lock_article_before_question(mysql_sessions, monkeypatch):
    article_id, question_id = await seed_published_question(mysql_sessions)
    learner_locked_article = asyncio.Event()
    resume_learner = asyncio.Event()
    async with mysql_sessions() as learner, mysql_sessions() as admin:
        execute = learner.execute

        async def pause_after_article_lock(statement, *args, **kwargs):
            result = await execute(statement, *args, **kwargs)
            if write_lock_table(statement) == "learning_articles":
                learner_locked_article.set()
                await resume_learner.wait()
            return result

        monkeypatch.setattr(learner, "execute", pause_after_article_lock)
        admin_first_lock = watch_first_write_lock(admin, monkeypatch)
        tasks = [asyncio.create_task(learner_service.submit_answer(learner, actor("alice"), article_id, question_id, ["A"]))]
        try:
            await asyncio.wait_for(learner_locked_article.wait(), timeout=10)
            tasks.append(asyncio.create_task(admin_service.question_action(
                admin, administrator(), question_id, QuestionAction(revision=1, action="disable"),
            )))
            assert await asyncio.wait_for(admin_first_lock, timeout=10) == "learning_articles"
            resume_learner.set()
            grade, disabled = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)
            assert grade["is_correct"] is True
            assert disabled["enabled"] is False
        finally:
            await cancel_tasks(tasks)

    async with mysql_sessions() as session:
        answer = (await session.execute(select(LearningAnswer))).scalar_one()
        assert (answer.question_id, answer.user_id, answer.answers) == (question_id, "alice", ["A"])
        assert (await session.get(LearningQuestion, question_id)).enabled is False


@pytest.mark.asyncio
async def test_admin_save_reads_article_version_published_while_waiting_for_lock(mysql_sessions, monkeypatch):
    article_id, question_id = await seed_published_question(mysql_sessions)
    new_excerpt = "复测充电回路后记录读数。"
    revised_content = content().model_copy(update={"summary": new_excerpt}).model_dump()
    revised_question = {**question(), "source_excerpt": new_excerpt}

    async with mysql_sessions() as publisher, mysql_sessions() as admin:
        article = (await publisher.execute(
            select(LearningArticle).where(LearningArticle.id == article_id).with_for_update(),
        )).scalar_one()
        article.active_version = 2
        publisher.add(LearningArticleVersion(
            article_id=article_id, version=2, snapshot={"content": revised_content, "sources": []},
            confirmed_by="reviewer", created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        await publisher.flush()

        admin_first_lock = watch_first_write_lock(admin, monkeypatch)
        save = asyncio.create_task(admin_service.save_question(
            admin, administrator(), question_id, SaveQuestion(revision=1, content=revised_question),
        ))
        try:
            # By its first locking query, the admin has already opened an older snapshot reading ownership.
            assert await asyncio.wait_for(admin_first_lock, timeout=10) == "learning_articles"
            await publisher.commit()
            saved = await asyncio.wait_for(save, timeout=10)
            assert (saved["article_version"], saved["revision"]) == (2, 2)
            assert saved["payload"]["source_excerpt"] == new_excerpt
        finally:
            await cancel_tasks([save])
