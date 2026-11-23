"""学习中心做题人端服务——只读 + 答题 + 进度统计。

只允许登录用户访问（不要求 PERM_LEARNING），
且只能看到 enabled + active_version>0 的文章和 approved + enabled 的题目。
答题写入 learning_answers 表用于进度追踪。
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.models.learning import (
    LearningAnswer, LearningArticle, LearningArticleVersion, LearningQuestion,
)
from app.modules.admin.services.learning_content import reading_article
from app.modules.admin.services.learning_service import preview_answer as shared_preview_answer


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor_id(actor) -> str:
    return str(actor.get("id") or actor.get("username") or "")


async def list_articles(db, actor, keyword: str = "", page: int = 1):
    """获取启用文章列表，每条附带当前用户的答题进度。"""
    user_id = _actor_id(actor)
    # 只看 enabled + 已确认过的文章
    query = select(LearningArticle).where(
        LearningArticle.enabled.is_(True),
        LearningArticle.active_version > 0,
    )
    if keyword.strip():
        query = query.where(LearningArticle.title.contains(keyword.strip(), autoescape=True))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    articles = (await db.execute(
        query.order_by(LearningArticle.updated_at.desc()).offset((page - 1) * 20).limit(20)
    )).scalars().all()

    if not articles:
        return {"total": 0, "items": []}

    article_ids = [a.id for a in articles]
    user_id_val = user_id

    # 每篇文章的总题数（approved + enabled + 匹配 active_version）
    total_q = (await db.execute(
        select(
            LearningQuestion.article_id,
            LearningArticle.active_version.label("av"),
            func.count().label("cnt"),
        ).join(LearningArticle, LearningQuestion.article_id == LearningArticle.id)
        .where(
            LearningQuestion.article_id.in_(article_ids),
            LearningQuestion.review_status == "approved",
            LearningQuestion.enabled.is_(True),
            LearningQuestion.article_version == LearningArticle.active_version,
        ).group_by(LearningQuestion.article_id)
    )).all()
    total_q_map = {row.article_id: row.cnt for row in total_q}

    # 当前用户的已答题数
    answered = (await db.execute(
        select(
            LearningAnswer.article_id,
            LearningAnswer.article_version,
            func.count().label("cnt"),
        ).where(
            LearningAnswer.user_id == user_id_val,
            LearningAnswer.article_id.in_(article_ids),
        ).group_by(LearningAnswer.article_id, LearningAnswer.article_version)
    )).all()
    answered_map: dict[tuple, int] = {(row.article_id, row.article_version): row.cnt for row in answered}

    items = []
    for art in articles:
        t = total_q_map.get(art.id, 0)
        a = answered_map.get((art.id, art.active_version), 0)
        items.append({
            "id": art.id,
            "title": art.title,
            "category": art.category,
            "summary": (art.draft or {}).get("summary", "") if art.draft else "",
            "active_version": art.active_version,
            "updated_at": art.updated_at.isoformat(),
            "total_count": t,
            "answered_count": a,
            "completed": t > 0 and a >= t,
        })
    return {"total": total, "items": items}


async def get_article(db, actor, article_id: str):
    """获取单篇文章详情 + 题目列表（做题人视角，脱敏）。"""
    user_id = _actor_id(actor)
    article = (await db.execute(
        select(LearningArticle).where(
            LearningArticle.id == article_id,
            LearningArticle.enabled.is_(True),
            LearningArticle.active_version > 0,
        )
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

    # 只返回 approved + enabled 的题目
    questions = (await db.execute(
        select(LearningQuestion).where(
            LearningQuestion.article_id == article.id,
            LearningQuestion.article_version == article.active_version,
            LearningQuestion.review_status == "approved",
            LearningQuestion.enabled.is_(True),
        ).order_by(LearningQuestion.created_at)
    )).scalars().all()

    # 当前用户的答题记录
    answers_row = (await db.execute(
        select(LearningAnswer.question_id, LearningAnswer.answers, LearningAnswer.is_correct, LearningAnswer.created_at)
        .where(
            LearningAnswer.user_id == user_id,
            LearningAnswer.article_id == article.id,
            LearningAnswer.article_version == article.active_version,
        )
    )).all()
    answer_map = {row.question_id: {
        "answers": row.answers, "is_correct": row.is_correct, "created_at": row.created_at.isoformat(),
    } for row in answers_row}

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
    """提交答案——复用 admin 的校验逻辑，且 admin 已统一写入 LearningAnswer。"""
    result = await shared_preview_answer(db, actor, article_id, question_id, answers)
    # preview_answer 内部会写入 LearningAnswer（处理了重复提交）
    return result


async def get_progress(db, actor):
    """当前用户的整体学习进度统计。"""
    user_id = _actor_id(actor)

    # 启用文章数
    articles = (await db.execute(
        select(LearningArticle).where(
            LearningArticle.enabled.is_(True),
            LearningArticle.active_version > 0,
        )
    )).scalars().all()

    total_questions = 0
    article_ids = [a.id for a in articles]
    if article_ids:
        tq = (await db.execute(
            select(func.count()).select_from(LearningQuestion).where(
                LearningQuestion.article_id.in_(article_ids),
                LearningQuestion.review_status == "approved",
                LearningQuestion.enabled.is_(True),
            )
        )).scalar_one() or 0
        total_questions = int(tq)

    answered = (await db.execute(
        select(func.count()).select_from(LearningAnswer).where(
            LearningAnswer.user_id == user_id,
        )
    )).scalar_one() or 0
    correct = (await db.execute(
        select(func.count()).select_from(LearningAnswer).where(
            LearningAnswer.user_id == user_id,
            LearningAnswer.is_correct.is_(True),
        )
    )).scalar_one() or 0

    # 完成的文章数 = 所有 approved/enabled 题目都答过
    completed_articles = 0
    if article_ids:
        for art in articles:
            t = (await db.execute(
                select(func.count()).select_from(LearningQuestion).where(
                    LearningQuestion.article_id == art.id,
                    LearningQuestion.review_status == "approved",
                    LearningQuestion.enabled.is_(True),
                    LearningQuestion.article_version == art.active_version,
                )
            )).scalar_one() or 0
            if t == 0:
                continue
            a = (await db.execute(
                select(func.count()).select_from(LearningAnswer).where(
                    LearningAnswer.user_id == user_id,
                    LearningAnswer.article_id == art.id,
                    LearningAnswer.article_version == art.active_version,
                )
            )).scalar_one() or 0
            if a >= t:
                completed_articles += 1

    return {
        "total_articles": len(articles),
        "completed_articles": completed_articles,
        "total_questions": total_questions,
        "answered_questions": int(answered),
        "correct_questions": int(correct),
    }
