"""关键词提取工具

从文本中提取技术关键词，用于召回层的关键词匹配。
"""

from typing import Dict, List, Set


def extract_keywords(text: str, keyword_dict: Dict[str, List[str]]) -> Set[str]:
    """从文本中提取在 keyword_dict 中出现的词。

    Args:
        text: 输入文本
        keyword_dict: {类别名: [关键词列表]}

    Returns:
        命中的关键词集合（所有类别取并集）
    """
    if not text:
        return set()

    text_lower = text.lower()
    matched: Set[str] = set()

    for keywords in keyword_dict.values():
        for kw in keywords:
            if kw.lower() in text_lower:
                matched.add(kw)

    return matched
