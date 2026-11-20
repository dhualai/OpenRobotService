"""分析 live 测试输出：按场景统计提单数/动作/异常。"""
import re, sys

txt = open(sys.argv[1], encoding='utf-8', errors='replace').read()
parts = re.split(r'场景 ([A-GH][0-9]) \[', txt)
# parts = [pre, sid1, body1, sid2, body2, ...]
results = {}
i = 1
while i + 1 < len(parts):
    sid = parts[i]
    body = parts[i + 1]
    submits = len(re.findall(r'OK已提单', body))
    actions = re.findall(r'-> action=(\w+)', body)
    stages = re.findall(r'stage=(\w+)', body)
    codes = re.findall(r'-> code=(\d+)', body)
    err = ('服务暂时不可用' in body) or ('[ERR]' in body)
    results[sid] = dict(submits=submits, actions=actions, stages=stages, codes=codes, err=err)
    i += 2

EXPECT = {
    'A1': '提单x1', 'A2': '提单x1', 'B1': '提单x1', 'B2': '提单x1',
    'C1': '提单x1(turn2应拦)', 'C2': '提单x2', 'C3': '提单x1(问进度不提)',
    'D1': '不提单', 'D2': '提单x1', 'D3': '不提单',
    'E1': '提单(需project)', 'E2': '提单(需project)', 'E3': '提单(需project)',
    'F1': '提单x1(强制)', 'F2': '不提单',
    'G1': 'draft_ready', 'G2': 'not_ready', 'G3': '提单后prepare拦',
}
print(f"{'场景':4} {'提单':4} {'末action':10} {'stage/code':18} {'异常':4} 期望")
for sid in sorted(results):
    r = results[sid]
    last_a = r['actions'][-1] if r['actions'] else '-'
    sc = (r['stages'][-1] if r['stages'] else '') or (r['codes'][-1] if r['codes'] else '')
    print(f"{sid:4} {r['submits']:<4} {last_a:10} {sc:18} {'ERR' if r['err'] else '':4} {EXPECT.get(sid,'')}")
