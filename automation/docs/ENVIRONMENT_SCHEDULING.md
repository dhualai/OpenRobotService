# 多环境池与并发调度

## 环境池

`automation/src/runtime/environments.py`

内置环境：

| 环境 | 标签 | 默认并发 |
|---|---|---|
| local | local / mock / fast | 4 |
| test | test / real / pro | 1 |
| nightly | test / real / nightly | 1 |

环境可通过以下变量覆盖地址：

```text
LOCAL_API_BASE_URL
LOCAL_AI_BASE_URL
TEST_API_BASE_URL
TEST_AI_BASE_URL
NIGHTLY_API_BASE_URL
NIGHTLY_AI_BASE_URL
```

## 调度器

```python
from automation.src.runtime import EnvironmentPool, RunScheduler, ScheduledTask

scheduler = RunScheduler(pool=EnvironmentPool())
results = scheduler.run_many(
    [
        ScheduledTask(scenario="api_mock", profile="fast", tags=("fast",)),
        ScheduledTask(scenario="business_chain", profile="fast", tags=("fast",)),
    ],
    max_workers=2,
)
```

约束：

- 每个环境有独立 semaphore。
- test/pro 环境默认串行，避免真实数据竞争。
- 调度器会注入 `OPENROBOT_API_BASE_URL`、`REAL_API_BASE_URL`、`AI_EVAL_BASE_URL`。
- 环境不满足标签时直接报错，不静默降级。