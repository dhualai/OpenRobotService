# ui/ — UI 自动化测试模块

## 职责
- 基于 Playwright 实现，覆盖微信 H5（Vue3 + Vant）和后台管理面板（桌面端）两种 UI 形态。
- 使用 Page Object 模式，每页一个类封装元素定位与交互操作。

## 结构
| 目录/文件 | 说明 |
|-----------|------|
| conftest.py | 浏览器上下文、截图策略、device Fixture |
| pages/ | Page Object 类，每文件对应一个页面或视图 |
| 	ests/ | UI 测试用例 |
| utils/ | 截图对比、移动端/桌面端视口预设 |

## 约定
- 不设独立 components/ 目录；公共 UI 操作方法内聚到 Page Object 的私有方法中。
- 截图策略：失败时自动截图，保存到 output/screenshots/。
