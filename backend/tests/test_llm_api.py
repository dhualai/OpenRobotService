#!/usr/bin/env python3
"""
测试 LLM API 连通性

运行方式：
    cd backend
    python -m tests.test_llm_api
"""
import asyncio
import os

from dotenv import load_dotenv

# 加载 AI 模块 .env
_backend = os.path.dirname(os.path.dirname(__file__))
load_dotenv(os.path.join(_backend, "app", "ai", ".env"))

from app.ai.core import get_llm_client
from app.ai.config import get_ai_config


async def test_llm_connection():
    """测试 LLM 连接"""
    print("=" * 60)
    print("开始测试 LLM API 连通性...")
    print("=" * 60)

    try:
        config = get_ai_config()
        print(f"\n✅ 配置加载成功")
        print(f"   API Base URL: {config.deepseek_base_url}")
        print(f"   Model: {config.deepseek_model}")
        print(f"   API Key: {config.deepseek_api_key[:20]}...")
    except Exception as e:
        print(f"\n❌ 配置加载失败: {e}")
        return

    try:
        llm = await get_llm_client()
        print(f"\n✅ LLM 客户端初始化成功")

        print(f"\n📤 发送测试请求: '你好，请用一句话介绍你自己'")
        response = await llm.complete(
            prompt="你好，请用一句话介绍你自己",
            max_tokens=100,
        )

        print(f"\n✅ 收到响应:")
        print(f"   {response}")

    except Exception as e:
        print(f"\n❌ LLM 调用失败: {e}")
        return

    try:
        print(f"\n📤 发送相同请求测试缓存...")
        response2 = await llm.complete(
            prompt="你好，请用一句话介绍你自己",
            max_tokens=100,
        )

        if response == response2:
            print(f"✅ 缓存生效（响应一致）")
        else:
            print(f"⚠️  缓存未生效（响应不一致）")

    except Exception as e:
        print(f"\n❌ 缓存测试失败: {e}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_llm_connection())
