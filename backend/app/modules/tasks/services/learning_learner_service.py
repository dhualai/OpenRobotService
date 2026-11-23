"""学习中心做题人端服务——只读 + 答题 + 进度统计。

只允许登录用户访问（不要求 PERM_LEARNING），
且只能看到 enabled + active_version>0 的文章和 approved + enabled 的题目。
答题写入 learning_answers 表用于进度追踪。
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.models.learning import (
    LearningAnswer, LearningArticle, LearningArticleVersion, LearningQuestion, LearningWrongBook,
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

    # 当前用户的"最后一次"答题结果——每道题只取最近一条记录
    # 用 row_number() 窗口函数取 per (question_id, article_version) 的最后一条
    last_ans_cte = (
        select(
            LearningAnswer.article_id,
            LearningAnswer.article_version,
            LearningAnswer.question_id,
            LearningAnswer.is_correct,
            func.row_number().over(
                partition_by=[LearningAnswer.question_id, LearningAnswer.article_version],
                order_by=LearningAnswer.created_at.desc(),
            ).label("rn"),
        ).where(
            LearningAnswer.user_id == user_id_val,
            LearningAnswer.article_id.in_(article_ids),
        ).cte("last_answers")
    )

    # 每篇文章的"最后一次答过的不同题目数"和"最后一次答对的不同题目数"
    last_agg = (await db.execute(
        select(
            last_ans_cte.c.article_id,
            last_ans_cte.c.article_version,
            func.count().label("answered_cnt"),
            func.sum(case((last_ans_cte.c.is_correct.is_(True), 1), else_=0)).label("correct_cnt"),
        ).where(last_ans_cte.c.rn == 1)
        .group_by(last_ans_cte.c.article_id, last_ans_cte.c.article_version)
    )).all()
    last_map: dict[tuple, tuple] = {
        (row.article_id, row.article_version): (row.answered_cnt, row.correct_cnt or 0)
        for row in last_agg
    }

    items = []
    for art in articles:
        t = total_q_map.get(art.id, 0)
        a, c = last_map.get((art.id, art.active_version), (0, 0))
        items.append({
            "id": art.id,
            "title": art.title,
            "category": art.category,
            "summary": (art.draft or {}).get("summary", "") if art.draft else "",
            "active_version": art.active_version,
            "updated_at": art.updated_at.isoformat(),
            "total_count": t,
            "answered_count": a,
            "correct_count": c,  # 最后一次结果中答对的
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
