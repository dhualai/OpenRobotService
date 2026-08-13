import sys
sys.path.insert(0, r"D:\CodeHub\AI\OpenRobotService")
from dotenv import load_dotenv
load_dotenv(r"D:\CodeHub\AI\OpenRobotService\ai\.env")
from ai.config import get_ai_config
from ai.agents.AiTaskPlatform.product_registry import pick_manual_dir, list_products, resolve_product_dir

c = get_ai_config()
print("LOG_MANUALS 解析 OK, 产品数 =", len(c.log_manuals))
for k, v in c.log_manuals.items():
    print("  %s: server=%s" % (k, v["server"]))
    print("      local =%s" % v["local"])

print("产品列表:", list_products())
print("USP日志匹配 ->", pick_manual_dir("algo/DYNAMIC_MAP-USPA-LOGS-/debug_logs.log.29"))
print("ORS日志匹配 ->", pick_manual_dir("something/ORS/service.log"))
print("(当前Windows下 server 不可达，应回退 local => 上面应显示本地 D:/CodeHub/Algorithm 路径)")
