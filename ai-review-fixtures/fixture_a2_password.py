# 【AI 检视测试样本 A2】快筛验证：口令字面量
# 本文件为 AI Review 观察期测试样本（虚构值，无真实凭据），不合并。
# 预期快筛：高危「疑似密钥」×1（口令变量名 + 引号内 8 位以上字面量）。
# 预期 AI 深度检视：报口令硬编码高危；对照组 os.environ / 花括号引用不应报。

import os

DB_PASSWORD = "fixture-passw0rd"

# 对照组（引用形态，预期不命中）
API_KEY = os.environ.get("API_KEY")
VAULT_TOKEN = "${VAULT_TOKEN}"
