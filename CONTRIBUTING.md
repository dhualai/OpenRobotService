# 贡献指南

感谢你考虑为 OpenRobotService 做贡献！

## 开发流程

1. Fork 本仓库并克隆到本地
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交改动：`git commit -m "feat: 描述你的改动"`
4. 推送分支：`git push origin feature/your-feature`
5. 提交 Pull Request

## 提交信息规范（Conventional Commits）

**提交信息必须使用中文**（前后端通用，谨防再犯）。

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档变更
- `refactor:` 重构（非功能、非修复）
- `test:` 测试相关
- `chore:` 构建/工具/依赖

> ⚠️ Windows PowerShell 环境注意：`git commit -m "中文"` 会因 GBK→UTF-8 编码冲突产生乱码。
> 解决方法：将中文 message 写入临时文件（UTF-8），用 `git commit -F 文件名` 提交，提交后删除临时文件。

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

### 前端（React + TypeScript + TDesign Mobile）
- 遵循 ESLint 配置
- 组件使用函数式组件 + Hooks 语法

### 微信 H5 适配要求（强制）

本项目是面向微信服务号的 H5 应用，**所有组件与功能开发完成后，必须确保在「微信内置浏览器」与「手机微信端」均适配并流畅运行**，否则视为未完成。

- [ ] **双环境实测**：在 iOS 微信、Android 微信内置浏览器各实测一次（不只是 PC Chrome 模拟器），覆盖核心交互路径
- [ ] **iOS 兼容性**：日期用 `YYYY-MM-DD` 格式（`new Date('2026-01-02')` 而非 `2026/01/02`）；输入框 `position: fixed` 失焦后页面归位；长列表滚动惯性 `-webkit-overflow-scrolling: touch`
- [ ] **安全域名/前端资源路径**：跳转、回调地址带部署前缀（生产 `/p/app/`、测试 `/t/app/`），OAuth `state` 保留完整回跳 URL（参考 `docs/troubleshooting.md` 3.3 节）
- [ ] **JS-SDK 鉴权**：用到分享/扫码/定位的页面，`setupWechatShare` 在路由进入时签名一次，签名失败不阻塞主流程
- [ ] **触控交互**：点击热区 ≥ 44px；避免 hover-only 操作；`click` 事件在 iOS 下有 300ms 延迟，关键反馈用 `touchstart` 或 `fastclick`
- [ ] **网络与性能**：SSE/上传等长连接在微信切后台时会被挂起，需做断线重连；首屏资源控制体积，避免微信内置浏览器白屏超 3s
- [ ] **无 PC-only API**：不使用 `window.alert/confirm/prompt`（微信内置浏览器会拦截或样式异常），改用 TDesign 组件；不依赖 `localStorage` 在无痕模式下的持久性，关键状态兜底到后端
- [ ] **Popup 嵌套**：在一个 `<Popup>` 内部再触发另一个 `<Popup>`（含 DateTimePicker/Picker 外层包 Popup）时，内层 Popup 必须加 `destroyOnClose` + `preventScrollThrough={false}`，否则微信内置浏览器（iOS WKWebView）下 `useLockScroll` 的 touchmove 监听器残留会导致再次打开时滚轮无法滚动（详见 `docs/troubleshooting.md` 3.4 节）

> 详见 `automation/docs/testing/done-definition.md` 提测标准与 `automation/docs/testing/code-review-checklist.md` 前端检查节。

## 报告问题

请通过 [Issues](https://github.com/dhualai/OpenRobotService/issues) 报告 bug 或提出功能建议，尽量提供复现步骤。
