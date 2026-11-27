# 【AI 检视测试样本 D】深度检视专属：SQL 注入 + 越权 + 路径穿越
# 本文件为 AI Review 观察期测试样本（伪代码，不可运行），不合并。
# 预期快筛：0 命中、保持沉默（f-string 形态的 SQL 不在快筛正则内）——
#   用于验证「快筛不命中 = 沉默」的边界，避免狼来了。
# 预期 AI 深度检视（打 ai-review 标签触发，按安全红线 12 条）：
#   1. SQL 注入高危：ticket_id 未经参数化直接进 f-string 查询；
#   2. 越权高危：接口无鉴权依赖、无属主校验，任何人可读任意工单；
#   3. 路径穿越高危：filename 未固定基目录 + 边界校验即拼路径读文件。

from fastapi import APIRouter

router = APIRouter()


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    row = db.execute(f"SELECT * FROM tickets WHERE id = '{ticket_id}'")   # 预期 1
    return row


@router.get("/attachments")
def read_attachment(filename: str):
    # 预期 3：用户可控文件名直接拼接基目录
    with open("/data/uploads/" + filename, "rb") as fh:
        return fh.read()
