"""生成猜你想问问题列表"""
from dotenv import load_dotenv
from pathlib import Path
import json, re, os, sys

# 确保项目根在 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from qdrant_client import QdrantClient

DATA = 'D:/Code/OpenRobotService_Data/docs'

def load_jsonl(path):
    items = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            line = re.sub(r',\s*}', '}', line)
            line = re.sub(r',\s*]', ']', line)
            try:
                items.append(json.loads(line))
            except Exception:
                pass
    return items

results = []

# ===== 1. FAQ =====
cat_map = {
    'manual.1': '部署与配置', 'manual.2': '车辆上线与管理', 'manual.3': '充电管理',
    'manual.4': '外设接入', 'manual.5': '地图管理', 'manual.6': '库位配置',
    'manual.7': '载具配置', 'manual.8': '偏移量配置', 'manual.9': '任务管理',
    'manual.10': '流程管理', 'manual.11': '监控与回放',
}
faq = load_jsonl(f'{DATA}/faq_doc/faq_merged_clean.jsonl')
print(f'FAQ: {len(faq)} 条')
for item in faq:
    sources = item.get('source_ids', [])
    cat = '操作手册'
    for s in sources:
        for prefix, name in cat_map.items():
            if s.startswith(prefix):
                cat = name
                break
        if cat != '操作手册':
            break
    results.append({
        'id': item['faq_id'], 'question': item['question'],
        'aliases': item.get('aliases', []), 'category': cat, 'source': 'faq',
    })

# ===== 2. Platform FAQ =====
pf = load_jsonl(f'{DATA}/platform_faq/platform_faq.jsonl')
print(f'Platform FAQ: {len(pf)} 条')
for item in pf:
    results.append({
        'id': item['faq_id'], 'question': item['question'],
        'aliases': [], 'category': '平台功能', 'source': 'platform_faq',
    })

# ===== 3. 排查树症状 =====
trouble = json.load(open(f'{DATA}/问题排查树_v1.json', encoding='utf-8'))
for cat in trouble.get('categories', []):
    cat_name = cat['name']
    for sym in cat.get('symptoms', []):
        results.append({
            'id': f'trouble.{sym["id"]}',
            'question': sym['name'],
            'aliases': [],
            'category': f'问题排查-{cat_name}',
            'source': 'troubleshooting',
        })
print(f'排查树: {sum(len(c.get("symptoms",[])) for c in trouble.get("categories",[]))} 条')

# ===== 4. 车端错误码 =====
cfg = os.getenv('QDRANT_LOCAL_PATH', '')
local = Path(cfg)
if not local.is_absolute():
    local = Path('.').resolve() / local
c = QdrantClient(path=str(local))
cheduan_cols = sorted(
    [col.name for col in c.get_collections().collections
     if 'cheduan' in col.name and 'manual' not in col.name],
    reverse=True
)
if cheduan_cols:
    col = cheduan_cols[0]
    points, _ = c.scroll(collection_name=col, limit=300, with_payload=True)
    codes = []
    for p in points:
        payload = p.payload
        code = str(payload.get('error_code', '')).strip()
        desc = payload.get('description_cn', payload.get('description', '')).strip()
        if code and desc:
            codes.append({'code': code, 'desc': desc[:80]})
    codes.sort(key=lambda x: x['code'])

    # 每百位段选1-2个代表
    selected = {}
    for cd in codes:
        try:
            prefix = int(cd['code']) // 100
        except Exception:
            continue
        if prefix not in selected:
            selected[prefix] = []
        if len(selected[prefix]) < 2:
            selected[prefix].append(cd)

    for prefix in sorted(selected.keys()):
        for cd in selected[prefix]:
            results.append({
                'id': f'error.{cd["code"]}',
                'question': f'车端错误码{cd["code"]}是什么意思？',
                'aliases': [f'错误码{cd["code"]}', f'报错{cd["code"]}怎么办', f'{cd["code"]}错误'],
                'category': '车端错误码',
                'source': 'error_code',
            })
    print(f'错误码: {sum(len(v) for v in selected.values())} 个代表/共 {len(codes)} 个')
c.close()

# ===== 输出 =====
out_path = Path(__file__).resolve().parent.parent / 'data' / 'suggested_questions.json'
out_path.parent.mkdir(parents=True, exist_ok=True)
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f'\n总计: {len(results)} 条 -> {out_path}')

# 分类统计
from collections import Counter
cats = Counter(r['category'] for r in results)
for cat, cnt in cats.most_common():
    print(f'  {cat}: {cnt}')
