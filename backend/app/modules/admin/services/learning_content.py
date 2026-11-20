import json
import re
import unicodedata

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing import Literal


class KnowledgePoint(BaseModel):
    id: str = Field(pattern=r"^KP[1-9][0-9]*$")
    text: str = Field(min_length=1, max_length=2000)
    source_markers: list[str] = Field(min_length=1, max_length=10)
    source_excerpt: str = Field(min_length=1, max_length=2000)


class ArticleContent(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(default="未分类", min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=10)
    summary: str = Field(default="", max_length=4000)
    applicability: str = Field(default="", max_length=4000)
    key_points: list[KnowledgePoint] = Field(default_factory=list, max_length=5)
    method: str = Field(default="", max_length=6000)
    cautions: str = Field(default="", max_length=4000)
    example: str = Field(default="", max_length=4000)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class QuestionOption(BaseModel):
    key: str = Field(min_length=1, max_length=8, pattern=r"^[A-Z][A-Z0-9]*$")
    text: str = Field(min_length=1, max_length=1000)


class QuestionContent(BaseModel):
    question_type: Literal["single_choice", "multiple_choice", "true_false"]
    stem: str = Field(min_length=1, max_length=4000)
    options: list[QuestionOption] = Field(min_length=2, max_length=6)
    correct_answer: list[str] = Field(min_length=1, max_length=6)
    explanation: str = Field(min_length=1, max_length=4000)
    point_ids: list[str] = Field(min_length=1, max_length=5)
    source_excerpt: str = Field(min_length=1, max_length=4000)
    difficulty: Literal["easy", "medium", "hard"] = "medium"

    @model_validator(mode="after")
    def valid_options(self):
        keys = [option.key for option in self.options]
        texts = [normalize(option.text) for option in self.options]
        answers = self.correct_answer
        if len(set(keys)) != len(keys) or len(set(texts)) != len(texts) or not all(texts):
            raise ValueError("选项key及内容不可重复或为空")
        if len(set(answers)) != len(answers) or not set(answers).issubset(keys):
            raise ValueError("答案必须是有效且不重复的选项key")
        if self.question_type != "multiple_choice" and len(answers) != 1:
            raise ValueError("单选和判断题必须只有一个正确答案")
        if self.question_type == "multiple_choice" and len(answers) < 2:
            raise ValueError("多选题必须至少两个正确答案")
        if self.question_type == "true_false" and set(keys) != {"T", "F"}:
            raise ValueError("判断题必须使用T/F选项")
        if self.question_type == "true_false":
            labels = {option.key: option.text.strip() for option in self.options}
            if labels != {"T": "正确", "F": "错误"}:
                raise ValueError("判断题选项文本固定为正确/错误")
        if not normalize(self.stem) or not normalize(self.explanation):
            raise ValueError("题干和解析不能为空")
        return self


def normalize(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text))).casefold()


def redact(text, terms=()):
    value = str(text or "")
    for term in sorted({str(term) for term in terms if term and len(str(term)) > 1}, key=len, reverse=True):
        value = re.sub(re.escape(term), "[已脱敏]", value, flags=re.IGNORECASE)
    value = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "[链接已隐藏]", value)
    value = re.sub(r"(?:https?://|www\.)\S+|/api/[^\s)]+", "[链接已隐藏]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", value)
    value = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[电话已隐藏]", value)
    value = re.sub(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?", "[地址已隐藏]", value)
    value = re.sub(r"(?i)(?:api[_-]?key|password|passwd|secret|token|密钥|密码|序列号|SN|设备编号)\s*[:=：]\s*[^\s,，;；]+", "[敏感值已隐藏]", value)
    value = re.sub(r"(?i)\b(?:ORS|TASK|TICKET)[-# ]?\d+\b", "[工单编号已隐藏]", value)
    return re.sub(r"<[^>]+>", "", value)


def sanitize(value, terms):
    if isinstance(value, str):
        return redact(value, terms)
    if isinstance(value, list):
        return [sanitize(item, terms) for item in value]
    if isinstance(value, dict):
        structural = {"id", "key", "marker", "source_markers", "question_type", "correct_answer", "point_ids", "difficulty"}
        return {key: item if key in structural else sanitize(item, terms) for key, item in value.items()}
    return value


def parse_json(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        raise HTTPException(422, "AI返回的内容不是有效JSON，请重试") from None


def article_text(content):
    return "\n".join([content.get(key, "") for key in ("summary", "applicability", "method", "cautions", "example")]
        + [point["text"] for point in content.get("key_points", [])])


def validate_article(content, sources):
    if not content["summary"].strip() or not content["applicability"].strip() or not content["key_points"]:
        raise HTTPException(422, "请补齐知识结论、适用条件和核心知识点")
    if content["warnings"]:
        raise HTTPException(422, "请先解决并清除待核验事项")
    points = content["key_points"]
    if len({point["id"] for point in points}) != len(points):
        raise HTTPException(422, "知识点标识不可重复")
    material = {source["marker"]: source["content"] for source in sources}
    for point in points:
        if not point["text"].strip() or not set(point["source_markers"]).issubset(material):
            raise HTTPException(422, "知识点必须关联有效来源标记")
        if not normalize(point["source_excerpt"]) or not any(
            normalize(point["source_excerpt"]) in normalize(material[marker]) for marker in point["source_markers"]
        ):
            raise HTTPException(422, "知识点依据摘录必须来自所选脱敏材料")


def validate_question(payload, article):
    try:
        content = QuestionContent.model_validate(payload).model_dump()
    except ValidationError:
        raise HTTPException(422, "题目结构无效，请检查题型、选项、答案和必填字段") from None
    if not set(content["point_ids"]).issubset({point["id"] for point in article["key_points"]}):
        raise HTTPException(422, "题目关联的知识点不属于当前文章版本")
    if not normalize(content["source_excerpt"]) or normalize(content["source_excerpt"]) not in normalize(article_text(article)):
        raise HTTPException(422, "答案依据摘录必须来自绑定的文章版本")
    return content


def reading_article(content):
    return {key: content.get(key) for key in (
        "title", "category", "tags", "summary", "applicability", "method", "cautions", "example"
    )} | {"key_points": [{"text": point["text"]} for point in content["key_points"]]}
