# API 测试说明

当前实现为代码驱动，默认跑内存 MockBackend：

- 用例文件：`automation/tests/{module}/test_{module}_code.py`
- 每个请求通过测试模块内的 `_api()` helper，统一生成 Allure step 与 Request/Response/断言附件
- Mock 开关：`USE_MOCK=1`（默认）/ `USE_MOCK=0`（真实后端）

## 常用命令

```powershell
cd automation

# Mock API 测试
pytest tests/ -m api -v

# 指定模块
pytest tests/tasks -v

# 真实后端冒烟
USE_MOCK=0 REAL_API_BASE_URL=http://localhost:8400 pytest tests/real -m smoke -v
```

## 新增用例

1. 在 `tests/{module}/test_{module}_code.py` 增加 `test_*` 函数。
2. 使用 `_api()` helper，尽量带上 `expected_status` 与 `expected_fields`。
3. 先跑 Mock，再跑 `USE_MOCK=0` 验证真实契约。