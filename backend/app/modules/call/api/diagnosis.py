"""
AI 路由定义已迁移到 ai/api/router.py

AI 模块独立启动（端口 8400），路由不再挂载在 backend 服务下。

如需在 backend 中复用 AI 路由：
    1. 在 backend/app/__init__.py 中添加:
       _project_root = Path(__file__).resolve().parent.parent.parent
       sys.path.insert(0, str(_project_root))
    2. 从 ai.api 导入:
       from ai.api import qa_router, chat_router, memory_router
"""
