"""部门匹配器：工单 → 部门

三大类问题 → 三个部门，分不清的不分。

  机器人事业部 — 车体硬件/机械故障
  车端软件     — 传感器/算法/通信协议
  智能规划     — 调度系统/服务号

分不清的不分：让三个部门的人一起参与召回+LLM决定。
"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

# ── 三类确定信号 ──

_ROBOT_HW = [
    # 通用硬件故障
    "车辆故障", "机器人故障", "硬件故障", "机械故障", "车体故障", "报修", "故障报修",
    # 具体表现
    "开不了机", "充不进电", "电池故障", "电池没电",
    "电机不转", "电机故障", "电机烧了",
    "轮子坏了", "轮子卡死", "轮子脱落",
    "车体损坏", "车体变形", "车体碰撞", "硬件损坏",
    "底盘故障", "底盘变形",
    "升降机构", "货叉卡住", "货叉不动作",
    "机械臂", "传送带", "滚轮",
    "电源故障", "电源模块", "无法上电", "断电",
    "外壳破损", "外壳变形", "进水", "摔坏",
    "手动模式", "手动切换", "叉车",
]

_CAR_SW = [
    "激光雷达", "雷达故障", "雷达数据异常", "激光扫描",
    "传感器故障", "传感器异常", "传感器数据", "传感器报错",
    "摄像头故障", "视觉传感器", "深度相机",
    "超声波传感器", "红外传感器",
    "定位算法", "定位精度", "定位漂移", "重定位失败", "slam", "定位异常",
    "感知算法", "感知异常", "障碍物检测失败", "目标识别错误",
    "控制器故障", "控制器报错", "控制逻辑错误", "车辆控制异常",
    "网关故障", "网关通信异常", "网关协议", "车载网关",
    "底层驱动", "嵌入式系统", "固件", "内核", "驱动异常",
    "传感器融合", "多传感器", "imu", "里程计",
]

_SCHEDULING = [
    # 调度系统 / 服务号（关键词匹配 → 智能规划部门）
    "调度", "usp", "调度系统", "调度平台",
    "任务调度", "任务分配", "任务下发", "任务队列", "任务阻塞",
    "路径规划", "mapf", "a*", "路径下发", "路径失败",
    "地图编辑", "地图预处理", "路网", "背景图",
    "库位", "载具", "取放货", "货架",
    "外设", "输送线", "电梯", "自动门", "充电桩",
    "仿真", "模拟器", "仿真车",
    "部署", "运维", "容器", "docker", "打包", "版本更新",
    "配置管理", "license", "多语言", "国际化",
    "监控告警", "日志", "在线更新",
    "服务号", "微信", "我要摇人", "工单系统", "智能问答",
    "后台管理", "系统任务", "数据分析", "数据看板",
    "ai派单", "ai诊断", "智能派单", "ai闭环",
    # 以上为调度/服务号关键词，不属于硬件的
]


class DepartmentMatcher:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    def match_department(self, ticket: TicketContext) -> str:
        text = " ".join(filter(None, [
            ticket.title, ticket.problem_description,
        ])).lower()

        robot_hits = [kw for kw in _ROBOT_HW if kw in text]
        car_hits = [kw for kw in _CAR_SW if kw in text]
        sched_hits = [kw for kw in _SCHEDULING if kw in text]

        sources = sum([bool(robot_hits), bool(car_hits), bool(sched_hits)])

        if sources >= 2:
            logger.info(f"[dept_matcher] 跨部门歧义 → 不过滤 "
                        f"(hw={robot_hits[:2]}, sw={car_hits[:2]}, sched={sched_hits[:2]})")
            return ""

        if robot_hits:
            logger.info(f"[dept_matcher] 硬件({robot_hits[:3]}) → 机器人事业部")
            return "机器人事业部"

        if car_hits:
            logger.info(f"[dept_matcher] 车端软件({car_hits[:3]}) → 车端软件")
            return "车端软件"

        if sched_hits:
            logger.info(f"[dept_matcher] 调度({sched_hits[:3]}) → 智能规划")
            return "智能规划"

        logger.info(f"[dept_matcher] 无匹配 → 不过滤")
        return ""

    def filter_by_department(self, engineers, department):
        if not department:
            return list(engineers)
        filtered = [e for e in engineers if e.department == department]
        logger.info(f"[dept_matcher] 部门过滤: {len(engineers)}→{len(filtered)} ({department})")
        return filtered

    def filter(self, ticket, engineers, project_name=""):
        if not engineers:
            return []
        dept = self.match_department(ticket)
        if dept:
            return self.filter_by_department(engineers, dept)
        return list(engineers)
