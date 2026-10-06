"""双队列：当前排查只取 steer，followup 留到单独 drain。"""

import asyncio

from ai.agents.AiTaskPlatform.runtime.inject_mailbox import drain, put


def test_followup_is_not_drained_into_current_round():
    task_id = "queue-split-test"

    async def _run():
        await put(task_id, "插入当前排查", lane="steer")
        await put(task_id, "等这轮结束再看", lane="followup")
        steered = await drain(task_id)
        still = await drain(task_id, lane="followup")
        return steered, still

    steered, still = asyncio.run(_run())
    assert steered == ["插入当前排查"]
    assert still == ["等这轮结束再看"]
    assert asyncio.run(drain(task_id)) == []
    assert asyncio.run(drain(task_id, lane="followup")) == []
