"""Rebuild index.md — 50 models, 8 series only (no new series/models)."""
import re, os
from pathlib import Path
from collections import defaultdict

CATALOG = Path(r"D:\Code\OpenRobotService_Data\kb\company\product_catalog")

models = {}
for md_file in sorted(CATALOG.glob('*.md')):
    if md_file.name == 'index.md':
        continue
    content = md_file.read_text(encoding='utf-8').replace('\r\n', '\n')
    series_match = re.search(r'产品系列[：:]\s*(.+?)\s*[|｜\n]', content)
    series = series_match.group(1).strip() if series_match else '其他'
    name_match = re.search(r'^# (.+?)$', content, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else md_file.stem
    specs = {}
    for line in content.split('\n'):
        m = re.match(r'\| (.+?) \| (.+?) \|', line)
        if m and m.group(1) != '参数':
            specs[m.group(1)] = m.group(2)
    models[md_file.stem] = {'name': name, 'series': series, 'specs': specs}

series_models = defaultdict(list)
for mid, mdata in models.items():
    series_models[mdata['series']].append(mid)

SERIES_ORDER = [
    '潜伏小车系列', '自动搬运车系列', '智能搬运车系列',
    '智能堆高系列', '智能前移系列', '智能牵引系列',
    '智能拣料系列', '具身机器人系列',
]

SERIES_META = {
    '潜伏小车系列': ('300kg – 5000kg', '窄通道料架搬运、产线对接'),
    '自动搬运车系列': ('1200kg – 2000kg', '点对点搬运、跨车间跨楼层'),
    '智能搬运车系列': ('1000kg – 5000kg', '高速重载、室内外多场景搬运'),
    '智能堆高系列': ('800kg – 2000kg', '低位到高位货架堆垛（最高 13m）'),
    '智能前移系列': ('1200kg – 2000kg', '室内外托盘上架、窄通道前移'),
    '智能牵引系列': ('500kg – 6000kg', '室内外牵引、观光接驳'),
    '智能拣料系列': ('50kg 料箱', '密集料箱库料箱存取'),
    '具身机器人系列': ('2kg – 300kg', '料箱转运、拣选、柔性抓取'),
}

lines = [
    '# 搬马机器人产品目录', '',
    '> 🏢 公司知识 — 中力数智搬马机器人全系列产品索引',
    f'> 共 {len(models)} 款车型，{len(series_models)} 大系列',
    '', '---', '',
    '## 一、系列总览', '',
    '中力数智搬马机器人共有 8 大产品系列：', '',
    '| 系列 | 车型数 | 载荷范围 | 主要用途 |',
    '|------|--------|----------|----------|',
]
for s in SERIES_ORDER:
    if s in series_models:
        load, purpose = SERIES_META.get(s, ('-', '-'))
        lines.append(f'| {s} | {len(series_models[s])} | {load} | {purpose} |')

lines += ['', '---', '', '## 二、按系列查看', '']

for s_name in SERIES_ORDER:
    if s_name not in series_models:
        continue
    mids = series_models[s_name]
    names = []
    for mid in mids:
        mdata = models[mid]
        name_short = mdata['name'].replace(mid + ' ', '').replace(mid, '').strip()
        load = mdata['specs'].get('载荷', '-')
        names.append(f'{mid}({name_short[:25]} {load})')
    lines.append(f'### {s_name}（{len(mids)}款）')
    lines.append(f'型号：{"、".join(names)}')
    lines.append('')

# Load classification
lines += ['---', '', '## 三、按载荷分级', '']
light, medium, heavy, super_heavy, misc = [], [], [], [], []
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
    label = f'{mid}({load_str})'
    if val <= 500: light.append(label)
    elif val <= 1500: medium.append(label)
    elif val <= 3000: heavy.append(label)
    else: super_heavy.append(label)

for label, items in [('轻载（≤500kg）', light), ('中载（500–1500kg）', medium),
                      ('重载（1500–3000kg）', heavy), ('超重载（≥3000kg）', super_heavy)]:
    lines.append(f'### {label}')
    lines.append('、'.join(items) if items else '（无）')
    lines.append('')
if misc:
    lines.append(f'### 其他')
    lines.append('、'.join(misc))
    lines.append('')

# Navigation
lines += ['---', '', '## 四、按导航方式', '']
nav_groups = defaultdict(list)
for mid, mdata in models.items():
    nav = mdata['specs'].get('导航方式', '')
    if not nav: nav_groups['待补充'].append(mid)
    elif '3D激光SLAM' in nav and ('二维码' in nav or '融合' in nav): nav_groups['3D激光SLAM + 二维码融合定位'].append(mid)
    elif '3D激光SLAM' in nav: nav_groups['3D激光SLAM 导航'].append(mid)
    elif '激光SLAM' in nav and '反光板' in nav: nav_groups['激光SLAM / 反光板导航'].append(mid)
    elif '激光SLAM' in nav: nav_groups['激光SLAM 导航'].append(mid)
    elif '二维码' in nav: nav_groups['二维码导航'].append(mid)
    elif '视觉' in nav: nav_groups['视觉导航'].append(mid)
    else: nav_groups[nav].append(mid)

for nav_type in ['二维码导航', '激光SLAM 导航', '激光SLAM / 反光板导航',
                  '3D激光SLAM 导航', '3D激光SLAM + 二维码融合定位', '视觉导航', '待补充']:
    if nav_type in nav_groups:
        lines.append(f'### {nav_type}')
        lines.append('、'.join(nav_groups[nav_type]))
        lines.append('')

# TOP rankings
lines += ['---', '', '## 五、关键指标 TOP 排行', '']
load_data = []
for mid, mdata in models.items():
    val_str = mdata['specs'].get('载荷', '0')
    try: val = float(re.search(r'[\d.]+', str(val_str)).group()); load_data.append((mid, val, val_str))
    except: pass
lines.append('### 最大载荷 TOP 5')
lines.append('  >  '.join([f'{mid}（{v}）' for mid, _, v in sorted(load_data, key=lambda x: x[1], reverse=True)[:5]]))
lines.append('')

lift_data = []
for mid, mdata in models.items():
    val_str = mdata['specs'].get('起升高度', '0')
    try: val = float(re.search(r'[\d.]+', val_str).group()); lift_data.append((mid, val))
    except: pass
lines.append('### 最高起升 TOP 5')
lines.append('  >  '.join([f'{mid}（{int(v)}mm）' for mid, v in sorted(lift_data, key=lambda x: x[1], reverse=True)[:5]]))
lines.append('')

(CATALOG / 'index.md').write_text('\n'.join(lines), encoding='utf-8')
print(f'index.md rebuilt: {len(models)} models in {len(series_models)} series')
