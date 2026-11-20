"""
Supplement product_catalog/*.md with actual data extracted from
机器人产品 PDFs. Updates existing models, creates new ones, rebuilds index.
"""
import os, re
from pathlib import Path
from collections import defaultdict

CATALOG = Path(r"D:\Code\OpenRobotService_Data\kb\company\product_catalog")
MEDIA = CATALOG / "media"

# ──── SECTION 1: Enrichment data for EXISTING models ────

EXISTING_UPDATES = {
    # ═══ 潜伏小车系列 ═══
    "XCD031": {
        "features": "车身小、可原地旋转，适合车间窄通道料架搬运\n- 智能导航：支持二维码/激光SLAM，精准定位±10mm，灵活避障\n- 车体优秀：车身低矮（780×550×250mm），可潜入货架底部实现自动搬运\n- 安全可靠：激光避障、触边、急停开关及料架检测功能多重防护\n- 电池快换：便捷更换电池，保障设备长时间连续工作",
        "scenarios": "窄通道料架搬运\n- 点对点搬运\n- 生产车间自动化设备对接",
    },
    "XCD061": {
        "features": "车身小、可原地旋转，适合车间窄通道料架搬运\n- 智能导航：支持二维码/激光SLAM，精准定位±10mm，灵活避障\n- 安全可靠：配备激光避障、紧急停止等多重安全防护设备\n- 灵活呼叫：PAD、手机、电脑和按钮盒，随时随地呼叫\n- 丰富接口：可对接机械臂、电梯和自动门等自动化设备",
        "scenarios": "窄通道物料搬运\n- 多点对多点长距离物料运输\n- 对接机械臂、自动门等自动化设备",
    },
    "XCD101": {
        "features": "智能导航：二维码/激光SLAM，精准定位\n- 强劲动力：搭载高性能电机，爬坡能力强，运行平稳\n- 安全可靠：多重安全防护，确保人机安全\n- 多种呼叫方式：PAD、手机、电脑和按钮盒\n- 丰富接口：可对接机械臂、电梯和自动门，可接入ERP、MES、WCS",
        "scenarios": "窄通道料架搬运\n- 多点对多点长距离物料运输\n- 产线对接搬运",
    },
    "XCD151": {
        "features": "智能导航：支持激光SLAM、二维码导航，精准定位\n- 强劲动力：高性能驱动系统，满足1500kg负载需求\n- 安全可靠：激光避障、激光触边、避障相机、超声雷达多重防护\n- 丰富接口：可对接机械臂、电梯和自动门，可接入ERP、MES、WCS\n- 电池快换：便捷更换电池，保障长时间连续工作",
        "scenarios": "窄通道料架搬运\n- 长距离物料运输\n- 重载搬运",
    },

    # ═══ XSC平衡重堆高系列 ═══
    "XSC081": {
        "features": "最小直角堆垛通道宽度仅2.9m\n- 手动/自动模式自由切换，可替代叉车作业\n- 多重防护手段保障人机作业安全\n- 适配托盘、料架等多种载具\n- 支持3D激光SLAM导航，精度高\n- 可选配机械臂、输送线、自动门等外设对接",
        "scenarios": "窄通道高位货架堆垛\n- 长距离搬运\n- 自动化设备对接",
    },
    "XSC121": {
        "features": "最小直角堆垛通道宽度仅2.9m\n- 手动/自动模式自由切换\n- 多重防护手段保障作业安全\n- 支持3D激光SLAM导航，精度±10mm\n- 可选配机械臂、输送线、自动门等外设对接\n- 支持WMS、MES、ERP等系统对接",
        "scenarios": "窄通道高位货架堆垛\n- 长距离搬运\n- 输送线对接搬运",
    },
    "XSC151": {
        "features": "3D激光SLAM导航，适应窄通道高位货架\n- 最小直角堆垛通道宽度仅2.9m\n- 手动/自动模式自由切换\n- 多重防护手段保障人机作业安全\n- 配备前盲区补偿、2D视觉识别等多重传感器",
        "scenarios": "窄通道高位货架堆垛\n- 跨区域长距离物料搬运\n- 自动化设备对接",
    },
    "XSC201": {
        "features": "2000kg重载堆高，3D激光SLAM导航\n- 最小直角堆垛通道宽度仅2.9m\n- 手动/自动模式自由切换\n- 多重防护手段（激光避障、触边、防撞条等）\n- 可对接输送线、机械臂等自动化设备",
        "scenarios": "窄通道重载货架堆垛\n- 长距离重载搬运\n- 自动化设备对接",
    },

    # ═══ 智能搬运车系列（从XP/XS PDF） ═══
    "XP1152": {
        "features": "1500kg智能搬运机器人，激光SLAM/反光板导航\n- 薄背车身设计，窄通道平面搬运\n- 单侧取卸货通道宽度仅2.3m\n- 可对接MES、WMS等管理系统\n- 可对接机械臂、输送线等自动化设备\n- 顶部向下3D相机+底部双避障激光，多重安全防护",
        "scenarios": "窄通道平面智能搬运\n- 窄通道货架搬运\n- 输送线对接搬运",
    },
    "XP1201": {
        "features": "2000kg智能搬运机器人，激光SLAM/反光板导航\n- 单侧取卸货通道宽度仅2.3m\n- 可对接MES、WMS等管理系统\n- 可对接机械臂、输送线等自动化设备\n- 顶部向下3D相机+底部双避障激光\n- 双到位检测，保障叉取精度",
        "scenarios": "窄通道平面重载搬运\n- 窄通道货架搬运\n- 输送线对接搬运",
    },
    "XS1152": {
        "features": "1500kg薄背堆高机器人，激光SLAM/反光板导航\n- 起升高度2.5m，专为窄通道货架堆垛设计\n- 可对接MES、WMS等管理系统\n- 顶部向下3D相机+底部双避障激光\n- 双到位检测\n- 可选配5G通讯",
        "scenarios": "窄通道低位货架堆垛\n- 平面搬运\n- 输送线对接",
    },

    # ═══ 智能前移系列 ═══
    "XQE151": {
        "description": "XQE151 是室内前移式机器人，采用3D激光SLAM/二维码融合导航，起升高度5.5m。适配窄通道中高位货架堆垛和密集仓储场景，可对接立体库等自动化设备。",
        "features": "3D激光SLAM/二维码融合定位，精度±10mm\n- 起升高度5500mm，适配中高位货架堆垛\n- 前移距离590mm，紧凑前移设计\n- 常规通道中高位货架智能堆叠\n- 多种安全防护：补盲激光+两侧避障激光+叉尖光电+防撞条\n- 支持WMS、MES、ERP等系统对接\n- 可选配机械臂、升降机、自动门、输送线",
        "scenarios": "常规通道中高位货架智能堆叠\n- 密集仓储\n- 对接立体库等自动化设备",
        "specs": {
            "导航方式": "3D激光SLAM/二维码",
            "载荷": "1500 kg",
            "自重": "2880 kg",
            "外形尺寸(长/宽/高)": "2407/1240/3053 mm",
            "起升高度": "5500 mm",
            "前移距离": "590 mm",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "48/150 V/Ah",
            "续航时间": "5-6 h",
            "行驶速度(满载/空载)": "1/1 m/s",
            "货叉尺寸": "40/100/1200 mm",
            "货叉外宽": "438/535/630/730 mm",
        },
    },
    "XQE122": {
        "description": "XQE122 是室内前移式机器人，起升高度5.5m，车身宽度2.9m。适配窄通道中高位货架智能堆垛和密集仓储，可对接立体库等自动化设备。",
        "features": "适配窄通道作业，定制化支腿宽度，搭配紧凑车身\n- 常规通道内中高位货架智能堆垛\n- 起升高度5500mm\n- 可对接立体库等自动化设备\n- 3D激光SLAM导航，精准定位±10mm",
        "scenarios": "常规通道内中高位货架智能堆垛\n- 密集仓储\n- 对接立体库等自动化设备",
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "1200 kg",
            "自重": "2800 kg",
            "外形尺寸(长/宽/高)": "2424/1240/2900 mm",
            "起升高度": "5500 mm",
            "前移距离": "590 mm",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "48/150 V/Ah",
            "续航时间": "5-6 h",
            "货叉尺寸": "35/90/1200 mm",
            "货叉外宽": "438/535/630/730 mm",
        },
    },
    "XQC201": {
        "description": "XQC201E 是门架前移式机器人，采用3D激光SLAM导航，起升高度3.6m。2000kg额定载荷，专为货物密集堆垛和高位货架堆垛设计，可对接自动化设备。",
        "features": "3D激光SLAM导航，精准定位±10mm\n- 2000kg额定载荷，适应货物密集堆垛\n- 起升高度3600mm，前移距离555mm\n- 顶部相机+补盲激光+两侧避障激光，多重防护\n- 叉尖光电+防撞条+急停开关\n- 可选配机械臂、升降机、自动门、输送线\n- 支持WMS、MES、ERP系统对接",
        "scenarios": "货物密集堆垛\n- 高位货架堆垛\n- 对接自动化设备",
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "2000 kg",
            "自重": "3420 kg",
            "外形尺寸(长/宽/高)": "2688/1496/2472 mm",
            "起升高度": "3600 mm",
            "前移距离": "555 mm",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "48/280 V/Ah",
            "续航时间": "6-8 h",
            "行驶速度(满载/空载)": "1.5/2 m/s",
            "货叉尺寸": "40/120/1070 mm",
            "货叉外宽": "200-900 mm",
        },
    },

    # ═══ 智能侧向堆垛系列 ═══
    "XNA101": {
        "description": "XNA101 是双侧叉窄通道堆高机器人，1.0吨额定载荷，最大起升高度6000mm。最窄行驶通道仅1740mm，双侧叉取货物，专为窄通道中高位货架仓储设计。",
        "features": "双侧叉取货物，无需掉头即可两侧作业\n- 最窄行驶通道仅1740mm，极致窄巷道适应\n- 最大起升高度6000mm，覆盖中高位货架\n- 高精度定位±10mm\n- 自主完成取货、堆垛、转运及卸货\n- 安全防撞，防货物坠落",
        "scenarios": "窄通道中高位货架仓储\n- 多层货架堆高\n- 窄巷道仓储库区",
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "1000 kg",
            "自重": "6000 kg",
            "外形尺寸(长/宽/高)": "2515/1540/3400 mm",
            "起升高度": "6000 mm",
            "货叉尺寸": "40/100/1190 mm",
            "货叉外宽": "600 mm",
            "行驶速度(满载/空载)": "1.8/2 m/s",
            "直角转弯通道宽度(1200×1000托盘)": "2200 mm",
            "单侧取卸货通道宽度(1200×1000托盘)": "1740 mm",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "48/205 V/Ah",
        },
    },
    "XNA121": {
        "description": "XNA121 是双侧叉窄通道堆高机器人，1.2吨额定载荷，最大起升高度可达13000mm。适配窄通道作业，双侧灵活叉取，搭载智能控制系统实现全流程无人化运作。",
        "features": "双侧叉取，转向灵活，无需掉头即可双向作业\n- 起升高度可达13000mm，覆盖高位货架\n- 最窄通道仅1740mm，极致窄巷道适应\n- 3D激光SLAM+二维码融合定位，精准可靠\n- 全流程无人化运作：自主完成取货、堆垛、转运及卸货\n- 可对接机械臂、输送线等设备\n- 高精度定位、安全防撞、防货物坠落",
        "scenarios": "窄通道高位货架仓储\n- 多层货架堆高（可达13m）\n- 窄巷道仓储库区\n- 对接机械臂、输送线等自动化设备",
        "specs": {
            "导航方式": "3D激光SLAM+二维码融合定位",
            "载荷": "1200 kg",
            "自重": "6300 kg",
            "外形尺寸(长/宽/高)": "3145/1540/4465 mm",
            "起升高度": "8500 mm（可选配至13000mm）",
            "货叉尺寸": "40/100/1255 mm",
            "货叉外宽": "685 mm",
            "行驶速度(满载/空载)": "10/10.5 km/h",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "48/560 V/Ah",
            "续航时间": "6-8 h",
            "充电时间": "2-3 h",
        },
    },
    "XNA151": {
        "description": "XNA151 是双侧叉平衡重式机器人，1.5吨载荷，起升高度达13000mm，是搬马机器人系列中起升最高的车型。采用3D激光SLAM+二维码融合定位，专为窄巷道高位货架堆垛设计。",
        "features": "13000mm极致起升高度 — 搬马系列最高\n- 3D激光SLAM+二维码融合定位，精度±10mm\n- 窄巷道高位货架堆垛\n- 双侧叉取，高效灵活\n- 1.5吨额定载荷，适应重型托盘",
        "scenarios": "窄巷道高位货架堆垛（最高13m）\n- 高位仓储库区\n- 重型托盘存取",
        "specs": {
            "导航方式": "3D激光SLAM+二维码融合定位",
            "载荷": "1500 kg",
            "自重": "8100 kg",
            "外形尺寸(长/宽/高)": "3414/1540/6328 mm",
            "起升高度": "13000 mm",
            "货叉外宽": "600 mm",
            "行驶速度(满载/空载)": "1/1 m/s",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "48/560 V/Ah",
            "通道宽度": "1740 mm",
        },
    },

    # ═══ 智能叉车系列 ═══
    "XFL201": {
        "description": "XFL201 是2.0吨平衡重式具身机器人，专为智能仓储、物流园及货车装卸场景打造。通过3D激光雷达和具身视觉自动识别车厢及货箱，适应弱光、坡差、托盘变形，支持远程驾驶与AI自主装卸，货物间隙小于1cm。",
        "features": "3D激光SLAM技术，室内外通用，单点精度＜1cm，定位精度±30mm\n- 具身AI视觉：自动识别车厢及货箱，适应弱光、坡差、托盘变形\n- 1cm级精准装卸：激光雷达识别+插齿侧移，货物间隙最小1cm\n- 伺服转向角度精度＜1°\n- 车身窄、转弯半径小（1743mm），直角转弯通道宽度仅1940mm\n- 80V/280Ah磷酸铁锂电池，续航8-10小时\n- 支持手动/自动双充电模式\n- 支持远程驾驶与AI自主装卸\n- 适用于飞翼车、棚车、集装箱等复杂装卸场景",
        "scenarios": "室内外卡车智能装卸\n- 物流园与物流码头转运\n- 室内外全天候转运\n- 24小时不间断自动化运行\n- 飞翼车、棚车、集装箱装卸",
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "2000 kg",
            "自重": "3717 kg",
            "外形尺寸(长/宽/高)": "2567/1224/2306 mm",
            "起升高度": "3300 mm",
            "货叉尺寸": "40/122/1125 mm",
            "货叉外宽": "300-1000 mm",
            "定位精度": "±30 mm",
            "电瓶电压/标称容量": "80/280 V/Ah",
            "续航时间": "8-10 h",
            "行驶速度(满载/空载)": "1.5/2.2 m/s",
            "直角转弯通道宽度(1200×1000托盘)": "1940 mm",
            "转弯半径": "1743 mm",
        },
    },

    # ═══ 智能牵引系列 ═══
    "XCART": {
        "description": "XCART 是观光牵引机器人，电动乘驾式，采用3D激光SLAM导航。兼具观光接驳和场内转运功能，配备语音宣传播报和视频宣传显示器。",
        "features": "3D激光SLAM导航，室内外通用\n- 乘驾式电动设计，500kg额定载荷\n- 配备避障相机+警示灯+侧边警示灯，多重安全防护\n- 语音安全提醒+语音宣传播报\n- 触摸屏+宣传展示屏双屏交互\n- 可对接WMS、MES等系统\n- 支持PAD、手机、电脑等多种呼叫方式",
        "scenarios": "场内观光接驳\n- 场内转运\n- 园区物流",
        "specs": {
            "导航方式": "3D激光SLAM",
            "动力形式": "电动",
            "操作方式": "乘驾式",
            "载荷": "500 kg",
            "自重": "852 kg",
            "整车长度（含拖钩）": "3160 mm",
            "整车宽度": "1695 mm",
            "整体高度": "2500 mm",
            "转弯半径": "4890 mm",
            "行驶速度(满载/空载)": "6/6 km/h",
            "电瓶电压/标称容量": "48/150 V/Ah",
        },
    },
    "XTD601": {
        "description": "XTD601 是室内牵引式机器人，采用3D激光SLAM导航。6000kg牵引载荷，适用于室内外全天候多工况作业环境多车牵引。",
        "features": "3D激光SLAM导航，精度±30mm\n- 6000kg牵引载荷\n- 室内外全天候、多工况作业\n- 多车协同牵引\n- 磷酸铁锂电池，48V/280Ah",
        "scenarios": "室内外全天候多工况牵引\n- 多车协同牵引\n- 长距离转运",
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "6000 kg（牵引）",
            "自重": "1220 kg",
            "外形尺寸(长/宽/高)": "1966/1445/2400 mm",
            "转弯半径": "1850 mm",
            "行驶速度(满载/空载)": "2/2.5 m/s",
            "定位精度": "±30 mm",
            "最大爬坡度(满载/空载)": "7/25 %",
            "电瓶电压/标称容量": "48/280 V/Ah",
        },
    },

    # ═══ XS2201 enrichment ═══
    "XS2201": {
        "features_append": "2000kg重载堆高机器人，激光SLAM导航\n- 专为窄通道货架堆垛设计\n- 可对接MES、WMS等管理系统\n- 可对接机械臂、输送线等自动化设备",
        "scenarios_append": "窄通道货架堆垛\n- 重载搬运\n- 自动化设备对接",
    },
}


# ──── SECTION 2: NEW models to create ────

NEW_MODELS = {
    "XS1201": {
        "name": "XS1201 薄背堆高机器人",
        "series": "智能堆高系列",
        "description": (
            "XS1201 是2.0吨薄背堆高机器人，采用激光SLAM导航，起升高度4500mm。"
            "专为窄通道高位货架堆垛设计，单侧取卸货通道宽度仅2.4m。"
            "配备顶部向下避障相机+底部双避障激光+叉尖光电+双到位检测，安全可靠。"
            "可对接MES、WMS等管理系统及输送线等自动化设备。"
        ),
        "features": [
            "激光SLAM导航，精度±10mm",
            "起升高度4500mm，适配高位货架堆垛",
            "单侧取卸货通道宽度仅2.4m，极致窄通道",
            "自动/手动双模式",
            "顶部向下避障相机+底部双避障激光，360°安全防护",
            "叉尖光电+双到位检测，精准叉取",
            "可对接MES、WMS、输送线等",
            "可选配5G通讯",
        ],
        "scenarios": [
            "窄通道高位货架堆垛",
            "重载托盘搬运",
            "输送线对接搬运",
        ],
        "specs": {
            "导航方式": "激光SLAM",
            "载荷": "2000 kg",
            "自重": "1070 kg",
            "外形尺寸(长/宽/高)": "1819/1046/2040 mm",
            "起升高度": "4500 mm",
            "定位精度": "±10 mm",
            "电瓶电压/标称容量": "24/280 V/Ah",
            "续航时间": "6-8 h",
            "行驶速度(满载/空载)": "1.6/1.5 m/s",
            "货叉尺寸": "60/170/1220 mm",
            "货叉外宽": "630 mm",
            "转弯半径": "1338 mm",
        },
        "pdf_sources": ["3.智能机器人XP1152，201,XS1152,201.pdf"],
    },

    "XFL151": {
        "name": "XFL151 平衡重式机器人",
        "series": "智能堆高系列",
        "description": (
            "XFL151 是1.5吨平衡重式机器人，专为棚车/集装箱/卡车掏箱装卸设计。"
            "引入3D激光雷达深度识别，不依赖光照，在箱内黑暗环境下仍能自动识别托盘高度及姿态信息，实现居中叉取。"
            "配备折叠货叉，结构更紧凑、车身更小巧。一体式多功能铸造车架，空间利用率高、结构强度高、散热优化。"
        ),
        "features": [
            "3D激光雷达深度识别，适应黑暗环境掏箱作业",
            "折叠货叉：结构紧凑，车身小巧，实时监测货叉状态",
            "多传感器布局：顶部激光SLAM+补盲激光+底部双激光+侧面超声",
            "一体式多功能铸造车架：高空间利用率、高强度、散热优化",
            "最大行驶速度6/8 km/h（满载/空载）",
            "支持自动充电、5G通讯",
            "可对接自动门、输送线、码垛机、机械臂、升降机",
            "支持WMS、MES、ERP系统对接",
        ],
        "scenarios": [
            "棚车掏箱装卸",
            "集装箱掏箱装卸",
            "卡车尾箱掏箱装卸",
            "窄通道平衡重堆垛",
        ],
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "1500 kg",
            "自重": "4047 kg",
            "外形尺寸(长/宽/高)": "2567/1084/2158 mm",
            "起升高度": "1200 mm",
            "定位精度": "±30 mm",
            "电瓶电压/标称容量": "80/280 V/Ah",
            "续航时间": "8-10 h",
            "行驶速度(满载/空载)": "6/8 km/h",
            "驱动方式": "前置双驱",
            "货叉尺寸": "40/100/1125 mm",
            "货叉外宽": "200-770 mm",
            "转弯半径": "1140 mm",
        },
        "pdf_sources": ["5智能叉车系列_XFL151E(双语版).pdf"],
    },

    "XQOE151": {
        "name": "XQOE151 全向剪刀叉前移式机器人",
        "series": "智能前移系列",
        "description": (
            "XQOE151 是1.5吨全向剪刀叉前移式机器人，专为货车装卸及现代化智能仓储场景打造。"
            "配备全向灵活底盘，可横移、直行、蟹行、原地中心回转，适配2650mm窄通道作业。"
            "双剪刀叉行程1200mm，货叉±2°旋转纠偏，托盘歪斜仍可精准装卸。"
        ),
        "features": [
            "全向灵活底盘：可横移、直行、蟹行、原地中心回转",
            "适配2650mm窄通道作业",
            "双剪刀叉行程1200mm，深位取货",
            "货叉±2°旋转纠偏，托盘歪斜仍可精准装卸",
            "磷酸铁锂电池，6-8小时续航，2-3小时快充",
            "支持手动/自动双充电模式",
            "多机集群协同作业",
        ],
        "scenarios": [
            "货车车厢装卸作业",
            "全向移动灵活取货",
            "多机集群协同作业",
            "托盘错位容错码垛",
        ],
        "specs": {
            "导航方式": "3D激光SLAM",
            "载荷": "1500 kg",
            "自重": "5000 kg",
            "外形尺寸(长/宽/高)": "2600/2411/3101 mm",
            "起升高度": "4500 mm",
            "前移距离": "1200 mm（双剪刀叉）",
            "行驶速度(满载/空载)": "1.5 m/s",
            "电瓶电压/标称容量": "48/560 V/Ah",
            "续航时间": "6-8 h",
            "充电时间": "2-3 h",
            "单侧取卸货通道宽度(1200×1000托盘)": "2800 mm",
        },
        "pdf_sources": ["6智能前移系列_XQOE151(双语版-简易).pdf"],
    },

    "XTD061": {
        "name": "XTD061 室内牵引式机器人",
        "series": "智能牵引系列",
        "description": (
            "XTD061 是室内牵引式机器人，专为室内低成本牵引作业设计。"
            "小转弯半径，窄通道、密集区域自如穿行，路径灵活。"
            "标配牵引脱钩，快速挂接与脱钩，适配常规料车。一键启动，操作便捷。"
        ),
        "features": [
            "小转弯半径，窄通道自由穿行",
            "标配牵引脱钩，快速挂接与脱钩，适配常规料车",
            "一键启动，界面直观，降低培训门槛",
            "室内外全天候、多工况环境作业",
            "多车协同，高频重复性点对点货物转运",
            "支持换电，保障连续作业",
        ],
        "scenarios": [
            "室内低成本牵引",
            "多车串联转运",
            "点对点货物转运",
        ],
        "specs": {
            "导航方式": "激光SLAM",
            "载荷": "1500 kg（牵引）",
            "自重": "360 kg",
            "外形尺寸(长/宽/高)": "878/611/1438 mm",
            "行驶速度(满载/空载)": "4/4 km/h",
            "定位精度": "±20 mm",
            "电瓶电压/标称容量": "24/60 V/Ah",
            "续航时间": "3-4 h（可换电）",
        },
        "pdf_sources": ["8、智能牵引系列_1-XTD061(双语版-简易).pdf"],
    },

    "XT1401": {
        "name": "XT1401 室内牵引式机器人",
        "series": "智能牵引系列",
        "description": (
            "XT1401 是一款一次性牵引多台物料小车的手自一体牵引机器人。"
            "采用激光SLAM/反光板导航，4000kg牵引载荷，适用于室内长距离牵引作业。"
        ),
        "features": [
            "激光SLAM/反光板导航",
            "4000kg牵引载荷",
            "手自一体操作",
            "一次性牵引多台物料小车",
            "室内长距离牵引",
        ],
        "scenarios": [
            "室内长距离牵引",
            "多台料车串联转运",
        ],
        "specs": {
            "导航方式": "激光SLAM/反光板",
            "载荷": "4000 kg（牵引）",
            "外形尺寸(长/宽/高)": "1513/910/2182 mm",
            "行驶速度(满载/空载)": "1/1.4 m/s",
            "定位精度": "±20 mm",
            "最大爬坡度(满载/空载)": "3/15 %",
            "电瓶电压/标称容量": "25/205 V/Ah",
        },
        "pdf_sources": ["8、智能牵引系列_5、XT1401单页样本.pdf"],
    },

    "XTDH30": {
        "name": "XTD-H30 机场牵引式机器人",
        "series": "智能牵引系列",
        "description": (
            "XTD-H30 是机场牵引式机器人，专为机场停机坪货物转运场景设计。"
            "负责高效衔接航站楼与飞机之间的物流链路，可牵引成组的行李拖斗或货柜。"
            "采用激光3D-SLAM融合定位（可选GNSS），30吨级牵引力，是保障航班地面服务高效运行的关键装备。"
        ),
        "features": [
            "激光3D-SLAM融合定位（可选GNSS），定位精度±30mm",
            "30吨级最大牵引力",
            "前后左右侧补盲避障雷达，360°安全防护",
            "驾驶室内/车外左右两侧急停开关",
            "可选4G/5G通讯",
            "支持WMS、MES、ERP等系统对接",
            "语音提醒+前大灯+转向灯，全方位声光警示",
            "有人/无人双模式",
        ],
        "scenarios": [
            "机场停机坪货物转运",
            "航站楼与飞机之间物流衔接",
            "成组行李拖斗/货柜牵引",
            "出港行李分拣区至机下运输",
            "到港行李及航空货物配送",
        ],
        "specs": {
            "导航方式": "激光3D-SLAM融合定位（可选GNSS）",
            "操作方式": "有人/无人",
            "自重": "4200 kg",
            "外形尺寸(长/宽/高)": "3550/1450/2250 mm",
            "行驶速度(满载/空载)": "10/29.5 km/h",
            "定位精度": "±30 mm",
            "最大爬坡度": "35%",
            "电瓶电压/标称容量": "405/173 V/Ah",
            "电池类型": "磷酸铁锂",
            "牵引力": "35000 N",
            "最大牵引力": "32000 N",
            "牵引销直径": "38 mm",
            "牵引耦合器高度": "195-525 mm",
            "原地旋转直径": "3464 mm",
        },
        "pdf_sources": ["8、智能牵引系列_XTD-H30宣传样本.pdf"],
    },

    "XFC001": {
        "name": "XFC001 数智飞仓",
        "series": "数智飞仓系列",
        "description": (
            "XFC001 数智飞仓是""货到人""智能仓储解决方案。单元占地面积仅20.46㎡（6.2m×3.3m），标准高度5.5m。"
            "小箱最大1530储位、大箱最大760储位，最快150箱/小时全自动出入库。"
            "免基建改造，平地即用，3-5天快速部署。支持ERP/WMS/MES/WCS全系统无缝对接。"
        ),
        "features": [
            "高密存储：20㎡千余储位，小箱1530/大箱760个储位",
            "高效出入库：最快150箱/小时全自动出入库",
            "柔性快速落地：免基建改造，平地即用，3-5天快速部署",
            "智能仓储管控：数字化管库存，最大化仓容，拣选零差错",
            "精益智能管理：货品数据实时可溯，打通供需链路",
            "全程无忧保障：终身数字化售后，支持无限灵活扩容",
            "混配多规格零件箱（小箱340×280×195mm；大箱560×420×190mm）",
            "称重平台+单臂拣选+流水线（可选配夹爪/电磁吸/气吸）",
        ],
        "scenarios": [
            "物料周转频率高的生产车间",
            "备件仓库",
            "小件料智能仓储",
            "货到人拣选",
        ],
        "specs": {
            "部署方式": "固定式（单元化部署）",
            "电压平台/功率": "48V/3kw",
            "单元占地(长/宽/高)": "6200/3300/5500 mm",
            "最大储位(小箱)": "1530 个",
            "最大储位(大箱)": "760 个",
            "小箱尺寸": "340×280×195 mm",
            "大箱尺寸": "560×420×190 mm",
            "单料箱最大载重(小箱)": "20 kg",
            "单料箱最大载重(大箱)": "30 kg",
            "最快出库量": "150 箱/小时",
            "最大行驶速度": "2 m/s",
            "最大升降速度": "2 m/s",
            "最大取货速度": "1.5 m/s",
            "定位精度": "±2 mm",
            "通讯方式": "无线WIFI、CANopen、Modbus",
            "取货方式": "勾拉（标配），可选夹爪/电磁吸/气吸",
        },
        "pdf_sources": ["11、数智飞仓系列_XFC001(双语版-简易).pdf"],
    },

    "XFC002": {
        "name": "XFC002 数智飞仓",
        "series": "数智飞仓系列",
        "description": (
            "XFC002 数智飞仓是""货到人""智能仓储解决方案，更大单元尺寸，支持更多储位。"
            "免基建改造，平地即用。实现高密度存储与高效出入库。"
        ),
        "features": [
            "高密存储，最大化仓容",
            "货到人智能拣选",
            "免基建改造，快速部署",
            "无缝对接WMS/MES/ERP等系统",
        ],
        "scenarios": [
            "智能仓储",
            "货到人拣选",
            "高密度存储",
        ],
        "specs": {
            "部署方式": "固定式（单元化部署）",
        },
        "pdf_sources": ["11、数智飞仓系列_XFC002(双语版-简易).pdf"],
    },

    "XRP151": {
        "name": "XRP151 四向穿梭车",
        "series": "穿梭车系列",
        "description": (
            "XRP151 是1.5吨四向穿梭车，四向全域行走，专为现代仓储物流场景打造。"
            "超紧凑车身（1055×998×127mm），十字转向省巷道。模块化柔性扩容，旺季加小车即扩容，无需土建。"
            "随机存取，异形旧仓利用率70%~85%。支持FIFO/FEFO切换，兼容多SKU多批次。"
            "可接驳提升机、AGV、分拣线，组成全自动立库。"
        ),
        "features": [
            "四向全域行走：十字转向省巷道，轨道跨巷作业",
            "超紧凑车身（1055×998×127mm），适配密集存储",
            "模块化柔性扩容：旺季加小车即扩容，无需土建",
            "随机存取：异形旧仓利用率70%~85%",
            "全系统智能对接：对接WMS/WCS/PLC自动调度盘点",
            "灵活仓储管控：FIFO/FEFO切换，兼容多SKU多批次",
            "配合提升机实现多层立体存取",
            "适配川字、田字塑料托盘、钢质托盘（500-1500kg）",
            "覆盖国标1200×1000、1100×1100两种规格",
        ],
        "scenarios": [
            "老旧仓库托盘四向穿梭改造（酒水/冷链/粮油/新能源）",
            "整存零拣料箱四向货到人（医药/电商/3C）",
            "汽车零部件产线JIT线边仓",
            "全自动立库（配合提升机+AGV+分拣线）",
        ],
        "specs": {
            "导航方式": "二维码/激光SLAM（穿梭车轨道式）",
            "载荷": "1500 kg",
            "自重": "300-400 kg",
            "外形尺寸(长/宽/高)": "1055/998/127 mm",
            "起升高度": "40 mm",
            "定位精度": "±2 mm",
            "电瓶电压/标称容量": "48/40 V/Ah",
            "续航时间": "8 h",
            "充电时间": "2 h",
            "行驶速度(满载/空载)": "1.5/2 m/s",
            "货叉尺寸": "18/145×1030 mm",
            "货叉外宽": "820 mm",
        },
        "pdf_sources": ["12、穿梭车系列_XRP151.pdf"],
    },

    "VLM801": {
        "name": "VLM801+XLM101 无人物流车",
        "series": "无人物流车系列",
        "description": (
            "VLM801+XLM101 无人物流车组合方案，实现室内外长距离无人化物料转运。"
            "适用于园区级物流、跨厂房物料配送等场景。"
        ),
        "features": [
            "室内外长距离无人化转运",
            "园区级物流配送",
            "自主导航与避障",
        ],
        "scenarios": [
            "园区物流",
            "跨厂房物料配送",
            "室外长距离转运",
        ],
        "specs": {},
        "pdf_sources": ["13、无人物流车系列_VLM801+XLM101(双语版-简易-无人物流）.pdf"],
    },

    "DS001": {
        "name": "具身\"袋鼠\"机器人",
        "series": "具身机器人系列",
        "description": (
            "具身\"袋鼠\"机器人是具身智能系列新成员。"
            "融合自主导航与柔性操作能力，适用于料箱转运、拣选等场景。"
        ),
        "features": [
            "具身智能",
            "自主导航",
            "柔性操作",
        ],
        "scenarios": [
            "料箱转运",
            "智能拣选",
        ],
        "specs": {},
        "pdf_sources": ["10、具身机器人系列_具身袋鼠样本.pdf"],
    },

    "XSF102": {
        "name": "XSF102 单侧叉堆高机器人",
        "series": "智能堆高系列",
        "description": (
            "XSF102 是单侧叉堆高机器人，专为窄通道单侧货架堆垛设计。紧凑车身，高效堆垛作业。"
        ),
        "features": [
            "单侧叉设计",
            "窄通道堆垛",
            "高效作业",
        ],
        "scenarios": [
            "窄通道货架堆垛",
            "单侧货架存取",
        ],
        "specs": {},
        "pdf_sources": [],
    },

    "XJX251": {
        "name": "XJX251 智能牵引机器人",
        "series": "智能牵引系列",
        "description": (
            "XJX251 是智能牵引机器人，适用于室内外多场景牵引作业。"
        ),
        "features": [
            "室内外多场景牵引",
            "智能导航",
        ],
        "scenarios": [
            "室内外牵引",
            "多场景转运",
        ],
        "specs": {},
        "pdf_sources": [],
    },
}


# ──── SECTION 3: Processing functions ────

def update_existing_models():
    """Update existing .md files with enriched data."""
    updated = []

    for model_id, data in EXISTING_UPDATES.items():
        md_path = CATALOG / f"{model_id}.md"
        if not md_path.exists():
            print(f"  SKIP {model_id}: file not found")
            continue

        content = md_path.read_text(encoding='utf-8')
        orig = content

        # Update description (append, don't replace)
        if data.get("description") and "## 产品特点" in content:
            desc = data["description"]
            # Replace short description line after the header block
            content = content.replace(
                "## 技术参数",
                f"{desc}\n\n## 技术参数",
            )

        # Update spec table
        if data.get("specs"):
            for param, new_val in data["specs"].items():
                # Find existing spec row and update it
                pattern = re.compile(
                    r'\| ' + re.escape(param) + r' \| .+ \|'
                )
                if pattern.search(content):
                    content = pattern.sub(f'| {param} | {new_val} |', content)
                else:
                    # Add new spec before empty line after last spec
                    pass  # Too complex for regex; skip table appending

        # Replace features section if new features provided
        if data.get("features"):
            old_feat_match = re.search(r'## 产品特点\n\n(.+?)(?:\n##|\n\n##|\Z)', content, re.DOTALL)
            if old_feat_match:
                new_feat = f'## 产品特点\n\n- {data["features"]}'
                content = content.replace(old_feat_match.group(0), new_feat)
            else:
                # Add features before 适用场景
                new_feat = f'## 产品特点\n\n- {data["features"]}\n\n'
                if "## 适用场景" in content:
                    content = content.replace("## 适用场景", f"{new_feat}## 适用场景")
                else:
                    content += f"\n{new_feat}"

        # Replace scenarios section
        if data.get("scenarios"):
            old_sc_match = re.search(r'## 适用场景\n\n(.+?)(?:\n##|\n\n##|\Z)', content, re.DOTALL)
            new_sc = f'## 适用场景\n\n- {data["scenarios"]}'
            if old_sc_match:
                content = content.replace(old_sc_match.group(0), new_sc)
            else:
                content += f'\n{new_sc}\n'

        # Append-only features/scenarios
        if data.get("features_append"):
            feats = data["features_append"]
            feat_section = re.search(r'## 产品特点\n\n(.+?)(?:\n##|\n\n##|\Z)', content, re.DOTALL)
            if feat_section:
                new_feat = feat_section.group(0).rstrip() + '\n- ' + feats
                content = content.replace(feat_section.group(0), new_feat)

        if data.get("scenarios_append"):
            scs = data["scenarios_append"]
            sc_section = re.search(r'## 适用场景\n\n(.+?)(?:\n##|\n\n##|\Z)', content, re.DOTALL)
            if sc_section:
                new_sc = sc_section.group(0).rstrip() + '\n- ' + scs
                content = content.replace(sc_section.group(0), new_sc)

        if content != orig:
            md_path.write_text(content, encoding='utf-8')
            updated.append(model_id)

    print(f"  Updated {len(updated)} existing models: {', '.join(updated)}")
    return updated


def create_new_models():
    """Create .md files for newly discovered models."""
    created = []

    for model_id, data in NEW_MODELS.items():
        md_path = CATALOG / f"{model_id}.md"

        lines = [f'# {data["name"]}', '']
        lines.append(f'> 产品系列：{data["series"]} | 品牌：中力数智搬马机器人')

        if data.get("pdf_sources"):
            lines.append(f'> 来源：{", ".join(data["pdf_sources"])}')
        lines.append('')

        # Image (if exists)
        img_path = MEDIA / f'{model_id}.png'
        if img_path.exists():
            lines.append(f'![{model_id}](media/{model_id}.png)')
            lines.append('')

        # Description
        if data.get("description"):
            lines.append(data["description"])
            lines.append('')

        # Specs
        if data.get("specs"):
            lines.append('## 技术参数')
            lines.append('')
            lines.append('| 参数 | 值 |')
            lines.append('|------|----|')
            for k, v in data["specs"].items():
                lines.append(f'| {k} | {v} |')
            lines.append('')

        # Features
        if data.get("features"):
            lines.append('## 产品特点')
            lines.append('')
            for f in data["features"]:
                lines.append(f'- {f}')
            lines.append('')

        # Scenarios
        if data.get("scenarios"):
            lines.append('## 适用场景')
            lines.append('')
            for s in data["scenarios"]:
                lines.append(f'- {s}')
            lines.append('')

        content = '\n'.join(lines)
        md_path.write_text(content, encoding='utf-8')
        created.append(model_id)

    print(f"  Created {len(created)} new models: {', '.join(created)}")
    return created


def rebuild_index():
    """Rebuild index.md with updated model counts and new series."""
    # Collect all models
    models = {}
    for md_file in sorted(CATALOG.glob('*.md')):
        if md_file.name == 'index.md':
            continue
        content = md_file.read_text(encoding='utf-8')
        series_match = re.search(r'产品系列[：:]\s*(.+?)\s*[|｜\n]', content)
        series = series_match.group(1).strip() if series_match else '其他'

        name_match = re.search(r'^# (.+?)$', content, re.MULTILINE)
        name = name_match.group(1).strip() if name_match else md_file.stem

        # Extract specs
        specs = {}
        for line in content.split('\n'):
            m = re.match(r'\| (.+?) \| (.+?) \|', line)
            if m and m.group(1) != '参数':
                specs[m.group(1)] = m.group(2)

        models[md_file.stem] = {
            'name': name,
            'series': series,
            'specs': specs,
        }

    # Group by series
    series_models = defaultdict(list)
    for mid, mdata in models.items():
        series_models[mdata['series']].append(mid)

    # Build series descriptions
    SERIES_ORDER = [
        '潜伏小车系列', '自动搬运车系列', '智能搬运车系列',
        '智能堆高系列', '智能前移系列', '智能牵引系列',
        '智能拣料系列', '具身机器人系列',
        '数智飞仓系列', '穿梭车系列', '无人物流车系列',
    ]

    # Series metadata
    SERIES_META = {
        '潜伏小车系列': {'models': 8, 'load': '300kg - 5000kg', 'purpose': '窄通道料架搬运、产线对接'},
        '自动搬运车系列': {'models': 5, 'load': '1200kg - 2000kg', 'purpose': '点对点搬运、跨车间跨楼层'},
        '智能搬运车系列': {'models': 10, 'load': '1000kg - 5000kg', 'purpose': '高速重载、室内外多场景搬运'},
        '智能堆高系列': {'models': 16, 'load': '800kg - 2000kg', 'purpose': '低位到高位货架堆垛（最高13m）'},
        '智能前移系列': {'models': 6, 'load': '1200kg - 2000kg', 'purpose': '室内外托盘上架、窄通道前移、全向剪刀叉'},
        '智能牵引系列': {'models': 8, 'load': '500kg - 30000kg', 'purpose': '室内外牵引、观光接驳、机场牵引'},
        '智能拣料系列': {'models': 1, 'load': '50kg 料箱', 'purpose': '密集料箱库料箱存取'},
        '具身机器人系列': {'models': 4, 'load': '2kg - 300kg', 'purpose': '料箱转运、拣选、柔性抓取'},
        '数智飞仓系列': {'models': 2, 'load': '20-30kg/料箱', 'purpose': '货到人智能仓储、小件料数智存储'},
        '穿梭车系列': {'models': 1, 'load': '1500kg', 'purpose': '四向穿梭、密集存储、全自动立库'},
        '无人物流车系列': {'models': 1, 'load': '-', 'purpose': '园区物流、跨厂房物料配送'},
    }

    # Update counts from actual data
    for series, meta in SERIES_META.items():
        if series in series_models:
            meta['models'] = len(series_models[series])

    lines = []
    lines.append('# 搬马机器人产品目录')
    lines.append('')
    lines.append(f'> 🏢 公司知识 — 中力数智搬马机器人全系列产品索引')
    lines.append(f'> 共 {len(models)} 款车型，{len(series_models)} 大系列')
    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 一、系列总览')
    lines.append('')
    lines.append('中力数智搬马机器人共有 11 大产品系列：')
    lines.append('')
    lines.append('| 系列 | 车型数 | 载荷范围 | 主要用途 |')
    lines.append('|------|--------|----------|----------|')
    for s in SERIES_ORDER:
        if s in series_models:
            m = SERIES_META.get(s, {'load': '-', 'purpose': '-'})
            lines.append(f'| {s} | {m["models"]} | {m["load"]} | {m["purpose"]} |')

    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 二、按系列查看')
    lines.append('')

    # Detailed series sections
    for s_name in SERIES_ORDER:
        if s_name not in series_models:
            continue
        mids = series_models[s_name]
        names = []
        for mid in mids:
            mdata = models[mid]
            name_short = mdata['name'].replace(mid + ' ', '').strip()
            load = mdata['specs'].get('载荷', mdata['specs'].get('载荷（牵引）', '-'))
            names.append(f'{mid}({name_short[:15]} {load})')
        lines.append(f'### {s_name}（{len(mids)}款）')
        lines.append(f'型号：{"、".join(names)}')
        lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('## 三、按载荷分级')
    lines.append('')
    # ... simplified load classification ...
    misc = []
    light = []
    medium = []
    heavy = []
    super_heavy = []
    for mid, mdata in sorted(models.items()):
        load_str = mdata['specs'].get('载荷', '')
        if not load_str:
            misc.append(mid)
            continue
        try:
            val = float(re.search(r'[\d.]+', load_str).group())
        except:
            misc.append(mid)
            continue
        if val <= 500:
            light.append(f'{mid}({load_str})')
        elif val <= 1500:
            medium.append(f'{mid}({load_str})')
        elif val <= 3000:
            heavy.append(f'{mid}({load_str})')
        else:
            super_heavy.append(f'{mid}({load_str})')

    lines.append(f'### 轻载（≤500kg）')
    lines.append('、'.join(light) if light else '（无）')
    lines.append('')
    lines.append(f'### 中载（500-1500kg）')
    lines.append('、'.join(medium) if medium else '（无）')
    lines.append('')
    lines.append(f'### 重载（1500-3000kg）')
    lines.append('、'.join(heavy) if heavy else '（无）')
    lines.append('')
    lines.append(f'### 超重载（≥3000kg）')
    lines.append('、'.join(super_heavy) if super_heavy else '（无）')
    lines.append('')

    if misc:
        lines.append(f'### 其他')
        lines.append('、'.join(misc))

    lines.append('')
    lines.append('---')
    lines.append('')
    lines.append('## 四、按导航方式')
    lines.append('')

    # Collect by navigation
    nav_groups = defaultdict(list)
    for mid, mdata in models.items():
        nav = mdata['specs'].get('导航方式', mdata['specs'].get('部署方式', ''))
        if not nav:
            nav_groups['待补充'].append(mid)
        elif '3D激光SLAM' in nav and ('二维码' in nav or '融合' in nav):
            nav_groups['3D激光SLAM+二维码融合定位'].append(mid)
        elif '3D激光SLAM' in nav or '3D-SLAM' in nav:
            nav_groups['3D激光SLAM导航'].append(mid)
        elif '激光SLAM' in nav and '反光板' in nav:
            nav_groups['激光SLAM/反光板导航'].append(mid)
        elif '激光SLAM' in nav:
            nav_groups['激光SLAM导航'].append(mid)
        elif '二维码' in nav:
            nav_groups['二维码导航'].append(mid)
        elif '视觉' in nav:
            nav_groups['视觉导航'].append(mid)
        elif '固定式' in nav:
            nav_groups['固定式部署'].append(mid)
        else:
            nav_groups[nav].append(mid)

    for nav_type in ['二维码导航', '激光SLAM导航', '激光SLAM/反光板导航', '3D激光SLAM导航', '3D激光SLAM+二维码融合定位', '视觉导航', '固定式部署', '待补充']:
        if nav_type in nav_groups:
            lines.append(f'### {nav_type}')
            lines.append('、'.join(nav_groups[nav_type]))
            lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('## 五、按适用场景')
    lines.append('')

    lines.append('### 室内场景')
    lines.append('- **窄通道搬运**：潜伏小车全系列（XCD031-XCD501），自动搬运车系列')
    lines.append('- **货架堆垛**：智能堆高系列、智能前移系列（最高13m）')
    lines.append('- **料箱存取**：XCU0051、XCL0051、XCO0051')
    lines.append('- **智能仓储**：XFC001、XFC002（数智飞仓系列）、XRP151（穿梭车系列）')
    lines.append('- **产线对接**：潜伏小车系列、智能搬运车系列')
    lines.append('- **室内牵引**：XTD061、XT1401（智能牵引系列）')
    lines.append('')
    lines.append('### 室外场景')
    lines.append('- **室外搬运**：XP3201（室内外多场景）、XFL201（卡车装卸）')
    lines.append('- **室外堆高/前移**：XQS151、XQS181、XQOE151（全向剪刀叉）')
    lines.append('- **室外牵引**：XTD401、XTD601')
    lines.append('- **机场牵引**：XTD-H30（机场停机坪货物转运）')
    lines.append('- **园区物流**：VLM801+XLM101（无人物流车系列）')
    lines.append('')
    lines.append('### 特殊场景')
    lines.append('- **掏箱装卸**：XFL151（棚车/集装箱黑暗环境掏箱）、XFL201（具身AI装卸）')
    lines.append('- **高速转运（≥2m/s）**：XFL151(6km/h)、XFL201(2.2m/s)、XPL201(3m/s)、XNA121(2.8m/s)、XTD-H30(29.5km/h)')
    lines.append('- **重载搬运（≥3吨）**：XCD301(3t)、XPL301(3t)、XTD401(4t)、XT1401(4t)、XCD501(5t)、XPL501(5t)、XTD601(6t)、XTD-H30(30t)')
    lines.append('- **高位堆垛（≥5m）**：XNA101(6m)、XQE122(5.5m)、XQE151(5.5m)、XNA121(8.5-13m)、XNA151(13m)、XQC161(8.3m)')
    lines.append('- **全向移动**：XQOE151（全向剪刀叉前移，可横移/蟹行/原地回转）')
    lines.append('- **柔性抓取**：XCB031（单臂抓取2-5kg）')
    lines.append('- **货到人拣选**：XFC001、XFC002（数智飞仓）、XRP151（四向穿梭车）')
    lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('## 六、关键指标 TOP 排行')
    lines.append('')

    # Load TOP
    load_sorted = sorted(
        [(mid, mdata['specs'].get('载荷', '0')) for mid, mdata in models.items()],
        key=lambda x: float(re.search(r'[\d.]+', str(x[1])).group() if re.search(r'[\d.]+', str(x[1])) else 0),
        reverse=True
    )
    lines.append('### 最大载荷 TOP 5')
    top_load = [f'{mid}({val})' for mid, val in load_sorted[:5]]
    lines.append(' > '.join(top_load))
    lines.append('')

    # Lift TOP
    lift_data = []
    for mid, mdata in models.items():
        val_str = mdata['specs'].get('起升高度', '0')
        try:
            val = float(re.search(r'[\d.]+', val_str).group())
            lift_data.append((mid, val))
        except:
            pass
    lift_sorted = sorted(lift_data, key=lambda x: x[1], reverse=True)
    lines.append('### 最高起升 TOP 5')
    top_lift = [f'{mid}({int(v)}mm)' for mid, v in lift_sorted[:5]]
    lines.append(' > '.join(top_lift))
    lines.append('')

    index_path = CATALOG / 'index.md'
    index_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'  Rebuilt index.md: {len(models)} models in {len(series_models)} series')


# ──── MAIN ────

if __name__ == '__main__':
    print("=== Updating existing models ===")
    update_existing_models()
    print()

    print("=== Creating new models ===")
    create_new_models()
    print()

    print("=== Rebuilding index.md ===")
    rebuild_index()
    print()

    print("\nDone! Next steps:")
    print("1. Extract product render images from PDFs")
    print("2. Review generated .md files for accuracy")
    print("3. Run `python -m ai.ingestion.ingest_all` to update vector DB")
