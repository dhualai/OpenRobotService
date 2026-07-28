"""
Supplement product_catalog/*.md with data from 机器人产品/*.pdf

Steps:
1. Read all extracted PDF texts
2. Parse model data (descriptions, features, scenarios, specs)
3. Match to existing .md files → enrich
4. Create new .md files for newly discovered models
5. Extract product images from PDFs
6. Rebuild index.md
"""
import fitz
import os, re, json
from pathlib import Path
from collections import defaultdict

BASE = Path(r"D:\Code\OpenRobotService_Data\机器人产品\机器人产品")
CATALOG = Path(r"D:\Code\OpenRobotService_Data\kb\company\product_catalog")
MEDIA = CATALOG / "media"
TEXT_DIR = Path(r"C:\Users\PAJ26020\.claude\tmp_robot_texts")

# ── Model knowledge extracted from PDFs ──

# Series-level descriptions (from series brochures)
SERIES_DESC = {}

# Per-model enrichment data
MODEL_DATA = {}  # model -> {description, features, scenarios, specs: {param: value}}

# New models to create (not in existing catalog)
NEW_MODELS = {}

# ── Utils ──

def clean_text(text):
    """Clean extracted PDF text."""
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text

def parse_spec_table(text, model_id):
    """Attempt to extract spec key-value pairs from text."""
    specs = {}
    # Common patterns
    patterns = [
        (r'载荷[（(]Load capacity[)）]?\s*[：:]?\s*(\d+)', 'load_capacity_kg'),
        (r'导航方式[（(]Navigation[)）]?\s*[：:]?\s*(.+?)[\s，,]', 'navigation'),
        (r'定位精度[（(]Positioning accuracy[)）]?\s*[：:]?\s*[±]?(\d+)', 'positioning_mm'),
        (r'起升高度[（(]Lift[^)）]*[)）]?\s*[：:]?\s*(\d+)', 'lift_height_mm'),
        (r'行驶速度[^，]*[满载/空载]?\s*[：:]?\s*([\d.]+)[/／]([\d.]+)', 'speed_ms'),
        (r'自重[（(]Service[^)）]*[)）]?\s*[：:]?\s*(\d+)', 'weight_kg'),
    ]
    return specs

# ── Parse series brochures ──

def parse_qianfu_daquan(text):
    """潜伏小车大全.pdf — series-level data for 潜伏顶升系列"""
    SERIES_DESC['潜伏小车系列'] = (
        "中力数智潜伏小车系列，工作方式为潜伏到料架底部，利用顶升机构将物料架顶起后搬运物料。"
        "车体小、可360°旋转，极大提高搬运效率和空间利用率。"
        "专为高效、灵活的物料搬运而设计，适用于各种工业场景，助力企业实现智能化升级。"
    )

    # Series features
    features = [
        "智能导航：支持激光导航、二维码导航，精准定位，灵活避障，适应复杂环境",
        "车体优秀：车身低矮，可潜入货架底部实现货物自动搬运，节省空间，提高效率",
        "安全可靠：配备如激光避障、紧急停止等多重安全防护设备，确保人机安全",
        "强劲动力：搭载高性能电机和驱动系统，爬坡能力强，运行平稳，满足不同负载需求",
        "多种高精度导航方式：激光SLAM、二维码，适应不同环境，精度可达±10mm",
        "防护性高：激光避障、激光触边、避障相机、避障超声雷达，急停开关及料架检测功能多重防护",
        "多种呼叫方式：PAD、手机、电脑和按钮盒，随时随地呼叫",
        "丰富接口：可对接机械臂，电梯和自动门等自动化设备，可接入ERP、MES、WCS等管理系统",
        "电池快换功能：便捷更换电池，保障设备长时间连续工作",
    ]

    scenarios = [
        "窄通道物料搬运：车身低矮，可原地旋转，灵活穿梭于窄通道料架之间，实现货物的灵活出入库",
        "多点对多点长距离物料运输：配备智能导航系统，可自主规划最优路径，实现多点对多点之间的物料自动运输",
        "对接机械臂、自动门等自动化设备：支持与机械臂、自动门等自动化设备无缝对接，实现物料搬运的全流程自动化",
    ]

    for model in ['XCD031', 'XCD061', 'XCD101', 'XCD151']:
        MODEL_DATA[model] = MODEL_DATA.get(model, {})
        MODEL_DATA[model]['series_features'] = features
        MODEL_DATA[model]['series_scenarios'] = scenarios

def parse_xsc_yangce(text):
    """XSC系列样册 — XSC series enriched data"""
    SERIES_DESC['XSC系列'] = (
        "XSC系列平衡重式堆高机器人，专为窄通道高位货架堆垛设计。"
        "最小直角堆垛通道宽度仅2.9m，支持手动/自动模式切换，可替代叉车作业。"
        "多重防护手段保障人机作业安全，适配托盘、料架等多种载具。"
    )

    features = [
        "最小直角堆垛通道宽度仅2.9m",
        "手动/自动模式自由切换，可替代叉车作业",
        "多重防护手段保障作业安全",
        "适配托盘、料架等多种载具",
        "支持3D激光SLAM导航，精度高",
        "配备前盲区补偿、2D视觉识别等多重传感器",
        "可选配机械臂、输送线、自动门等外设对接",
        "支持WMS、MES、ERP等系统对接",
    ]

    scenarios = [
        "智能堆垛：窄通道高位货架堆垛作业",
        "长距离搬运：跨区域长距离物料搬运",
        "自动化设备对接：对接输送线、机械臂等自动化设备",
    ]

    for model in ['XSC081', 'XSC121', 'XSC151', 'XSC201']:
        MODEL_DATA[model] = MODEL_DATA.get(model, {})
        MODEL_DATA[model]['series_features'] = features
        MODEL_DATA[model]['series_scenarios'] = scenarios

def parse_xp_xs_series(text):
    """XP1152/XP1201/XS1152/XS1201 series data"""
    # This PDF introduces XS1201 (not XS2201!) as a 2.0T 薄背堆高
    MODEL_DATA['XP1152'] = MODEL_DATA.get('XP1152', {})
    MODEL_DATA['XP1152']['series_desc'] = "智能搬运车系列，专为窄通道平面搬运设计，单侧取卸货通道宽度仅2.3m"

    MODEL_DATA['XP1201'] = MODEL_DATA.get('XP1201', {})
    MODEL_DATA['XP1201']['series_desc'] = "2.0T智能搬运机器人，激光SLAM/反光板导航"

    MODEL_DATA['XS1152'] = MODEL_DATA.get('XS1152', {})
    MODEL_DATA['XS1152']['series_desc'] = "1.5T薄背堆高机器人，起升高度2.5m，专为窄通道货架堆垛设计"

    # XS1201 is a NEW model (different from XS2201!)
    NEW_MODELS['XS1201'] = {
        'name': 'XS1201 薄背堆高机器人',
        'series': '智能堆高系列',
        'description': '2.0T薄背堆高机器人，起升高度4.5m，激光SLAM导航，专为窄通道高位货架堆垛设计。单侧取卸货通道宽度仅2.4m，可对接MES、WMS等管理系统及输送线等自动化设备。',
        'features': [
            '起升高度4.5m，适配高位货架',
            '激光SLAM导航，精准定位±10mm',
            '单侧取卸货通道宽度仅2.4m',
            '自动/手动双模式',
            '顶部向下避障相机+底部双避障激光',
            '叉尖光电+双到位检测',
            '可对接MES、WMS、输送线等',
        ],
        'scenarios': ['窄通道货架堆垛', '高位货架存取', '输送线对接搬运'],
        'specs': {
            '导航方式': '激光SLAM',
            '载荷': '2000 kg',
            '自重': '1070 kg',
            '外形尺寸(长/宽/高)': '1819/1046/2040 mm',
            '起升高度': '4500 mm',
            '定位精度': '±10 mm',
            '电瓶电压/标称容量': '24/280 V/Ah',
            '续航时间': '6-8 h',
            '行驶速度(满载/空载)': '1.6/1.5 m/s',
            '载荷中心距': '600 mm',
            '货叉尺寸': '60/170/1220 mm',
            '货叉外宽': '630 mm',
            '转弯半径': '1338 mm',
            '电池类型': '磷酸铁锂',
        },
    }

# ── Parse individual model PDFs ──

def parse_individual_pdfs():
    """Parse individual model PDFs for additional data."""

    # XFL151E (XFL151) — NEW model: 1.5T 平衡重式机器人
    NEW_MODELS['XFL151'] = {
        'name': 'XFL151 平衡重式机器人',
        'series': '智能堆高系列',
        'description': (
            '1.5吨平衡重式机器人，专为棚车/集装箱/卡车掏箱装卸设计。'
            '引入3D激光雷达深度识别，不依赖光照，在箱内黑暗环境下仍能自动识别托盘高度及姿态信息，实现居中叉取。'
            '配备折叠货叉，结构更紧凑、车身更小巧。一体式多功能铸造车架，空间利用率高、结构强度高。'
        ),
        'features': [
            '3D激光雷达深度识别，适应黑暗环境掏箱作业',
            '折叠货叉：结构紧凑，车身小巧，实时监测货叉状态',
            '多传感器布局：顶部激光SLAM+补盲激光+底部双激光+侧面超声',
            '一体式多功能铸造车架：高空间利用率、高强度、散热优化',
            '最大行驶速度6/8 km/h（满载/空载）',
            '支持自动充电、5G通讯',
            '可对接自动门、输送线、码垛机、机械臂、升降机',
            '支持WMS、MES、ERP系统对接',
        ],
        'scenarios': [
            '棚车掏箱装卸',
            '集装箱掏箱装卸',
            '卡车尾箱掏箱装卸',
            '窄通道平衡重堆垛',
        ],
        'specs': {
            '导航方式': '3D激光SLAM',
            '载荷': '1500 kg',
            '自重': '4047 kg',
            '外形尺寸(长/宽/高)': '2567/1084/2158 mm',
            '起升高度': '1200 mm',
            '定位精度': '±30 mm',
            '电瓶电压/标称容量': '80/280 V/Ah',
            '续航时间': '8-10 h',
            '行驶速度(满载/空载)': '6/8 km/h',
            '电池类型': '磷酸铁锂',
            '转弯半径': '1140 mm',
            '驱动方式': '前置双驱',
            '货叉尺寸': '40/100/1125 mm',
            '货叉外宽': '200-770 mm',
        },
    }

    # XQOE151 — NEW model
    NEW_MODELS['XQOE151'] = {
        'name': 'XQOE151 室外前移式机器人',
        'series': '智能前移系列',
        'description': (
            '室外前移式机器人，专为室外场景的托盘上架和堆垛作业设计。'
            '采用3D激光SLAM导航，适应室外复杂光照和路面条件。'
        ),
        'features': [
            '3D激光SLAM导航，适应室外环境',
            '前移式门架设计，窄通道作业',
            '适合室外托盘上架和堆垛',
        ],
        'scenarios': ['室外托盘上架', '室外货架堆垛', '室外搬运'],
        'specs': {
            '导航方式': '3D激光SLAM',
        },
    }

    # XTD061 — NEW model
    NEW_MODELS['XTD061'] = {
        'name': 'XTD061 智能牵引机器人',
        'series': '智能牵引系列',
        'description': (
            '0.6吨智能牵引机器人，专为室内低成本牵引作业设计。'
            '紧凑车身，灵活调度，适合多车串联作业。'
        ),
        'features': [
            '紧凑车身设计',
            '支持多车串联',
            '低成本牵引解决方案',
        ],
        'scenarios': ['室内牵引', '多车串联转运'],
        'specs': {
            '导航方式': '激光SLAM',
            '载荷': '600 kg (牵引)',
        },
    }

    # XT1401 — NEW model
    NEW_MODELS['XT1401'] = {
        'name': 'XT1401 智能牵引机器人',
        'series': '智能牵引系列',
        'description': '1.4吨智能牵引机器人，适用于室外长距离牵引作业。',
        'features': ['室外长距离牵引', '大牵引力'],
        'scenarios': ['室外牵引', '长距离转运'],
        'specs': {
            '载荷': '1400 kg (牵引)',
        },
    }

    # XJX251 — NEW model (has render PNG)
    NEW_MODELS['XJX251'] = {
        'name': 'XJX251 智能牵引机器人',
        'series': '智能牵引系列',
        'description': '智能牵引机器人，适用于室内外多场景牵引作业。',
        'features': ['室内外多场景牵引'],
        'scenarios': ['室内外牵引', '多场景转运'],
        'specs': {},
    }

    # XTD-H30 — NEW model
    NEW_MODELS['XTDH30'] = {
        'name': 'XTD-H30 智能牵引机器人',
        'series': '智能牵引系列',
        'description': '大吨位智能牵引机器人，适用于重型牵引作业场景。',
        'features': ['重型牵引', '大牵引载荷'],
        'scenarios': ['重型牵引', '室外大吨位转运'],
        'specs': {},
    }

    # XFC001 — NEW SERIES
    NEW_MODELS['XFC001'] = {
        'name': 'XFC001 数智飞仓',
        'series': '数智飞仓系列',
        'description': (
            '数智飞仓是"货到人"智能仓储解决方案。单元占地面积仅20.46㎡（6.2m×3.3m），标准高度5.5m。'
            '小箱最大1530储位、大箱最大760储位，最快出入库150箱/小时。'
            '免基建改造，平地即用，3-5天快速部署。支持ERP/WMS/MES/WCS全系统无缝对接。'
        ),
        'features': [
            '高密存储：20㎡千余储位，小箱1530/大箱760个储位',
            '高效出入库：最快150箱/小时全自动出入库',
            '柔性快速落地：免基建改造，平地即用，3-5天快速部署',
            '智能仓储管控：数字化管库存，最大化仓容，拣选零差错',
            '精益智能管理：货品数据实时可溯，打通供需链路',
            '全程无忧保障：终身数字化售后，支持无限灵活扩容',
            '混配多规格零件箱（小箱340×280×195mm；大箱560×420×190mm）',
            '称重平台+单臂拣选+流水线（可选配夹爪/电磁吸/气吸）',
        ],
        'scenarios': [
            '物料周转频率高的生产车间',
            '备件仓库',
            '小件料智能仓储',
            '货到人拣选',
        ],
        'specs': {
            '导航方式': '固定式（单元化部署）',
            '最大储位(小箱)': '1530 个',
            '最大储位(大箱)': '760 个',
            '单料箱最大载重(小箱)': '20 kg',
            '单料箱最大载重(大箱)': '30 kg',
            '单元占地(长/宽/高)': '6200/3300/5500 mm',
            '最快出库量': '150 箱/小时',
            '最大行驶速度': '2 m/s',
            '最大升降速度': '2 m/s',
            '最大取货速度': '1.5 m/s',
            '定位精度': '±2 mm',
            '电压平台/功率': '48V/3kw',
            '通讯方式': '无线WIFI、CANopen、Modbus',
        },
    }

    # XFC002 — NEW SERIES
    NEW_MODELS['XFC002'] = {
        'name': 'XFC002 数智飞仓',
        'series': '数智飞仓系列',
        'description': (
            '数智飞仓XFC002，"货到人"智能仓储解决方案。更大单元尺寸，支持更多储位。'
            '免基建改造，平地即用。实现高密度存储与高效出入库。'
        ),
        'features': [
            '高密存储，最大化仓容',
            '货到人智能拣选',
            '免基建改造，快速部署',
            '无缝对接WMS/MES/ERP等系统',
        ],
        'scenarios': ['智能仓储', '货到人拣选', '高密度存储'],
        'specs': {
            '导航方式': '固定式（单元化部署）',
        },
    }

    # XRP151 — NEW SERIES
    NEW_MODELS['XRP151'] = {
        'name': 'XRP151 四向穿梭车',
        'series': '穿梭车系列',
        'description': (
            '1.5吨四向穿梭车，四向全域行走，专为现代仓储物流场景打造。'
            '超紧凑车身，十字转向省巷道。模块化柔性扩容，旺季加小车即扩容，无需土建。'
            '随机存取，异形旧仓利用率70%~85%。支持FIFO/FEFO切换，兼容多SKU多批次。'
            '可接驳提升机、AGV、分拣线，组成全自动立库。'
        ),
        'features': [
            '四向全域行走：十字转向省巷道，轨道跨巷作业',
            '超紧凑车身：适配密集存储',
            '模块化柔性扩容：旺季加小车即扩容，无需土建',
            '随机存取：异形旧仓利用率70%~85%',
            '全系统智能对接：对接WMS/WCS/PLC自动调度盘点',
            '灵活仓储管控：FIFO/FEFO切换，兼容多SKU多批次',
            '配合提升机实现多层立体存取',
            '适配川字、田字塑料托盘、钢质托盘（500-1500kg）',
            '覆盖国标1200×1000、1100×1100两种规格',
        ],
        'scenarios': [
            '老旧仓库托盘四向穿梭改造（酒水/冷链/粮油/新能源）',
            '整存零拣料箱四向货到人（医药/电商/3C）',
            '汽车零部件产线JIT线边仓',
            '全自动立库（配合提升机+AGV+分拣线）',
        ],
        'specs': {
            '导航方式': '二维码/激光SLAM（穿梭车轨道式）',
            '载荷': '1500 kg',
            '自重': '300-400 kg',
            '外形尺寸(长/宽/高)': '1055/998/127 mm',
            '起升高度': '40 mm',
            '定位精度': '±2 mm',
            '电瓶电压/标称容量': '48/40 V/Ah',
            '续航时间': '8 h',
            '充电时间': '2 h',
            '行驶速度(满载/空载)': '1.5/2 m/s',
            '货叉尺寸': '18/145×1030 mm',
            '货叉外宽': '820 mm',
        },
    }

    # VLM801 + XLM101 — NEW SERIES
    NEW_MODELS['VLM801'] = {
        'name': 'VLM801 + XLM101 无人物流车',
        'series': '无人物流车系列',
        'description': (
            '无人物流车组合方案（VLM801 + XLM101），实现室内外长距离无人化物料转运。'
            '适用于园区级物流、跨厂房物料配送等场景。'
        ),
        'features': [
            '室内外长距离无人化转运',
            '园区级物流配送',
            '自主导航与避障',
        ],
        'scenarios': ['园区物流', '跨厂房物料配送', '室外长距离转运'],
        'specs': {},
    }

    # 具身"袋鼠" — NEW model in 具身系列
    NEW_MODELS['DS001'] = {
        'name': '具身"袋鼠"机器人',
        'series': '具身机器人系列',
        'description': (
            '具身"袋鼠"机器人，具身智能系列新成员。'
            '融合自主导航与柔性操作能力，适用于料箱转运、拣选等场景。'
        ),
        'features': ['具身智能', '自主导航', '柔性操作'],
        'scenarios': ['料箱转运', '智能拣选'],
        'specs': {},
    }

    # XSF102 — NEW model
    NEW_MODELS['XSF102'] = {
        'name': 'XSF102 单侧叉堆高机器人',
        'series': '智能堆高系列',
        'description': (
            '单侧叉堆高机器人，专为窄通道单侧货架堆垛设计。'
            '紧凑车身，高效堆垛作业。'
        ),
        'features': ['单侧叉设计', '窄通道堆垛', '高效作业'],
        'scenarios': ['窄通道货架堆垛', '单侧货架存取'],
        'specs': {},
    }


# ── Main processing ──

def supplement_existing_models():
    """Update existing .md files with enriched data."""
    updated = 0

    for md_file in CATALOG.glob('*.md'):
        if md_file.name == 'index.md':
            continue

        model_id = md_file.stem
        data = MODEL_DATA.get(model_id, {})
        if not data:
            continue

        content = md_file.read_text(encoding='utf-8')
        orig = content

        # Add series-level features if not already present
        if data.get('series_features'):
            feats = data['series_features']
            # Check if features section exists
            if '## 产品特点' not in content:
                content += '\n## 产品特点\n\n'
                for f in feats[:6]:  # Limit to top 6
                    content += f'- {f}\n'
                content += '\n'
            elif '- 智能导航' not in content and '- 车体优秀' not in content:
                # Append features before 适用场景
                feat_text = '\n'.join(f'- {f}' for f in feats[:6])
                content = content.replace('## 适用场景', f'{feat_text}\n\n## 适用场景')

        # Add scenarios if not present
        if data.get('series_scenarios') and '## 适用场景' not in content:
            scenarios = data['series_scenarios']
            content += '\n## 适用场景\n\n'
            for s in scenarios:
                content += f'- {s}\n'
            content += '\n'

        if content != orig:
            md_file.write_text(content, encoding='utf-8')
            updated += 1
            print(f'  Updated: {md_file.name}')

    print(f'Updated {updated} existing model files')


def create_new_models():
    """Create new .md files for newly discovered models."""
    created = 0

    for model_id, data in NEW_MODELS.items():
        md_path = CATALOG / f'{model_id}.md'

        lines = []
        lines.append(f'# {data["name"]}')
        lines.append('')
        lines.append(f'> 产品系列：{data["series"]} | 品牌：中力数智搬马机器人')

        # Source note
        pdf_sources = []
        base = Path(r'D:\Code\OpenRobotService_Data\机器人产品\机器人产品')
        for root, dirs, files in os.walk(base):
            for f in files:
                if model_id in f and f.endswith('.pdf'):
                    pdf_sources.append(f)
        if pdf_sources:
            lines.append(f'> 来源：{", ".join(pdf_sources)}')
        else:
            lines.append(f'> 来源：机器人产品目录')
        lines.append('')

        # Product image
        img_path = MEDIA / f'{model_id}.png'
        if img_path.exists():
            lines.append(f'![{model_id}](media/{model_id}.png)')
            lines.append('')

        # Description
        if data.get('description'):
            lines.append(f'{data["description"]}')
            lines.append('')

        # Specs
        if data.get('specs'):
            lines.append('## 技术参数')
            lines.append('')
            lines.append('| 参数 | 值 |')
            lines.append('|------|----|')
            for k, v in data['specs'].items():
                lines.append(f'| {k} | {v} |')
            lines.append('')

        # Features
        if data.get('features'):
            lines.append('## 产品特点')
            lines.append('')
            for f in data['features']:
                lines.append(f'- {f}')
            lines.append('')

        # Scenarios
        if data.get('scenarios'):
            lines.append('## 适用场景')
            lines.append('')
            for s in data['scenarios']:
                lines.append(f'- {s}')
            lines.append('')

        content = '\n'.join(lines)
        md_path.write_text(content, encoding='utf-8')
        created += 1
        print(f'  Created: {model_id}.md')

    print(f'Created {created} new model files')
    return created


def update_index():
    """Rebuild index.md to include new models."""
    from collections import OrderedDict

    # Read all .md files (excluding index.md)
    models = {}
    for md_file in sorted(CATALOG.glob('*.md')):
        if md_file.name == 'index.md':
            continue
        content = md_file.read_text(encoding='utf-8')

        # Extract series
        series_match = re.search(r'产品系列[：:]\s*(.+?)\s*[|｜]', content)
        series = series_match.group(1).strip() if series_match else '其他'

        # Extract model name
        name_match = re.search(r'^# (.+?)$', content, re.MULTILINE)
        name = name_match.group(1).strip() if name_match else md_file.stem

        models[md_file.stem] = {
            'name': name,
            'series': series,
        }

    print(f'Total models for index: {len(models)}')
    return models


# ── Run ──

if __name__ == '__main__':
    # Parse series brochures
    for f in TEXT_DIR.glob('*.txt'):
        text = f.read_text(encoding='utf-8')
        name = f.name

        if '潜伏小车大全' in name:
            parse_qianfu_daquan(text)
        elif 'XSC系列样册' in name:
            parse_xsc_yangce(text)
        elif 'XP1152' in name and 'XS1152' in name:
            parse_xp_xs_series(text)

    # Parse individual model PDFs
    parse_individual_pdfs()

    print(f'Model data entries: {len(MODEL_DATA)}')
    print(f'New models to create: {len(NEW_MODELS)}')
    print()

    # Supplement existing models
    print('=== Updating existing models ===')
    supplement_existing_models()
    print()

    # Create new models
    print('=== Creating new models ===')
    create_new_models()
    print()

    # Count existing
    existing = [f.stem for f in CATALOG.glob('*.md') if f.name != 'index.md']
    print(f'Existing models: {len(existing)}')
    print(f'New models: {NEW_MODELS.keys()}')

    print('\nDone! Remember to:')
    print('1. Extract product images from PDFs (render first page to PNG)')
    print('2. Update index.md with new models')
    print('3. Review generated .md files for accuracy')
