import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('ai/.env')
from ai.agents.AiTaskPlatform.contexts import is_platform_ticket
from ai.agents.AiTaskPlatform.schemas import TaskContext

t1 = TaskContext(task_id='1', description='【摇人吧服务号提单】用户希望调整服务号工单评论区的时间显示方式，具体待明确', title='调整时间显示')
t2 = TaskContext(task_id='2', description='机器人XNA-169在11点不动了，疑似路径规划超时', title='车不动')
t3 = TaskContext(task_id='3', description='【AGV调度项目】多车路径冲突需要排查', title='多车冲突')

r1 = is_platform_ticket(t1)
r2 = is_platform_ticket(t2)
r3 = is_platform_ticket(t3)
print('服务号用例 ->', r1, '(期望 True)')
print('调度用例   ->', r2, '(期望 False)')
print('AGV项目用例 ->', r3, '(期望 False)')
assert r1 is True, '服务号应判为平台工单'
assert r2 is False, '调度不应判为平台工单'
assert r3 is False, 'AGV项目不应判为平台工单'
print('全部断言通过 ✓')
