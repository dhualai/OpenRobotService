# 路径: ai/exceptions.py
"""
AI模块自定义异常类
"""


class AIError(Exception):
    """AI模块基础异常类"""

    def __init__(self, message: str, code: str = "AI_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


class AITimeoutError(AIError):
    """
    AI服务超时异常
    触发场景：
    - LLM 调用重试全部失败后
    - 外部服务响应超时
    """

    def __init__(self, message: str = "AI服务响应超时，请稍后重试"):
        super().__init__(message, code="AI_TIMEOUT")
        self.message = message


class RetrieveEmptyError(AIError):
    """
    检索结果为空异常
    触发场景：
    - 向量数据库查询无结果
    - BM25 稀疏检索无结果
    - 混合检索后无有效结果
    """

    def __init__(self, message: str = "未找到相关操作文档"):
        super().__init__(message, code="RETRIEVE_EMPTY")
        self.message = message


class LowConfidenceError(AIError):
    """
    检索置信度低异常
    触发场景：
    - Top-1 检索得分 < 阈值(0.65)
    - 检索结果与用户意图不匹配
    - 意图分类为非操作类但有低分检索结果
    """

    def __init__(self, message: str = "检索结果置信度低，无法生成可靠回答", confidence: float = 0.0):
        super().__init__(message, code="LOW_CONFIDENCE")
        self.message = message
        self.confidence = confidence


class IntentNotMatchError(AIError):
    """
    意图不匹配异常
    触发场景：
    - 用户意图不是操作类问题（如闲聊、问状态等）
    - 不属于"摇人操作问答"的业务范畴
    """

    def __init__(self, message: str = "该问题不属于操作指引范畴"):
        super().__init__(message, code="INTENT_NOT_MATCH")
        self.message = message


class JSONParseError(AIError):
    """
    LLM输出JSON解析失败异常
    触发场景：
    - LLM 返回的不是合法 JSON
    - JSON 结构不符合预期格式
    - 包含 markdown 代码块包裹的 JSON
    """

    def __init__(self, message: str = "AI返回格式解析失败"):
        super().__init__(message, code="JSON_PARSE_ERROR")
        self.message = message


class ContextRewriteError(AIError):
    """
    上下文改写失败异常
    触发场景：
    - 指代消解失败
    - 无法将"然后呢"等补全为完整问句
    """

    def __init__(self, message: str = "无法理解上下文，请重新描述您的问题"):
        super().__init__(message, code="CONTEXT_REWRITE_ERROR")
        self.message = message


class EmbeddingError(AIError):
    """
    Embedding 生成失败异常
    触发场景：
    - 模型加载失败
    - 向量化计算失败
    """

    def __init__(self, message: str = "文档向量化失败"):
        super().__init__(message, code="EMBEDDING_ERROR")
        self.message = message


class ServiceUnavailableError(AIError):
    """
    外部服务不可用异常
    触发场景：
    - DeepSeek API 不可用
    - Qdrant 服务不可用
    - Redis 服务不可用
    """

    def __init__(self, service_name: str, message: str = ""):
        msg = f"{service_name}服务不可用"
        if message:
            msg += f": {message}"
        super().__init__(msg, code="SERVICE_UNAVAILABLE")
        self.message = msg
        self.service_name = service_name
