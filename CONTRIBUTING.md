# 贡献指南

感谢你考虑为 OpenRobotService 做贡献！

## 开发流程

1. Fork 本仓库并克隆到本地
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交改动：`git commit -m "feat: 描述你的改动"`
4. 推送分支：`git push origin feature/your-feature`
5. 提交 Pull Request

## 提交信息规范（Conventional Commits）

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档变更
- `refactor:` 重构（非功能、非修复）
- `test:` 测试相关
- `chore:` 构建/工具/依赖

## 代码规范

### 后端（Python）
- 遵循 PEP 8，使用 `ruff` 格式化与检查
- 新增功能需附带 `pytest` 测试
- 类型注解尽量完整

```bash
cd backend
ruff check app/
pytest
```

### 前端（Vue）
- 遵循 ESLint 配置
- 组件使用 `<script setup>` 语法

## 报告问题

请通过 [Issues](https://github.com/dhualai/OpenRobotService/issues) 报告 bug 或提出功能建议，尽量提供复现步骤。
