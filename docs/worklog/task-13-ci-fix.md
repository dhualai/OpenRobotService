# Task-13: CI 复审问题修复

## 修复内容
| 问题 | 级别 | 修复 |
|------|------|------|
| pip install -e backend/ 炸了 | P1 | 改为 pip install -r backend/requirements.txt |
| test-auth 连不上 MySQL/Redis | P1 | test-auth job 添加独立的 services 块 |
| Allure 报告为空 | P2 | 3 个 job 添加 upload-artifact@v4 |
| Qdrant 用 sleep 5 启动 | P2 | 改到 services 块（带 healthcheck）|
| setup-env.bat 用 timeout | P3 | 改为 docker compose up -d --wait |
| Dockerfile.test 空文件 | P3 | 删除 |

