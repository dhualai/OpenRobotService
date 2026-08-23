"""部门过滤器（已废弃）→ 请使用 filtering.dept_router.DeptRouter 或 CandidateTightener。"""

from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter

# 向后兼容旧引用
DepartmentFilter = DeptRouter

__all__ = ["DepartmentFilter", "DeptRouter"]
