# PR #485 报告脱敏与场景汇总修复

## 目标

让业务报告保留必要的请求与响应诊断信息，遮蔽凭据，并把场景用例纳入 `test-gate` 的报告汇总。

## 改动

- `src/clients/api_client.py` 在生成 Allure 附件时遮蔽凭据字段和敏感头，去掉 URL 内的账号、查询串与片段。报告保留状态码、非敏感头和结构化字段。非结构化响应仅保留元数据，实际 HTTP 响应仍可由调用方读取。
- `.github/workflows/test-gate.yml` 单独上传场景 Allure 目录，名称匹配已有汇总规则。JUnit 继续独立下载。
- `src/reporting/tests/test_business_report.py` 验证合成凭据不会进入原始附件或业务 HTML，覆盖非结构化响应与场景产物汇总配置。

## 验证

本地 API 客户端、断言、Mock、报告和业务 API 回归共 197 条通过。Allure 结果和业务 HTML 均包含 197 条用例，Allure HTML 生成成功。真实场景产物汇总在工作流配置层验证。
