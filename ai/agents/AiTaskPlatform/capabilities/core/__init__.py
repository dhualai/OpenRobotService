"""capabilities.core — 能力基础设施（框架层，产品无关）

与具体能力（capabilities.tools）分离：
  - base: BaseCapability / CapabilityResult（抽象基类 + 统一返回）
  - registry: CapabilityRegistry / get_capability（能力注册表）
  - supervisor: Supervisor / SupervisorDecision（编排内核）
  - supervisor_todo: TodoList / TodoItem（自我任务清单）
  - router: Router（LLM 意图路由）
  - evaluator: Evaluator（自评估）

不在此导入具体能力；具体能力在 capabilities.tools 组织、经注册表自动注册。
"""
