"""快速测试图片分析功能 — 传入真实报错截图 + 工单上下文，看视觉 LLM 输出"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 加载 ai/.env
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "ai" / ".env")

import asyncio

TEST_DIR = Path(__file__).parent / "test_images"

async def main():
    # 加载测试图片
    images = []
    for name in ("1.png", "2.png"):
        f = TEST_DIR / name
        if f.exists():
            images.append(f)
            print(f"✅ {name} ({f.stat().st_size:,} bytes)")
        else:
            print(f"❌ {name} 不存在")

    if not images:
        print("没有图片，退出")
        return

    from ai.agents.AiTaskPlatform.attachments.parser import analyze_images

    attachments = [
        {"filename": img.name, "path": str(img.resolve())}
        for img in images
    ]

    task_context = {
        "title": "车不规划路线",
        "description": "车有任务不规划路线，路径规划中。切手动挪位置也不规划",
        "problem_summary": "车辆接到任务后不生成路径，一直显示路径规划中，切手动模式移动位置后也无法恢复",
        "hypotheses": ["路径规划算法异常", "地图数据错误", "任务分配逻辑问题"],
        "fault_code": "",
        "robot_type": "潜伏车",
    }

    print(f"\n上下文: {task_context['description']}")
    print("-" * 60)

    result = await analyze_images(attachments, task_context)
    print(result if result else "（无结果）")
    print("-" * 60)


if __name__ == "__main__":
    asyncio.run(main())
