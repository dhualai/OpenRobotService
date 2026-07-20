"""
问题排查树 .json → troubleshooting 集合

来源：问题排查树_v1.json（6 大类 46 个故障场景的决策树）
产出：每个 symptom 一个 chunk，tree 线性化为可读步骤文本
"""
import json
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass

from ai.config import get_docs_dir
from ai.ingestion.base import BaseIngester, Chunk
from ai.ingestion.registry import register


@dataclass
class TroubleshootingItem:
    """排查树中的一个 symptom"""
    symptom_id: str
    symptom_name: str
    category: str
    linearized_tree: str


class TroubleshootingJSONIngester(BaseIngester[TroubleshootingItem]):
    """问题排查树 JSON → troubleshooting 集合"""

    source_paths = [get_docs_dir() / "问题排查树_v1.json"]
    collection_prefix = "troubleshooting"
    collection_type = "troubleshooting"
    rebuild = True

    @staticmethod
    def _pointer_reader() -> str:
        from ai.config import get_active_troubleshooting_collection
        return get_active_troubleshooting_collection()

    @staticmethod
    def _pointer_writer(name: str) -> None:
        from ai.config import _write_active_troubleshooting_collection
        _write_active_troubleshooting_collection(name)

    pointer_reader = staticmethod(_pointer_reader)
    pointer_writer = staticmethod(_pointer_writer)

    def parse(self) -> List[TroubleshootingItem]:
        with open(self.source_paths[0], encoding='utf-8') as f:
            data = json.load(f)

        items = []
        for category in data.get("categories", []):
            cat_name = category.get("name", "")
            for symptom in category.get("symptoms", []):
                sid = symptom.get("id", "")
                sname = symptom.get("name", "")
                tree = symptom.get("tree", {})
                linearized = _linearize_symptom(sname, tree)

                items.append(TroubleshootingItem(
                    symptom_id=sid,
                    symptom_name=sname,
                    category=cat_name,
                    linearized_tree=linearized,
                ))
        return items

    def to_chunk(self, item: TroubleshootingItem) -> Chunk:
        text = f"{item.symptom_name}\n{item.linearized_tree}"
        return Chunk(
            id=self.stable_id("troubleshooting", item.symptom_id),
            text=text,
            payload={
                "symptom_id": item.symptom_id,
                "symptom_name": item.symptom_name,
                "category": item.category,
                "linearized_tree": item.linearized_tree,
                "content": text,
                "source": "问题排查树_v1.json",
            },
        )


# ── 线性化决策树 ────────────────────────────────────────────────

def _linearize_symptom(symptom_name: str, tree: Dict) -> str:
    """
    将一棵 symptom 的决策树递归拍平为可读文本。

    格式：
        {symptom_name}

        第1步：{root.description}
          → 用户说「{condition}」→ 【结论】原因：{cause}。方案：{solution}
          → 用户说「{condition}」→ 进入第2步
    """
    lines = [symptom_name, ""]
    root = tree.get("root", {})
    if root:
        _walk_node(root, lines, step_counter=[0])
    return "\n".join(lines)


def _walk_node(node: Dict, lines: List[str], step_counter: List[int], indent: str = ""):
    node_type = node.get("node_type", "step")

    if node_type == "conclusion":
        cause = node.get("cause", "")
        solution = node.get("solution", "")
        if cause or solution:
            cause_str = f"原因：{cause}。" if cause else ""
            lines.append(f"{indent}【结论】{cause_str}方案：{solution}")
        return

    if node_type == "checklist":
        desc = node.get("description", "")
        if desc:
            lines.append(f"{indent}{desc}")
        for i, item in enumerate(node.get("items", []), 1):
            check = item.get("check", "")
            result = item.get("result", {})
            r_cause = result.get("cause", "")
            r_solution = result.get("solution", "")
            lines.append(f"{indent}  {i}. 检查：{check}")
            if r_cause or r_solution:
                c_str = f"原因：{r_cause}。" if r_cause else ""
                lines.append(f"{indent}     → 如异常：【结论】{c_str}方案：{r_solution}")
        return

    # step / diagnosis / classification
    step_counter[0] += 1
    step_num = step_counter[0]
    desc = node.get("description", "")
    lines.append(f"第{step_num}步：{desc}")

    branches = node.get("branches", [])
    if not branches:
        return

    for branch in branches:
        condition = branch.get("condition", "")

        if "result" in branch:
            result = branch["result"]
            cause = result.get("cause", "")
            solution = result.get("solution", "")
            c_str = f"原因：{cause}。" if cause else ""
            lines.append(f"{indent}  → 用户说「{condition}」→ 【结论】{c_str}方案：{solution}")

        elif "next" in branch:
            next_node = branch["next"]
            lines.append(f"{indent}  → 用户说「{condition}」→ 进入第{step_counter[0] + 1}步")
            _walk_node(next_node, lines, step_counter, indent + "    ")


def register_all():
    register(TroubleshootingJSONIngester, description="问题排查树 JSON → troubleshooting 集合")


register_all()
