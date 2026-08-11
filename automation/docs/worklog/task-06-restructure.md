# Task-06: 目录结构重构

## 基本信息
| 字段 | 值 |
|------|-----|
| 任务编号 | TASK-06 |
| 任务名称 | framework/ 下沉到根目录 |
| 分支 | hxg |
| 状态 | 已完成 |

## 变更

| 原路径 | 新路径 |
|--------|--------|
| framework/logger/ | logger/ |
| framework/clients/ | clients/ |
| framework/assertions/ | assertions/ |
| framework/fixtures/ | fixtures/ |
| fixtures/ (test data) | testdata/ |
| framework/ | 已删除 |

- 19 个 .py 文件更新 import 路径
- conftest.py / pyproject.toml / README 同步更新
- 87 tests 全部通过
