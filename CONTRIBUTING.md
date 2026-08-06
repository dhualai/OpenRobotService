# 贡献指南

感谢你考虑为 OpenRobotService 做贡献！

## 编程最高准则（八荣八耻）—— 全项目通用，优先级高于一切

> **以下 8 条为本项目所有代码编写的最高行为准则（前端 / 后端 / 算法均适用），每次编码前默念一遍，每次 PR Review 逐条核对。**

| 耻 | 荣 |
|:---|:---|
| 1. 以暗猜接口为耻 | 以认真查阅为荣 |
| 2. 以模糊执行为耻 | 以寻求确认为荣 |
| 3. 以言想业务为耻 | 以人类确认为荣 |
| 4. 以创造接口为耻 | 以复用现有为荣 |
| 5. 以跳过验证为耻 | 以主动测试为荣 |
| 6. 以破坏架构为耻 | 以遵循规范为荣 |
| 7. 以假装理解为耻 | 以诚实无知为荣 |
| 8. 以盲目修改为耻 | 以谨慎重构为荣 |

---

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
