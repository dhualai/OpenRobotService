"""检查工程师 responsibility_modules 里调度USP子模块分布，确认新增仿真模块能否映射到工程师。"""
import json
import collections


def _mods_for_product(rm, product):
    """兼容三层 {产品:{界面:[功能]}} 与旧两层 {产品:[模块]}，返回功能名扁平列表。"""
    val = rm.get(product) or []
    if isinstance(val, dict):
        out = []
        for iface, funcs in val.items():
            out.extend(funcs if isinstance(funcs, list) else [funcs])
        return out
    return val if isinstance(val, list) else [val] if val else []


d = json.load(open('ai/agents/AiDiagnosisPlatform/assigner/eval/data/users_202607311332.json', encoding='utf-8'))['users']
c = collections.Counter()
for u in d:
    rm = u.get('responsibility_modules') or '{}'
    if isinstance(rm, str):
        try:
            rm = json.loads(rm)
        except Exception:
            rm = {}
    if not isinstance(rm, dict):
        rm = {}
    for mod in _mods_for_product(rm, '调度USP'):
        c[mod] += 1

print('=== 调度USP 所有子模块分布 ===')
for k, v in c.most_common():
    print(f'  {k}: {v}人')

print()
print('=== 前端/3D 相关工程师 ===')
for u in d:
    rm = u.get('responsibility_modules') or '{}'
    if isinstance(rm, str):
        try:
            rm = json.loads(rm)
        except Exception:
            rm = {}
    if not isinstance(rm, dict):
        rm = {}
    mods = _mods_for_product(rm, '调度USP')
    if any(('3D' in m or '前端' in m) for m in mods):
        print(f'  {u.get("name")} | {mods}')
