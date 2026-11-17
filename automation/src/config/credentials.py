"""测试账号凭据统一入口。

环境变量优先，默认值与 Mock 内置用户（src/mocks/backend_mock.py:_DEFAULT_USERS）
及 CI seed 脚本保持一致。未设置任何环境变量时，行为与改造前的内联明文完全一致。

环境变量命名：
    AUTH_ADMIN_USERNAME / AUTH_ADMIN_PASSWORD
    AUTH_ENGINEER_USERNAME / AUTH_ENGINEER_PASSWORD
    AUTH_CUSTOMER_USERNAME / AUTH_CUSTOMER_PASSWORD
    AUTH_U1_USERNAME / AUTH_U1_PASSWORD
    AUTH_U2_USERNAME / AUTH_U2_PASSWORD
"""

from __future__ import annotations

import os
from typing import Dict, Tuple

# 角色 -> (用户名环境变量, 密码环境变量, 默认用户名, 默认密码)
# 默认值严格对齐 Mock 内置用户，避免改动环境变量时行为漂移。
_ROLE_SPECS: Dict[str, Tuple[str, str, str, str]] = {
    "admin": ("AUTH_ADMIN_USERNAME", "AUTH_ADMIN_PASSWORD", "testadmin", "admin123"),
    "engineer": ("AUTH_ENGINEER_USERNAME", "AUTH_ENGINEER_PASSWORD", "engineer", "eng123"),
    "customer": ("AUTH_CUSTOMER_USERNAME", "AUTH_CUSTOMER_PASSWORD", "customer", "cust123"),
    "u1": ("AUTH_U1_USERNAME", "AUTH_U1_PASSWORD", "u1_auto", "123456"),
    "u2": ("AUTH_U2_USERNAME", "AUTH_U2_PASSWORD", "u2_auto", "123456"),
}


def role_credentials(role: str) -> Tuple[str, str]:
    """按角色返回 (用户名, 密码)。

    Args:
        role: admin / engineer / customer / u1 / u2

    Returns:
        (username, password) 元组；环境变量优先，缺省用内置默认值。

    Raises:
        KeyError: 未知角色。
    """
    try:
        user_env, pass_env, default_user, default_pass = _ROLE_SPECS[role]
    except KeyError:
        raise KeyError(f"未知的凭据角色: {role!r}，可选: {sorted(_ROLE_SPECS)}") from None
    return (
        os.getenv(user_env, default_user),
        os.getenv(pass_env, default_pass),
    )
