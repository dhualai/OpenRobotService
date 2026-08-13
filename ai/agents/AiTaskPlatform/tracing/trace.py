"""流程埋点（从 pipeline.py 拆分，独立成模块）

职责：
  - 定义可追踪的流程节点常量 NODE_*
  - TraceBus: 追加/取出追踪记录（每次请求独立）

不依赖任何 AI 客户端，纯记录。AiTaskAgent 持有 TraceBus 实例即可。
"""

import time


class Node:
    """可追踪的流程节点（供测试 Agent 对照）"""
    OVERHEAD = "overhead"          # 端点路由 + 客户端初始化
    LOAD_CONTEXT = "load_context"  # 加载工单上下文
    RETRIEVE = "retrieve"          # 三路并行分析
    ATTACHMENT = "attachment"      # 附件分析
    KNOWLEDGE = "knowledge"        # 历史工单检索
    BUILD_PROMPT = "build_prompt"  # Prompt 构建
    LLM = "llm"                    # LLM 调用
    PARSE = "parse"                # 结果解析
    DIAGNOSE = "diagnose"          # 诊断报告
    DISCUSS = "discuss"            # @AI 讨论
    SUMMARIZE = "summarize"        # 讨论摘要
    MEMORY = "memory"              # 记忆保存
    COMMENT = "comment"            # 写 task_comments
    SUBMIT = "submit"              # 方案提交


class TraceBus:
    """请求级埋点容器。每次对外请求前 reset，结束后 pop 全量。"""

    def __init__(self):
        self._trace: list = []

    def add(self, node: str, status: str, **kwargs):
        """追加一条追踪记录。status: ok | error | skipped"""
        entry = {"node": node, "status": status, "ts": round(time.perf_counter() * 1000)}
        entry.update(kwargs)
        self._trace.append(entry)

    def reset(self):
        """清空（新请求开始）"""
        self._trace = []

    def pop(self) -> list:
        """取出全部追踪记录并清空（每次请求独立）"""
        trace = self._trace
        self._trace = []
        return trace
