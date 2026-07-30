"""Supplement product catalog with new 机器人产品增项 PDF data — v4."""
import re
from pathlib import Path

CATALOG = Path(r"D:\Code\OpenRobotService_Data\kb\company\product_catalog")

def read_md(mid):
    md = CATALOG / f"{mid}.md"
    if not md.exists():
        return None
    return md.read_text(encoding="utf-8").replace("\r\n", "\n")

def write_md(mid, content):
    md = CATALOG / f"{mid}.md"
    md.write_text(content, encoding="utf-8")

def replace_section(content, header, bullets):
    """Replace a ## Section with new bullet content."""
    bullet_text = "\n".join(f"- {b}" for b in bullets)
    section = f"## {header}\n\n{bullet_text}"

    pattern = rf"(?:^|\n)## {header}\n\n.*?(?=\n##|\Z)"
    if re.search(pattern, content, re.DOTALL):
        new = re.sub(pattern, f"\n{section}", content, count=1, flags=re.DOTALL)
        return new
    else:
        # Append before next ## or at end
        return content.rstrip() + f"\n\n{section}\n"

def replace_spec_values(content, updates):
    """Update spec table values."""
    for param, new_val in updates.items():
        pattern = rf"(\| {re.escape(param)} \| ).*?( \|)"
        if re.search(pattern, content):
            content = re.sub(pattern, rf"\g<1>{new_val}\g<2>", content)
    return content

def insert_description(content, desc):
    """Insert description between image and ## 技术参数."""
    # Remove existing description
    content = re.sub(r'(!\[.*?\]\(.*?\))\n\n.*?\n\n(## 技术参数)', r'\1\n\n\2', content, flags=re.DOTALL)
    # Insert new
    content = content.replace(
        f"## 技术参数",
        f"{desc}\n\n## 技术参数"
    )
    return content

def fix_duplicates(content):
    """Fix duplicate/conjoined content in features and scenarios sections."""
    # XS2201 specific: has massive duplicates from corrupted section parsing
    # Rebuild features and scenarios cleanly
    lines = content.split("\n")
    cleaned = []
    seen_features = set()
    seen_scenarios = set()
    in_features = False
    in_scenarios = False

    for line in lines:
        if line.startswith("## 产品特点"):
            in_features = True
            in_scenarios = False
            cleaned.append(line)
            cleaned.append("")
            continue
        elif line.startswith("## 适用场景"):
            in_features = False
            in_scenarios = True
            cleaned.append(line)
            cleaned.append("")
            continue
        elif line.startswith("## "):
            in_features = False
            in_scenarios = False
            cleaned.append(line)
            continue

        if in_features and line.startswith("- "):
            stripped = line[2:].strip()
            if stripped and stripped not in seen_features:
                seen_features.add(stripped)
                cleaned.append(line)
            continue

        if in_scenarios and line.startswith("- "):
            stripped = line[2:].strip()
            if stripped and stripped not in seen_scenarios:
                seen_scenarios.add(stripped)
                cleaned.append(line)
            continue

        if not (in_features or in_scenarios):
            cleaned.append(line)

    return "\n".join(cleaned)

# ============================================================
# UPDATE DATA from 机器人产品增项 PDFs
# ============================================================

UPDATES = {
    "XCD101": {
        "description": "XCD101 是1.0吨标准化嵌入式顶升机器人。车身小、可原地旋转，适合车间窄通道料架搬运。适配川字托盘、田字托盘等多种载具，支持自动化设备对接、点对点长距离运输和跨楼层作业。",
        "features": [
            "更窄车身：车身小、可原地旋转，适合车间窄通道料架搬运",
            "1000kg 额定载荷，满足中重型物料搬运需求",
            "±10mm 定位精度，作业全程稳定可靠",
            "8小时续航：48V/40Ah 磷酸铁锂电池，满足全天候作业",
            "自动化对接：可对接机械臂、电梯、自动门、输送线等多种自动化设备",
            "点对点长距离运输：支持多点到多点灵活调度",
            "跨楼层作业：支持电梯对接，实现跨楼层物料转运",
            "多载具适配：适配川字托盘、田字托盘等多种标准载具",
        ],
        "scenarios": [
            "窄通道料架搬运",
            "生产车间自动化设备对接",
            "点对点长距离物料运输",
            "跨楼层物料转运",
            "多点对多点灵活调度",
        ],
        "specs": {},  # Specs already accurate
    },
    "XCD061": {
        "description": "XCD061 是600kg潜伏顶升搬运机器人。采用二维码/激光SLAM导航，车身紧凑（990×650×250mm），自重仅200kg，适合点对点长距离搬运和自动化设备对接场景。",
        "features": [
            "600kg 额定载荷，紧凑车身仅990mm长",
            "二维码/激光SLAM 双模导航，灵活适配不同场景",
            "±10mm 定位精度，作业稳定可靠",
            "最高速度2m/s（空载），搬运效率高",
            "点对点长距离搬运输送",
            "可对接自动化设备（机械臂、输送线等）",
        ],
        "scenarios": [
            "点对点长距离搬运",
            "对接自动化设备",
            "窄通道料架搬运",
        ],
        "specs": {
            "电瓶电压/标称容量": "48/32 V/Ah",
        },
    },
    "XCD151": {
        "description": "XCD151 是1.5吨潜伏顶升搬运机器人。采用二维码/激光SLAM双模导航，额定载荷1500kg，起升高度60mm，适合重载长距离搬运和自动化产线对接。",
        "features": [
            "1500kg 大载荷，满足重型物料搬运需求",
            "二维码/激光SLAM 双模导航，灵活适配",
            "±10mm 定位精度，作业精准可靠",
            "紧凑车身仅1185mm长，适合窄通道作业",
            "可对接机械臂、输送线、电梯等自动化设备",
        ],
        "scenarios": [
            "重载长距离搬运",
            "自动化产线对接",
            "多点对多点物料运输",
        ],
        "specs": {
            "电瓶电压/标称容量": "48/40 V/Ah",
        },
    },
    "EXP15": {
        "description": "EXP15 是1.5吨极简自动搬运车（昵称'小马'）。开箱即用，智能语音指导跟学操作，无需WiFi部署实施快。采用视觉导航+反光膜，支持跨车间转运，可换电池保证长时间工作，完全代替人工实现长距离自动搬运。",
        "features": [
            "开箱即用：智能语音指导，跟学式操作，简单高效",
            "无需WiFi：内部LoRa通讯，部署实施快",
            "电子转向：搬运更轻松更高效，运行速度1.2m/s",
            "可换电池：24V/60Ah磷酸铁锂电池，3-4小时续航，换电即用",
            "跨车间转运：支持室外跨车间长距离搬运",
            "双重避障雷达+双重急停开关+叉尖光电，多重保护人货安全",
            "视觉导航+反光膜导航，稳定可靠",
            "一车多任务+库位检测+多车交管",
            "手柄下压快速切换电动手动模式",
        ],
        "scenarios": [
            "电器制造行业长距离搬运",
            "智能制造产线对接",
            "新材料行业物料转运",
            "机械制造车间内部物流",
            "数控加工设备物料配送",
            "电商仓储物流搬运",
            "跨车间长距离自动搬运",
        ],
        "specs": {
            "整车长/宽/高": "1690/636(650)(700)/1420 mm",
        },
    },
    "XP1151": {
        "description": "XP1151 是1.5吨点对点智能搬运机器人。采用激光SLAM/反光板导航，支持可换电池，专为点对点长距离搬运、电梯跨楼层作业和大面积平面库自动出入库设计。",
        "features": [
            "1500kg 额定载荷，满足中重型物料搬运",
            "激光SLAM/反光板双模导航，定位精度±10mm",
            "可换电池设计，24V/60Ah磷酸铁锂电池",
            "点对点长距离搬运：适合大面积平面库",
            "跨楼层作业：支持电梯对接，实现多层物料转运",
            "紧凑车身（1635×812×2118mm），灵活穿行",
        ],
        "scenarios": [
            "点对点长距离搬运",
            "电梯跨楼层作业",
            "大面积平面库自动出入库",
        ],
        "specs": {},
    },
    "XPL201P": {
        "description": "XPL201P 是2.0吨物流专用高速搬运机器人。更高速度、更大载重，满载速度1.5m/s、空载3.0m/s，专为物流园区、大型仓库等重载长距离场景设计。",
        "features": [
            "2000kg 重载+3.0m/s高速（空载），效率远超常规搬运车",
            "激光SLAM/反光板导航，定位精度±10mm",
            "24V/150Ah大容量电池，满足长时间高强度作业",
            "物流专用设计：更高速度、更大载重的长距离智能搬运",
        ],
        "scenarios": [
            "物流园区长距离重载搬运",
            "大型仓库高速转运",
            "跨车间重载物料配送",
        ],
        "specs": {
            "行驶速度(满载/空载)": "1.5/3 m/s",
            "电瓶电压/标称容量": "24/150 V/Ah",
        },
    },
    "XPL201T": {
        "description": "XPL201T 是薄背物流专用搬运机器人，专为重载长距离搬运场景设计。采用激光SLAM/反光板导航，2吨额定载荷。",
        "features": [
            "2000kg 重载能力，满足物流行业高强度需求",
            "激光SLAM/反光板导航，作业精准可靠",
            "薄背设计：车身紧凑，适合窄通道作业",
            "重载长距离搬运：专为物流场景优化",
        ],
        "scenarios": [
            "重载长距离搬运",
            "物流配送中心转运",
            "跨车间物料运输",
        ],
        "specs": {},
    },
    "XPL201": {
        "description": "XPL201 是2.0吨高速重载智能搬运机器人。采用激光SLAM/反光板导航，2000kg额定载荷，速度最高3.0m/s（空载），专为重载长距离搬运场景打造。",
        "features": [
            "2000kg 重载能力+最高3.0m/s速度，兼顾效率与承载",
            "激光SLAM/反光板导航，定位精度±10mm",
            "高速重载设计，专为重载长距离搬运场景打造",
        ],
        "scenarios": [
            "重载长距离搬运",
            "物流园区转运配送",
        ],
        "specs": {},
    },
    "XS1151": {
        "description": "XS1151 是1.5吨薄背堆高机器人，专为窄通道多层纵深堆高设计。激光SLAM/反光板导航，起升高度1700mm，支持点对点和列对列任务，可对接WMS/MES/ERP等管理系统。",
        "features": [
            "窄通道多层纵深堆高：专为高位货架存取设计",
            "激光SLAM/反光板双模导航，定位精度±10mm",
            "3D避障相机+底部270°双激光+侧面超声，全方位安全防护",
            "任务类型丰富：点对点、点对列、列对点、列对列",
            "丰富设备对接：自动门、输送线、码垛机、机械臂、升降机",
            "系统对接：支持WMS、MES、ERP及定制系统",
            "5-6小时续航：24V/135Ah磷酸铁锂电池",
            "语音提醒+弧形灯+转向灯，声光双重提示",
            "触摸屏交互（可选呼叫盒/PAD/手机/电脑/空中相机）",
            "无线局域网交管+多车交管（可选5G网络交管）",
        ],
        "scenarios": [
            "窄通道多层纵深堆高",
            "高位货架存取",
            "货物长距离转运",
            "对接自动化设备和系统",
        ],
        "specs": {
            "外形尺寸(长/宽/高)": "1716/900/2177 mm",
            "电瓶电压/标称容量": "24/135 V/Ah",
        },
    },
    "XS1152": {
        "description": "XS1152 是1.5吨薄背堆高机器人，采用激光SLAM/反光板导航，起升高度1600-2500mm，专为窄通道多层货架堆高和货物长距离运输设计。",
        "features": [
            "1500kg 额定载荷，满足货架堆垛需求",
            "激光SLAM/反光板双模导航",
            "起升高度1600-2500mm，适配多层货架",
            "窄通道设计：适应多层货架密集仓储",
            "±10mm 定位精度",
        ],
        "scenarios": [
            "窄通道多层货架堆高",
            "货物长距离运输",
            "密集仓储出入库",
        ],
        "specs": {
            "外形尺寸(长/宽/高)": "1700/1004/2180 mm",
            "电瓶电压/标称容量": "24/205 V/Ah",
        },
    },
    "XS1161": {
        "description": "XS1161（新产品型号 XS1201）是2.0吨超薄托盘堆垛机器人。3D激光SLAM导航，起升高度可达4470mm（最高约6m），专为高位货架堆垛和仓库生产货物转运设计。",
        "features": [
            "2000kg 额定载荷，最大起升4470mm（约6m高位货架）",
            "3D激光SLAM导航，室内外通用",
            "超薄托盘设计，适应超薄托盘密集存储",
            "顶部相机+补盲激光+两侧避障激光，全方位安全防护",
            "库区功能丰富：顺序卸货/取货、同时取卸、空满显示、库区筛选、单库位清空/置满、整库区清库/满库",
            "可对接机械臂、升降机、自动门、输送线等自动化设备",
            "系统对接：支持WMS/MES/ERP及定制系统",
            "语音提醒+转向灯+区域警示灯，多重声光提示",
            "24V/280Ah大容量磷酸铁锂电池",
            "3kW驱动+4.5kW起升电机，动力强劲",
        ],
        "scenarios": [
            "高位货架堆垛（最高6m）",
            "仓库、生产货物转运",
            "超薄托盘密集存储",
            "对接立库、输送线等自动化设备",
        ],
        "specs": {
            "外形尺寸(长/宽/高)": "1819/1046/4945 mm",
            "起升高度": "4470 mm（最高约6000mm）",
            "电瓶电压/标称容量": "24/280 V/Ah",
        },
    },
    "XS2201": {
        "description": "XS2201 是2.0吨重载堆高机器人（含 XS2201C 等子型号）。激光SLAM/反光板导航，起升高度3000mm，8-9小时超长续航，专为窄通道重载货架堆垛设计，可对接多种自动化设备和系统。",
        "features": [
            "2000kg 重载能力，激光SLAM/反光板导航",
            "起升高度3000mm，适配高层货架密集仓储",
            "3D避障相机+底部270°避障激光+侧面超声，全方位安全防护",
            "8-9小时超长续航：24V/205Ah磷酸铁锂电池",
            "窄通道重载堆高：直角转弯通道仅需2.6m",
            "可对接自动门、输送线、码垛机、机械臂、升降机",
            "系统对接：支持WMS、MES、ERP及定制系统",
            "语音提醒+蓝光灯+转向灯，声光双重提示",
        ],
        "scenarios": [
            "窄通道重载堆高",
            "货架堆垛存取",
            "多载具适配（托盘、料笼等）",
            "对接自动化设备和管理系统",
        ],
        "specs": {},
    },
    "XSC081": {
        "description": "XSC081 是800kg平衡重式堆高机器人。3D激光SLAM导航，起升高度3000mm，车身小直角堆垛通道仅2.9m。附带手动操作模式可随时切换为常规叉车作业，适配田字托盘、川字托盘等多种载具。",
        "features": [
            "800kg 额定载荷，起升高度3000mm",
            "3D激光SLAM导航，室内外通用，导航精度±10mm",
            "车身紧凑：直角堆垛通道宽度仅需2.9m",
            "附带手动操作模式，可随时切换常规叉车作业",
            "补盲激光+两侧避障激光+叉尖光电+叉根激光+叉根碰撞，全方位安全防护",
            "库区功能：顺序卸货/取货、同时取卸、空满显示、库区筛选、单库位清空/置满、整库区清库/满库",
            "可对接机械臂、升降机、自动门、输送线",
            "系统对接：支持WMS、MES、ERP及定制系统",
            "语音提醒+一字灯+转向灯，多重声光提示",
            "适配田字托盘、川字托盘、料笼等多种载具",
        ],
        "scenarios": [
            "货架堆垛作业",
            "货物室内外长距离运输",
            "对接立库、输送线等自动化设备",
        ],
        "specs": {
            "外形尺寸(长/宽/高)": "2285/970/2482 mm",
            "电瓶电压/标称容量": "24/205 V/Ah",
        },
    },
    "XSC151": {
        "description": "XSC151 是1.5吨平衡重式堆高机器人。3D激光SLAM导航室内外通用，最高6m起升高度，车身紧凑直角堆垛仅2.9m。标配手动操作模式可随时切换，适配田字/川字托盘、料笼、汽车专用料架等多种载具，专为高位密集仓储和集装箱装卸场景打造。",
        "features": [
            "1500kg 额定载荷，最大起升高度6m（门架3129mm）",
            "3D激光SLAM导航，室内外通用，导航精度±10mm",
            "极强通过性：直角堆垛通道宽度仅需2.9m",
            "高精度定位：导航精度±10mm，作业全程稳定可靠",
            "附带手动操作模式，可随时切换为常规叉车作业",
            "补盲激光+两侧避障激光+叉尖光电+叉根激光+叉根碰撞，全方位安全",
            "库区功能：顺序卸货/取货、同时取卸、空满显示、库区筛选",
            "可对接机械臂、升降机、自动门、输送线",
            "系统对接：支持WMS、MES、ERP及定制系统",
        ],
        "scenarios": [
            "高位货架堆垛作业（最高6m）",
            "室内外长距离货物转运",
            "对接立库、输送线等自动化设备",
            "全天候24小时自动化运行",
            "集装箱装卸",
        ],
        "specs": {
            "外形尺寸(长/宽/高)": "2407/1050/3129 mm",
            "起升高度": "6000 mm",
            "电瓶电压/标称容量": "48/205 V/Ah",
        },
    },
    "XSC201": {
        "description": "XSC201 是2.0吨平衡重式堆高机器人。3D激光SLAM导航室内外通用，最高6m起升高度，车身紧凑直角堆垛仅3.1m。标配手动操作模式可随时切换，专为高位密集仓储和集装箱装卸场景打造。",
        "features": [
            "2000kg 重载，最大起升高度6m（门架3129mm）",
            "3D激光SLAM导航，室内外通用，导航精度±10mm",
            "极强通过性：直角堆垛通道宽度仅需3.1m",
            "高精度定位：导航精度±10mm，作业全程稳定可靠",
            "附带手动操作模式，可随时切换为常规叉车作业",
            "补盲激光+两侧避障激光+叉尖光电+叉根激光+叉根碰撞，全方位安全",
            "库区功能：顺序卸货/取货、同时取卸、空满显示、库区筛选",
            "可对接机械臂、升降机、自动门、输送线",
            "系统对接：支持WMS、MES、ERP及定制系统",
        ],
        "scenarios": [
            "高位货架堆垛作业（最高6m）",
            "室内外长距离货物转运",
            "对接立库、输送线等自动化设备",
            "全天候24小时自动化运行",
            "集装箱装卸",
        ],
        "specs": {
            "外形尺寸(长/宽/高)": "2579/1050/3129 mm",
            "起升高度": "6000 mm",
            "电瓶电压/标称容量": "48/205 V/Ah",
        },
    },
}

def main():
    updated = 0
    for mid, data in UPDATES.items():
        content = read_md(mid)
        if content is None:
            print(f"  SKIP {mid}: file not found")
            continue

        original = content
        content = fix_duplicates(content)  # Clean up any existing corruption

        # Insert description
        if "description" in data and data["description"]:
            content = insert_description(content, data["description"])

        # Replace features
        if "features" in data and data["features"]:
            content = replace_section(content, "产品特点", data["features"])

        # Replace scenarios
        if "scenarios" in data and data["scenarios"]:
            content = replace_section(content, "适用场景", data["scenarios"])

        # Update specs
        if "specs" in data and data["specs"]:
            content = replace_spec_values(content, data["specs"])

        # Dedup consecutive duplicate lines
        lines = content.split("\n")
        deduped = []
        for line in lines:
            if line.strip() and len(deduped) >= 1 and line == deduped[-1]:
                continue
            deduped.append(line)
        content = "\n".join(deduped)

        # Fix double-dash bullets
        content = re.sub(r'^- - ', '- ', content, flags=re.MULTILINE)

        # Ensure blank lines before headers
        content = re.sub(r'([^\n])\n## ', r'\1\n\n## ', content)

        if content != original:
            write_md(mid, content)
            print(f"  UPDATED {mid}")
            updated += 1
        else:
            print(f"  NOCHANGE {mid}")

    print(f"\nTotal updated: {updated}/{len(UPDATES)}")

if __name__ == "__main__":
    main()
