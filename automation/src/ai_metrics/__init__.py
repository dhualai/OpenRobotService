"""AI quality evaluation metrics (L1 deterministic layer).

Pure Python, no external LLM dependencies.
"""

from automation.src.ai_metrics.schema_validity import check_schema, resolve_path
from automation.src.ai_metrics.keyword_hit import hit_ratio, keyword_hit_passed, missing_keywords
from automation.src.ai_metrics.retrieval_recall import collection_hit, recall_score
from automation.src.ai_metrics.llm_judge import LLMJudgeClient, JudgeUnavailableError, judge_rubric
from automation.src.ai_metrics.faithfulness import judge_faithfulness
from automation.src.ai_metrics.veto import build_veto_prompt, check_veto_rules
from automation.src.ai_metrics.external_eval import (
    deepeval_available,
    external_evaluator_status,
    ragas_available,
    run_deepeval,
    run_ragas,
)
from automation.src.ai_metrics.run_recorder import (
    RunRecorder,
    RESULT_FIELDS,
    get_recorder,
    reset_recorder,
    dump_if_needed,
)

__all__ = [
    "check_schema",
    "resolve_path",
    "hit_ratio",
    "keyword_hit_passed",
    "missing_keywords",
    "collection_hit",
    "recall_score",
    "LLMJudgeClient",
    "JudgeUnavailableError",
    "judge_rubric",
    "judge_faithfulness",
    "build_veto_prompt",
    "check_veto_rules",
    "RunRecorder",
    "RESULT_FIELDS",
    "get_recorder",
    "reset_recorder",
    "dump_if_needed",
    "deepeval_available",
    "ragas_available",
    "external_evaluator_status",
    "run_deepeval",
    "run_ragas",
]
