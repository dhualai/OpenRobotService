"""学习中心做题人端服务——只读 + 答题 + 进度统计。

只允许登录用户访问（不要求 PERM_LEARNING），
且只能看到 enabled + active_version>0 的文章和 approved + enabled 的题目。
答题写入 learning_answers 表用于进度追踪。
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select

from app.models.learning import (
    LearningAnswer, LearningArticle, LearningArticleVersion, LearningQuestion, LearningWrongBook,
)
from app.modules.admin.services.learning_content import reading_article
from app.modules.admin.services.learning_service import grade_and_record_answer


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor_id(actor) -> str:
    return str(actor.get("id") or actor.get("username") or "")


def _published_articles():
    return select(LearningArticle).where(
        LearningArticle.enabled.is_(True), LearningArticle.active_version > 0,
    )


def _enabled_questions():
    return select(LearningQuestion).where(
        LearningQuestion.review_status == "approved", LearningQuestion.enabled.is_(True),
    )


def _course_questions(article):
    return _enabled_questions().where(
        LearningQuestion.article_id == article.id,
        LearningQuestion.article_version == article.active_version,
    )


def _course_answers(user_id, article):
    return select(LearningAnswer).where(
        LearningAnswer.user_id == user_id,
        LearningAnswer.article_id == article.id,
        LearningAnswer.article_version == article.active_version,
    )


async def _course_counts(db, user_id, article_ids):
    if not article_ids:
        return {}

    questions = (
        _enabled_questions().with_only_columns(
            LearningQuestion.id.label("id"), LearningQuestion.article_id, LearningQuestion.article_version,
        ).join(LearningArticle, LearningQuestion.article_id == LearningArticle.id)
        .where(
            LearningQuestion.article_id.in_(article_ids),
            LearningQuestion.article_version == LearningArticle.active_version,
        ).cte("active_questions")
    )
    last_answers = (
        select(
            LearningAnswer.question_id, LearningAnswer.is_correct,
            func.row_number().over(
                partition_by=LearningAnswer.question_id,
                order_by=(LearningAnswer.created_at.desc(), LearningAnswer.id.desc()),
            ).label("attempt_rank"),
        ).join(questions, and_(
            LearningAnswer.question_id == questions.c.id,
            LearningAnswer.article_id == questions.c.article_id,
            LearningAnswer.article_version == questions.c.article_version,
        )).where(LearningAnswer.user_id == user_id).cte("latest_answers")
    )
    rows = (await db.execute(
        select(
            questions.c.article_id,
            func.count(questions.c.id).label("total"),
            func.count(last_answers.c.question_id).label("answered"),
            func.count(case((last_answers.c.is_correct.is_(True), 1))).label("correct"),
        ).outerjoin(last_answers, and_(
            last_answers.c.question_id == questions.c.id, last_answers.c.attempt_rank == 1,
        )).group_by(questions.c.article_id)
    )).all()
    return {row.article_id: (row.total, row.answered, row.correct) for row in rows}


async def list_articles(db, actor, keyword: str = "", page: int = 1):
    """获取启用文章列表，每条附带当前用户的答题进度。"""
    query = _published_articles()
    if keyword.strip():
        query = query.where(LearningArticle.title.contains(keyword.strip(), autoescape=True))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    articles = (await db.execute(
        query.order_by(LearningArticle.updated_at.desc()).offset((page - 1) * 20).limit(20)
    )).scalars().all()

    counts = await _course_counts(db, _actor_id(actor), [article.id for article in articles])
    items = []
    for art in articles:
        question_count, answered, correct = counts.get(art.id, (0, 0, 0))
        items.append({
            "id": art.id,
            "title": art.title,
            "category": art.category,
            "summary": (art.draft or {}).get("summary", "") if art.draft else "",
            "active_version": art.active_version,
            "updated_at": art.updated_at.isoformat(),
            "total_count": question_count,
            "answered_count": answered,
            "correct_count": correct,
            "completed": question_count > 0 and answered == question_count,
        })
    return {"total": total, "items": items}


async def get_article(db, actor, article_id: str):
    """获取单篇文章详情 + 题目列表（做题人视角，脱敏）。"""
    user_id = _actor_id(actor)
    article = (await db.execute(
        _published_articles().where(LearningArticle.id == article_id)
    )).scalar_one_or_none()
    if not article:
        raise HTTPException(404, "知识点文章不存在或尚未发布")

    version_row = (await db.execute(
        select(LearningArticleVersion).where(
            LearningArticleVersion.article_id == article.id,
            LearningArticleVersion.version == article.active_version,
        )
    )).scalar_one_or_none()
    if not version_row:
        raise HTTPException(422, "文章版本数据异常")

    questions = (await db.execute(
        _course_questions(article).order_by(LearningQuestion.created_at, LearningQuestion.id)
    )).scalars().all()

    answers = (await db.execute(
        _course_answers(user_id, article)
        .where(LearningAnswer.question_id.in_([question.id for question in questions]))
        .order_by(LearningAnswer.created_at, LearningAnswer.id)
    )).scalars().all()
    answer_map = {answer.question_id: {
        "question_id": answer.question_id,
        "answers": answer.answers,
        "is_correct": answer.is_correct,
        "created_at": answer.created_at.isoformat(),
    } for answer in answers}

    return {
        "article": {
            "id": article.id,
            "title": article.title,
            "category": article.category,
            "active_version": article.active_version,
            "content": reading_article(version_row.snapshot["content"]),
        },
        "questions": [
            {
                "id": q.id,
                "question_type": q.payload["question_type"],
                "stem": q.payload["stem"],
                "options": q.payload["options"],
                "has_answer": q.id in answer_map,
            }
            for q in questions
        ],
        "answers": list(answer_map.values()),
    }


async def submit_answer(db, actor, article_id: str, question_id: str, answers: list[str]):
    """Score a published question and update the learner's progress."""
    article = (await db.execute(
        _published_articles().where(LearningArticle.id == article_id).with_for_update()
    )).scalar_one_or_none()
    if article is None:
        raise HTTPException(404, "知识点文章不存在或尚未发布")

    question = (await db.execute(
        _course_questions(article).where(LearningQuestion.id == question_id).with_for_update()
    )).scalar_one_or_none()
    if question is None:
        raise HTTPException(404, "题目不存在或尚未启用")

    user_id = _actor_id(actor)
    # Earlier deployments can enforce one stored answer per learner and question version.
    current_answer = (await db.execute(
        _course_answers(user_id, article).where(LearningAnswer.question_id == question_id)
        .order_by(LearningAnswer.created_at.desc(), LearningAnswer.id.desc()).limit(1).with_for_update()
    )).scalar_one_or_none()
    return await grade_and_record_answer(db, user_id, article, question, answers, current_answer=current_answer)


async def get_progress(db, actor):
    """当前用户的整体学习进度统计。"""
    article_ids = (await db.execute(_published_articles().with_only_columns(LearningArticle.id))).scalars().all()
    counts = (await _course_counts(db, _actor_id(actor), article_ids)).values()
    return {
        "total_articles": len(article_ids),
        "completed_articles": sum(total > 0 and answered == total for total, answered, _ in counts),
        "total_questions": sum(total for total, _, _ in counts),
        "answered_questions": sum(answered for _, answered, _ in counts),
        "correct_questions": sum(correct for _, _, correct in counts),
    }


async def question_count(db):
    query = _enabled_questions().join(LearningArticle, LearningQuestion.article_id == LearningArticle.id).where(
        LearningArticle.enabled.is_(True), LearningArticle.active_version > 0,
        LearningQuestion.article_version == LearningArticle.active_version,
    )
    return {"count": (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()}


async def list_wrongbook(db, actor, status: str = ""):
    """获取用户错题本列表。

    默认返回全部；status 可过滤：
      - '' 空 → 全部
      - wrong → 未掌握
      - mastered → 已掌握
      - skipped → 跳过
    """
    user_id = _actor_id(actor)
    query = select(LearningWrongBook).where(LearningWrongBook.user_id == user_id)
    if status.strip():
        query = query.where(LearningWrongBook.status == status.strip())
    query = query.order_by(
        LearningWrongBook.status,   # 未掌握排前面
        LearningWrongBook.last_wrong_at.desc(),
    )
    rows = (await db.execute(query)).scalars().all()

    # 附带文章标题
    article_ids = list({r.article_id for r in rows})
    article_map = {}
    if article_ids:
        arts = (await db.execute(
            select(LearningArticle.id, LearningArticle.title, LearningArticle.category)
            .where(LearningArticle.id.in_(article_ids))
        )).all()
        article_map = {a.id: {"title": a.title, "category": a.category} for a in arts}

    # 统计
    counts = (await db.execute(
        select(
            LearningWrongBook.status,
            func.count().label("cnt"),
        ).where(LearningWrongBook.user_id == user_id)
        .group_by(LearningWrongBook.status)
    )).all()
    count_map = {row.status: row.cnt for row in counts}

    items = []
    for r in rows:
        art = article_map.get(r.article_id, {})
        items.append({
            "id": r.id,
            "article_id": r.article_id,
            "article_title": art.get("title", ""),
            "article_category": art.get("category", ""),
            "article_version": r.article_version,
            "question_id": r.question_id,
            "question": r.question_payload_snapshot,
            "status": r.status,
            "wrong_count": r.wrong_count,
            "mastered_at": r.mastered_at.isoformat() if r.mastered_at else None,
            "last_wrong_at": r.last_wrong_at.isoformat(),
        })

    return {
        "items": items,
        "summary": {
            "total": sum(count_map.values()),
            "wrong": count_map.get("wrong", 0),
            "mastered": count_map.get("mastered", 0),
            "skipped": count_map.get("skipped", 0),
        },
    }


async def set_wrongbook_status(db, actor, wrongbook_id: str, status: str):
    """用户手动标记错题状态：skipped / mastered / wrong。"""
    user_id = _actor_id(actor)
    if status not in ("wrong", "mastered", "skipped"):
        raise HTTPException(422, "无效的错题状态")
    row = (await db.execute(
        select(LearningWrongBook).where(
            LearningWrongBook.id == wrongbook_id,
            LearningWrongBook.user_id == user_id,
        )
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "错题记录不存在")
    row.status = status
    row.updated_at = utcnow()
    if status == "mastered" and row.mastered_at is None:
        row.mastered_at = utcnow()
    await db.commit()
    return {"id": row.id, "status": row.status}
