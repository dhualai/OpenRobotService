"""归档派审结果与错误类型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArchiveReviewAssignment:
    """一次成功的归档派审结果。"""

    domain: str
    reviewer_id: str
    load_snapshot: dict[str, int] = field(default_factory=dict)
    probability_snapshot: dict[str, float] = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    reason: str = ""


class ArchiveReviewError(Exception):
    """派审失败：调用方应中止提交，不进入待审。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
