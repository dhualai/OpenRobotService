---
name: commit-feature-tests
description: >-
  提交功能前先补测试并跑通。Use when the user asks to commit, 提交, git commit,
  or when a change is a feature, 功能, behavior change, or bugfix that should
  be covered by a test before it is committed.
---

# 功能提交前先写测试

用户要求提交时，先看这次 diff 是不是一个功能或行为变化。是的话，先补测试、跑通，再提交。纯排版、注释、文档改名不必为了提交新造测试。

不要在用户没要求时自行提交。

## 判断

看暂存和未暂存的 diff，不要只看文件名。

- 用户能观察到的行为变了，或调用方依赖的返回值、分支、校验变了：这是功能，必须有对应测试。
- 只改文案、注释、格式，原有测试仍能说明行为：跑相关测试即可，不必新造。
- 一个提交里夹了多块行为：每块行为各有断言，不要用一条「能 import」代替。

## 测试放哪

跟现有测试走，不另起一套。

- `ai/` 下的行为：在被改模块旁边的 `test_*.py` 里加函数。用仓库 `.venv` 的 pytest 跑这个文件。
- `backend/` 下的行为：放进该模块已有的 `backend/tests/`，没有就在同级新建 `test_*.py`。
- `frontend/` 下的行为：有现成组件测试就加；没有就不要为了提交新建一整套前端测试框架。页面能开时按用户操作把改动走一遍；开不了就在提交说明里写清没在浏览器里看到什么。
- `automation/` 下的用例：只在用户要改自动化平台时按 `automation/AGENTS.md` 做。业务功能的单元测试不要塞进 `automation/tests/`。

## 怎么写

1. 先找这个模块已经怎么测：假对象、`tmp_path`、`asyncio.run`，照着写。
2. 断言写行为。删掉这次功能后，测试必须失败。
3. 不连真实模型、不连生产库、不拉现场日志。需要模型时用脚本化的假客户端，按顺序返回命令。
4. 敏感值（口令、地址、电话）要断言它没有出现在给模型的文本里。
5. 跑刚才新增和相邻的测试。失败就改代码或改测试，不要带着失败提交。

## 提交时

测试通过之后才 `git add` 相关测试和功能代码。不要提交 `_probe_automation.py`、`_tmp_topo_agent_zip/`、`.env`、密钥。提交说明里写这次行为为什么要改，以及跑过哪条 pytest。
