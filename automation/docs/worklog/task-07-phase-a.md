# Task-07: Phase A 架构改进

## 变更内容
- 重命名异常：ConnectionError -> ClientConnectionError, TimeoutError -> ClientTimeoutError
- Retry 统一到 utils/retry.py（消除 clients/base.py 和 utils/retry.py 两处定义）
- 消除循环导入：utils/retry 中 RetryExhaustedError 本地定义 + 异常懒加载
- 消除重复类：clients/exceptions 的 RetryExhaustedError 改为从 utils/retry 导入
- async_retry wrapper 修正：异步函数需要 await 而非直接 return
- 添加 --offline 选项到 conftest.py 和 client_fixtures.py
- 添加配置字段名校验到 ConfigLoader
- 清理 105+ 个文件的 BOM 头
- 112 tests 全部通过

## 修改文件
- clients/exceptions.py, clients/base.py, clients/__init__.py, clients/*.py
- utils/retry.py (新建 + 重写)
- conftest.py, fixtures/client_fixtures.py
- config/loader.py
- 和对应的测试文件

