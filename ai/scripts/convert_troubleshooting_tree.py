"""
将问题排查树 JSON 转换为诊断参考卡片（Markdown），适配通用知识库检索。

旧形式：决策树（逐步骤引导，锁死 LLM）
新形式：诊断参考卡片（结构化知识，LLM 自由参考）

输出：每个 symptom 一个 markdown 文件，按 category 分目录
"""

import json
import re
from pathlib import Path
from typing import Dict, List


def _extract_all_conclusions(node: Dict, results: List[str], prefix: str = "") -> None:
    """递归提取树中所有结论（cause + solution），保留条件链上下文"""
    node_type = node.get("node_type", "step")

    if node_type == "conclusion":
        cause = node.get("cause", "")
        solution = node.get("solution", "")
        if solution:
            ctx = f"{prefix}→ " if prefix else ""
            if cause:
                results.append(f"{ctx}**原因**：{cause}  \n  **处理**：{solution}")
            else:
                results.append(f"{ctx}**处理**：{solution}")
        return

    if node_type == "checklist":
        for item in node.get("items", []):
            check = item.get("check", "")
            result = item.get("result", {})
            r_cause = result.get("cause", "")
            r_solution = result.get("solution", "")
            if r_cause or r_solution:
                c_str = f"**原因**：{r_cause}。" if r_cause else ""
                results.append(f"- 检查：{check}  \n  → {c_str}**处理**：{r_solution}")
        return

    # step / diagnosis / classification
    desc = node.get("description", "")
    branches = node.get("branches", [])
    if not branches:
        return

    for branch in branches:
        condition = branch.get("condition", "")
        is_default = condition.lower() == "default"

        if "result" in branch:
            result = branch["result"]
            cause = result.get("cause", "")
            solution = result.get("solution", "")
            if cause or solution:
                c_str = f"**原因**：{cause}。" if cause else ""
                if is_default and desc:
                    results.append(f"- （{desc}）→ {c_str}**处理**：{solution}")
                elif is_default:
                    results.append(f"- {c_str}**处理**：{solution}")
                elif desc:
                    results.append(f"- **{condition}**（{desc}）→ {c_str}**处理**：{solution}")
                else:
                    results.append(f"- **{condition}** → {c_str}**处理**：{solution}")

        elif "next" in branch:
            next_node = branch["next"]
            next_desc = next_node.get("description", "")
            if is_default:
                new_prefix = prefix
            else:
                new_prefix = f"**{condition}**" if condition else prefix
            if desc and not is_default:
                new_prefix = f"{prefix}{' → ' if prefix else ''}**{desc}**：{condition}"
            _extract_all_conclusions(next_node, results, new_prefix)


def _extract_first_step_question(node: Dict) -> str:
    """提取第一层判断问题，作为快速区分点"""
    branches = node.get("branches", [])
    if not branches:
        return ""
    conditions = [b.get("condition", "") for b in branches if b.get("condition") and b["condition"] != "default"]
    if not conditions:
        return ""
    return " / ".join(conditions)


def _flatten_symptom(symptom: Dict, category_name: str) -> str:
    """将一个 symptom 拍平为诊断参考卡片"""
    name = symptom.get("name", "")
    tree = symptom.get("tree", {})
    root = tree.get("root", {})

    conclusions: List[str] = []
    _extract_all_conclusions(root, conclusions)

    first_q = _extract_first_step_question(root)

    lines = [f"## {name}", ""]
    lines.append(f"> 分类：{category_name} | 诊断参考")
    lines.append("")

    if first_q:
        lines.append(f"**关键判断**：{first_q}")
        lines.append("")

    if conclusions:
        lines.append("**诊断要点**：")
        lines.append("")
        for i, c in enumerate(conclusions, 1):
            lines.append(f"{i}. {c}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def convert(input_path: str, output_dir: str):
    """转换入口"""
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    total = 0
    for category in data.get("categories", []):
        cat_name = category.get("name", "")
        symptoms = category.get("symptoms", [])
        if not symptoms:
            continue

        parts: List[str] = []
        for symptom in symptoms:
            card = _flatten_symptom(symptom, cat_name)
            parts.append(card)
            total += 1

        # 每个 category 输出一个合并的 markdown 文件
        cat_slug = re.sub(r"[^\w]+", "_", cat_name).strip("_")
        output_file = out / f"diagnosis_{cat_slug}.md"
        header = f"# {cat_name} — 诊断参考\n\n> 自动生成自问题排查树 v1，{len(symptoms)} 个场景。\n> 本文件为诊断知识参考，非交互式排查脚本。\n\n---\n\n"
        output_file.write_text(header + "\n\n".join(parts), encoding="utf-8")
        print(f"  ✓ {output_file.name} ({len(symptoms)} 个场景)")

    print(f"\n共 {total} 个场景，{len(data.get('categories', []))} 个分类文件 → {output_dir}")


if __name__ == "__main__":
    import sys
    # 默认路径
    default_input = Path(__file__).resolve().parents[3] / "OpenRobotService_Data" / "docs" / "问题排查树_v1.json"
    default_output = Path(__file__).resolve().parents[3] / "OpenRobotService_Data" / "kb" / "team" / "diagnosis"

    input_path = sys.argv[1] if len(sys.argv) > 1 else str(default_input)
    output_dir = sys.argv[2] if len(sys.argv) > 2 else str(default_output)

    print(f"输入: {input_path}")
    print(f"输出: {output_dir}")
    print()
    convert(input_path, output_dir)
