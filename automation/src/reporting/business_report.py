"""生成业务视角测试结论报告（四层 + 步骤详情）。

层次：业务模块 → 功能场景(story) → 具体场景(title) → 执行步骤(steps)
每个步骤展示：中文动作 + 入参 + 出参 + 断言（附件内容内联）

用法：
    python automation/src/reporting/business_report.py \\
        --results automation/output/allure-results \\
        --out automation/output/test-report-business.html
"""
import argparse
import pathlib
import collections
import datetime
import re

if __package__:
    from .business_scenarios import (
        EVIDENCE_ATTACHMENT, OBSERVATION_SOURCES, SCENARIOS, SCENARIO_LABEL, SOURCE_LABELS,
        describe_actual,
    )
    from .report_data import (
        STATUS_CN, case_duration_ms, count_statuses, describe_counts, load_results,
        read_object, report_overview,
    )
else:
    from business_scenarios import (
        EVIDENCE_ATTACHMENT, OBSERVATION_SOURCES, SCENARIOS, SCENARIO_LABEL, SOURCE_LABELS,
        describe_actual,
    )
    from report_data import (
        STATUS_CN, case_duration_ms, count_statuses, describe_counts, load_results,
        read_object, report_overview,
    )

DEFAULT_RESULTS = pathlib.Path('output/allure-results')
DEFAULT_OUT = pathlib.Path('output/test-report-business.html')

FEATURE_INFO = {
    '我要摇人': ('用户端入口', '用户通过智能对话描述问题，系统识别意图后自动创建工单'),
    '系统任务': ('工单全生命周期', '创建、查询、指派、状态流转、催办、评论、关闭等核心处理流程'),
    '后台管理': ('管理后台', '项目、用户、角色权限、知识库、统计日报等配置与运营能力'),
    '认证': ('登录与鉴权', '登录、令牌刷新、权限校验、越权访问拦截'),
    '业务链路': ('端到端流程', '跨模块串联：对话 → AI 问答 → 转工单 → 处理 → 关闭'),
}

STORY_LABEL = {
    '用户': '用户管理', '消息': '消息收发', '会话': '通话会话', '创建工单': '创建工单',
    '角色': '角色权限', '指派工单': '工单指派', '风险': '风险识别', '登录': '登录认证',
    '项目': '项目管理', '查询工单': '工单查询', '催办': '工单催办', '问答': 'AI 问答',
    '认证': '认证机制', '点赞': '点赞互动', '状态流转': '状态流转', '更新工单': '更新工单',
    '删除工单': '删除工单', '评论': '工单评论', '统计': '数据统计', '日报': '日报导出',
    '全链路': '全流程串联', '权限': '权限校验', '阶段响应': '阶段协商',
    '阶段完成': '阶段推进', '规格文档': '问题文档', '操作日志': '操作记录',
    '工单关联': '工单关联关系', '知识发布': '知识发布', '知识检索': '知识检索',
}

# 路径 → 业务对象（正则）
RESOURCES = [
    (r'^/api/tasks/\d+/status$', '工单状态'),
    (r'^/api/tasks/\d+/assign$', '工单指派'),
    (r'^/api/tasks/\d+/comments', '工单评论'),
    (r'^/api/tasks/\d+/spec-doc', '问题文档'),
    (r'^/api/tasks/\d+/relations', '工单关联'),
    (r'^/api/tasks/\d+/operation-logs', '操作记录'),
    (r'^/api/tasks/\d+/respond', '阶段协商'),
    (r'^/api/tasks/\d+/complete-step', '阶段推进'),
    (r'^/api/tasks/\d+', '工单'),
    (r'^/api/tasks/cuiban-notification', '工单催办'),
    (r'^/api/tasks/assignable-users', '可指派人员'),
    (r'^/api/tasks/filter', '工单筛选'),
    (r'^/api/tasks/stats', '工单统计'),
    (r'^/api/tasks', '工单'),
    (r'^/api/call/conversations', '通话会话'),
    (r'^/api/call/messages', '通话消息'),
    (r'^/api/call/qa/ask', 'AI 问答'),
    (r'^/api/call/qa/submit', '问题提交'),
    (r'^/api/call/qa/ticket', '转工单'),
    (r'^/api/call', '通话'),
    (r'^/api/admin/projects/\w+/risks', '项目风险'),
    (r'^/api/admin/projects', '项目'),
    (r'^/api/admin/users', '用户'),
    (r'^/api/admin/roles', '角色'),
    (r'^/api/admin/knowledge', '知识库'),
    (r'^/api/admin/daily-reports', '日报'),
    (r'^/api/admin/tickets', '后台工单'),
    (r'^/api/admin', '后台配置'),
    (r'^/api/auth/login', '登录'),
    (r'^/api/auth/refresh', '令牌刷新'),
    (r'^/api/auth', '认证'),
    (r'^/api/health', '健康检查'),
]

VERBS = {'GET': '查询', 'POST': '提交', 'PUT': '更新', 'PATCH': '更新', 'DELETE': '删除'}
SPECIAL = {
    ('POST', '/api/tasks'): '提交建单请求',
    ('POST', '/api/auth/login'): '提交登录请求',
    ('DELETE', '/api/tasks'): '删除工单',
}


def translate_step(raw):
    """把 "POST /api/tasks/1/status" 翻译成 "更新工单状态"。"""
    if not raw:
        return '(未命名步骤)'
    parts = raw.split(' ', 1)
    if len(parts) != 2:
        return raw
    method, path = parts[0].upper(), parts[1].split('?')[0]
    if (method, path) in SPECIAL:
        return SPECIAL[(method, path)]
    for pattern, label in RESOURCES:
        if re.match(pattern, path):
            return VERBS.get(method, method) + label
    return raw


def load_att(root, step):
    """读取步骤附件：{'Request': text, 'Response': text, '断言信息': text}"""
    out = {}
    attachments = step.get('attachments')
    if not isinstance(attachments, list):
        return out
    for a in attachments:
        if not isinstance(a, dict):
            continue
        src, name = a.get('source'), a.get('name')
        if not isinstance(src, str) or not src or pathlib.Path(src).name != src or not name:
            continue
        p = root / src
        if p.exists():
            try:
                out[name] = p.read_text(encoding='utf-8')
            except Exception:
                pass
    return out


def extract_steps(root, steps, depth=0):
    out = []
    if not isinstance(steps, list):
        return out
    for s in steps:
        if not isinstance(s, dict):
            continue
        out.append({
            'raw': s.get('name') or '',
            'label': translate_step(s.get('name') or ''),
            'status': s.get('status') or 'unknown',
            'depth': depth,
            'att': load_att(root, s),
        })
        out.extend(extract_steps(root, s.get('steps'), depth + 1))
    return out


def result_labels(result):
    labels = result.get('labels')
    if not isinstance(labels, list):
        return {}
    return {
        item['name']: item['value'] for item in labels if isinstance(item, dict)
        and isinstance(item.get('name'), str) and isinstance(item.get('value'), str)
    }


def case_anchor(result):
    uid = result.get('uuid')
    return f'case-{uid}' if isinstance(uid, str) and re.fullmatch(r'[a-fA-F0-9-]+', uid) else ''


def collect(root=DEFAULT_RESULTS, *, results=None):
    scenes = collections.defaultdict(lambda: collections.defaultdict(list))
    for d in load_results(root) if results is None else results:
        labels = result_labels(d)
        source = d['source']
        if source in OBSERVATION_SOURCES:
            feat = source
        else:
            feat = labels.get('feature') or source or '来源未标注'
        story = labels.get('story') or '未标注场景'
        details = d.get('statusDetails')
        details = details if isinstance(details, dict) else {}
        scenes[feat][story].append({
            'title': d['name'],
            'status': d['status'],
            'source': source,
            'anchor': case_anchor(d),
            'steps': extract_steps(d['directory'], d.get('steps')),
            'message': str(details.get('message') or '').strip(),
            'duration': case_duration_ms(d),
        })
    return scenes


def esc(x):
    return (str(x).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _business_evidence(result, scenario_id):
    found = {}
    nodes = [result]
    while nodes:
        node = nodes.pop()
        attachments = node.get('attachments')
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict) or attachment.get('name') != EVIDENCE_ATTACHMENT:
                    continue
                source = attachment.get('source')
                if not isinstance(source, str) or not source or pathlib.Path(source).name != source:
                    continue
                evidence = read_object(result['directory'] / source)
                step = evidence.get('step')
                if evidence.get('scenario') == scenario_id and isinstance(step, str):
                    found[step] = evidence
        steps = node.get('steps')
        if isinstance(steps, list):
            nodes.extend(step for step in steps if isinstance(step, dict))
    return found


def _business_result(case, judgements):
    if case is None:
        return '未验证', 'neutral', '本次没有收到该测试来源的场景结果。'
    status = case['status']
    if status == 'skipped':
        detail = case.get('statusDetails')
        message = str(detail.get('message') or '') if isinstance(detail, dict) else ''
        if 'XFAIL' in message:
            return '未验证', 'neutral', '预期失败，计入跳过。'
        if 'USE_MOCK=0 required' in message:
            return '未验证', 'neutral', '本次没有启用真实后端验证。'
        return '未验证', 'neutral', '本次用例已跳过。'
    if status in ('failed', 'broken') or (status == 'passed' and False in judgements):
        return '失败', 'failure', '请查看实际结果和技术详情。'
    if status != 'passed' or None in judgements:
        return '未验证', 'neutral', '步骤证据不完整。'
    label = '模拟通过' if case['source'] == SOURCE_LABELS['quality-api'] else '通过'
    return label, 'success', ''


def render_business_scenarios(results):
    cases = collections.defaultdict(list)
    for result in results:
        scenario_id = result_labels(result).get(SCENARIO_LABEL)
        if scenario_id in SCENARIOS:
            cases[scenario_id].append(result)

    priority = {'failed': 0, 'broken': 1, 'passed': 2, 'skipped': 3, 'unknown': 4}
    parts = ['<section class="business-list"><h3>业务场景一览</h3>']
    for scenario_id, scenario in SCENARIOS.items():
        # A Mock run can collect a real-only case and skip it. Use the designated evidence source.
        candidates = [case for case in cases[scenario_id] if case['source'] == scenario.source]
        case = min(candidates, key=lambda record: priority[record['status']], default=None)
        evidence = _business_evidence(case, scenario_id) if case else {}
        observations = [
            (step, *describe_actual(step, evidence.get(step_id, {})))
            for step_id, step in scenario.steps.items()
        ]
        verdict, colour, reason = _business_result(case, [match for _, _, match in observations])
        parts.append(
            f'<details class="business-scenario" id="scenario-{scenario_id}">'
            f'<summary><span class="module-name">{esc(scenario.module)}</span>'
            f'<strong>{esc(scenario.title)}</strong>'
            f'<span class="business-status {colour}">{verdict}</span>'
            f'<span class="source">{esc(scenario.source)}{"（计划）" if case is None else ""}</span>'
            '</summary><div class="business-body">'
        )
        if reason:
            parts.append(f'<p class="meta">{esc(reason)}</p>')
        parts.append(
            '<table><thead><tr><th>角色</th><th>动作</th><th>预期结果</th>'
            '<th>实际结果</th></tr></thead><tbody>'
        )
        for step, actual, match in observations:
            colour = 'failure' if match is False and verdict == '失败' else ''
            parts.append(
                f'<tr><td>{esc(step.role)}</td><td>{esc(step.action)}</td>'
                f'<td>{esc(step.expected)}</td><td class="{colour}">{esc(actual)}</td></tr>'
            )
        parts.append('</tbody></table>')
        anchor = case_anchor(case) if case else ''
        if anchor:
            parts.append(
                f'<p><a href="#{anchor}" '
                'onclick="document.getElementById(this.hash.slice(1)).open = true">'
                '查看技术详情</a></p>'
            )
        parts.append('</div></details>')
    parts.append('</section>')
    return '\n'.join(parts)


def clip(text, limit=1800):
    t = text.strip()
    return t if len(t) <= limit else t[:limit] + f'\n…（已截断，原文 {len(t)} 字符）'


def render_att(att):
    """渲染入参/出参/断言三块。"""
    parts = []
    order = [('Request', '入参'), ('Response', '出参'), ('断言信息', '断言')]
    for key, label in order:
        if key in att:
            parts.append(f'<div class="att"><span class="att-label">{label}</span>'
                         f'<pre>{esc(clip(att[key]))}</pre></div>')
    return ''.join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description='生成业务视角测试结论报告')
    parser.add_argument('--results', default=str(DEFAULT_RESULTS),
                        help='Allure 结果目录（默认 output/allure-results）')
    parser.add_argument('--out', default=str(DEFAULT_OUT),
                        help='输出 HTML 路径（默认 output/test-report-business.html）')
    parser.add_argument('--context', type=pathlib.Path, help='Test Gate 判定与运行信息 JSON')
    parser.add_argument('--expect-gate', action='store_true', help='缺失门禁判定时显示未验收')
    args = parser.parse_args(argv)

    root = pathlib.Path(args.results)
    out_path = pathlib.Path(args.out)

    results = load_results(root)
    scenes = collect(root, results=results)
    all_scenes = [s for m in scenes.values() for v in m.values() for s in v]
    grand = len(all_scenes)
    bad = [s for s in all_scenes if s['status'] in ('failed', 'broken')]
    context = read_object(args.context) if args.context else {}
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    feats = sorted(scenes.keys(), key=lambda k: -sum(len(v) for v in scenes[k].values()))

    p = ['<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<title>测试结论报告</title><style>',
         '*{box-sizing:border-box}',
         'body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:32px 20px 64px;color:#1f2328;background:#f6f8fa;line-height:1.65}',
         '.wrap{max-width:1040px;margin:0 auto}',
         'h1{font-size:23px;margin:0 0 6px}',
         '.sub{color:#6e7781;font-size:13px;margin-bottom:22px}',
         '.report-overview{border-radius:12px;padding:18px 22px;margin-bottom:26px;'
         'border:1px solid #d1d9e0;background:#fff;overflow-wrap:anywhere}',
         '.report-overview h2{margin:0 0 6px;font-size:17px}.report-overview p{margin:6px 0;font-size:14px}',
         '.gate-suites{margin-top:14px}.source{font-size:11px;color:#57606a}',
         '.business-list{margin:22px 0}.business-list h3{font-size:17px}',
         '.business-scenario{background:#fff;border:1px solid #d1d9e0;border-radius:9px;margin:8px 0}',
         '.business-scenario>summary{cursor:pointer;padding:12px;display:grid;',
         'grid-template-columns:6em minmax(13em,1fr) 5em 12em;gap:10px;align-items:center}',
         '.business-scenario>summary .module-name::before{content:"▸";margin-right:5px}',
         '.business-scenario[open]>summary .module-name::before{content:"▾"}',
         '.business-scenario>summary .source{font-size:12px}',
         '.business-body{padding:0 12px 10px}.module-name,.neutral{color:#57606a}',
         '.business-status{font-weight:600}.success{color:#146c2e}.failure{color:#b3261e}',
         '@media(max-width:700px){.business-scenario>summary{grid-template-columns:5em 1fr}',
         '.business-body{overflow-x:auto}}',
         '.toc{background:#fff;border:1px solid #e3e8ee;border-radius:10px;padding:6px 20px;font-size:13px;margin:22px 0}',
         '.toc a{display:inline-block;margin:8px 18px 8px 0;color:#0969da;text-decoration:none}',
         'table{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden;font-size:14px}',
         'th{background:#f0f3f6;text-align:left;padding:10px 14px;font-weight:600;font-size:13px;color:#57606a}',
         'td{padding:11px 14px;border-top:1px solid #eaeef2;vertical-align:top}',
         '.mod{font-weight:600}.mod small{display:block;font-weight:400;color:#6e7781;font-size:12px;margin-top:2px}',
         'section.module{margin-top:34px}',
         'section.module>h3{font-size:17px;margin:0 0 4px}',
         'section.module>h3 span{font-weight:400;font-size:13px;color:#6e7781;margin-left:8px}',
         '.mdesc{font-size:13px;color:#6e7781;margin:0 0 14px}',
         '.story{margin-bottom:16px}',
         '.story>h4{font-size:14px;margin:0 0 8px;color:#24292f}',
         '.story>h4 span{font-weight:400;font-size:12px;color:#6e7781;margin-left:8px}',
         'details.scene{background:#fff;border:1px solid #e3e8ee;border-radius:8px;margin-bottom:6px;overflow:hidden}',
         'details.scene>summary{cursor:pointer;padding:9px 14px;font-size:13px;display:flex;align-items:center;gap:9px;list-style:none}',
         'details.scene>summary::-webkit-details-marker{display:none}',
         'details.scene>summary::before{content:"▸";color:#8b949e;font-size:11px}',
         'details.scene[open]>summary::before{content:"▾"}',
         'details.scene>summary:hover{background:#f6f8fa}',
         '.sname{flex:1}',
         '.tag{font-size:11px;padding:1px 7px;border-radius:10px;font-weight:600;white-space:nowrap}',
         '.tag.ok{background:#e8f5ec;color:#146c2e}.tag.bad{background:#fdeeee;color:#b3261e}',
         '.tag.skip{background:#eef1f4;color:#57606a}',
         '.body{padding:6px 16px 14px 30px;border-top:1px dashed #eaeef2}',
         '.steplist{margin:8px 0 0;padding:0;list-style:none}',
         '.step{margin-bottom:10px}',
         '.step-head{font-size:13px;color:#1f2328;display:flex;align-items:center;gap:8px}',
         '.step-no{display:inline-block;min-width:20px;height:20px;line-height:20px;text-align:center;'
         'background:#eef1f4;border-radius:10px;font-size:11px;color:#57606a}',
         '.step-raw{font-size:11px;color:#8b949e;margin-left:6px}',
         '.st{font-weight:700;font-size:11px}.st.ok{color:#1a7f37}.st.bad{color:#cf222e}.st.skip{color:#8b949e}',
         'details.att{margin:4px 0 0 28px}',
         'details.att>summary{cursor:pointer;font-size:12px;color:#0969da;list-style:none;padding:2px 0}',
         'details.att>summary::-webkit-details-marker{display:none}',
         '.att{margin:6px 0 0 28px}',
         '.att-label{display:inline-block;font-size:11px;font-weight:600;color:#57606a;'
         'background:#f0f3f6;border-radius:4px;padding:1px 7px;margin-bottom:3px}',
         '.att pre{margin:0 0 8px;background:#fbfcfd;border:1px solid #eaeef2;border-radius:6px;'
         'padding:8px 11px;font-size:11.5px;line-height:1.5;overflow:auto;max-height:280px;color:#3d4450}',
         '.err{background:#fdf3f3;border-left:3px solid #cf222e;padding:9px 13px;margin-top:10px;'
         'font-size:12.5px;color:#8b2222;border-radius:4px;white-space:pre-wrap;word-break:break-word}',
         '.meta{font-size:11.5px;color:#8b949e;margin-top:8px}',
         '.note{background:#fff;border-left:3px solid #d0a215;padding:11px 15px;font-size:12.5px;'
         'color:#5c4a12;border-radius:4px;margin-top:18px}',
         '</style></head><body><div class="wrap">']

    p.append('<h1>自动化测试结论报告</h1>')
    p.append(f'<div class="sub">生成时间 {now} · {len(feats)} 个业务模块</div>')
    p.append(report_overview(count_statuses(all_scenes), context, expect_gate=args.expect_gate))
    p.append(render_business_scenarios(results))

    p.append('<div class="toc" style="background:#eef6ff;border-color:#c9defa">'
             '<b>怎么看到「步骤、入参、出参、断言」</b>：'
             '每个模块的第一个场景已默认展开；其余场景点名称即可展开，'
             '每个步骤再点「查看入参 / 出参 / 断言」可看到该次请求的完整报文与断言结果。'
             '</div>')

    p.append('<h3 style="font-size:15px;margin:0 0 10px">技术明细 · 各模块情况</h3>')
    p.append('<table><thead><tr><th style="width:26%">业务模块</th><th style="width:11%">场景数</th>'
             '<th style="width:32%">结果分布</th><th>测了什么</th></tr></thead><tbody>')
    for feat in feats:
        counts = count_statuses(s for items in scenes[feat].values() for s in items)
        mtotal = sum(counts.values())
        pos, desc = FEATURE_INFO.get(feat, ('', '该模块的业务流程与接口行为'))
        p.append(f'<tr><td class="mod">{esc(feat)}<small>{esc(pos)}</small></td>'
                 f'<td>{mtotal}</td><td>{esc(describe_counts(counts))}</td><td>{esc(desc)}</td></tr>')
    p.append('</tbody></table>')

    p.append('<div class="toc">快速跳转：')
    for i, feat in enumerate(feats):
        p.append(f'<a href="#m{i}">{esc(feat)}</a>')
    p.append('</div>')

    for i, feat in enumerate(feats):
        pos, desc = FEATURE_INFO.get(feat, ('', ''))
        mtotal = sum(len(v) for v in scenes[feat].values())
        p.append(f'<section class="module" id="m{i}">')
        p.append(f'<h3>{esc(feat)}<span>{mtotal} 个场景</span></h3>')
        p.append(f'<p class="mdesc">{esc(desc)}</p>')
        first_of_module = True
        for story, items in sorted(scenes[feat].items(), key=lambda kv: -len(kv[1])):
            label = STORY_LABEL.get(story, story)
            sbad = sum(1 for s in items if s['status'] in ('failed', 'broken'))
            mark = f' · <span style="color:#b3261e">{sbad} 个未通过</span>' if sbad else ''
            p.append(f'<div class="story"><h4>{esc(label)}<span>{len(items)} 个场景{mark}</span></h4>')
            for sc in sorted(items, key=lambda s: (0 if s['status'] in ('failed', 'broken') else 1)):
                is_bad = sc['status'] in ('failed', 'broken')
                tag_cls = 'bad' if is_bad else ('skip' if sc['status'] in ('skipped', 'unknown') else 'ok')
                auto_open = is_bad or first_of_module
                first_of_module = False
                source = f'<span class="source">{esc(sc["source"])}</span>' if sc["source"] else ''
                anchor = f' id="{sc["anchor"]}"' if sc["anchor"] else ''
                p.append(f'<details class="scene"{anchor}{" open" if auto_open else ""}>')
                p.append(f'<summary><span class="sname">{esc(sc["title"])}</span>'
                         f'{source}'
                         f'<span class="tag {tag_cls}">{STATUS_CN.get(sc["status"], sc["status"])}</span></summary>')
                p.append('<div class="body">')
                if sc['steps']:
                    p.append('<ul class="steplist">')
                    for idx, st in enumerate(sc['steps'], 1):
                        scls = 'bad' if st['status'] in ('failed', 'broken') else ('ok' if st['status'] == 'passed' else 'skip')
                        icon = '✗' if st['status'] in ('failed', 'broken') else ('✓' if st['status'] == 'passed' else '–')
                        raw = f'<span class="step-raw">{esc(st["raw"])}</span>' if st['raw'] and st['raw'] != st['label'] else ''
                        p.append(f'<li class="step"><div class="step-head">'
                                 f'<span class="step-no">{idx}</span>'
                                 f'<span>{esc(st["label"])}</span>{raw}'
                                 f'<span class="st {scls}">{icon}</span></div>')
                        if st['att']:
                            has_data = any(k in st['att'] for k in ('Request', 'Response', '断言信息'))
                            if has_data:
                                p.append('<details class="att"%s><summary>查看入参 / 出参 / 断言</summary>'
                                         % (' open' if (scls == 'bad' or (auto_open and idx == 1)) else ''))
                                p.append(render_att(st['att']))
                                p.append('</details>')
                        p.append('</li>')
                    p.append('</ul>')
                else:
                    p.append('<div class="meta">仅记录用例执行结果</div>')
                if sc['message']:
                    if is_bad:
                        p.append(f'<div class="err">{esc(sc["message"])}</div>')
                    elif sc['status'] == 'skipped':
                        label = '预期失败（计入跳过）' if 'XFAIL' in sc['message'] else '跳过原因'
                        p.append(f'<div class="meta">{label} · {esc(sc["message"])}</div>')
                if sc["duration"] is not None:
                    elapsed = f'{sc["duration"]} 毫秒' if sc["duration"] else '不足 1 毫秒'
                    p.append(f'<div class="meta">耗时 {elapsed}</div>')
                p.append('</div></details>')
            p.append('</div>')
        p.append('</section>')

    p.append('<div class="note"><b>怎么看这份报告：</b>每个「场景」= 一次完整的功能验证。'
             '展开场景可看到执行了哪几步（✓ 成功 / ✗ 失败），每步可再展开查看'
             '<b>入参</b>（发了什么请求）、<b>出参</b>（服务端返回什么）、<b>断言</b>（期望值与实际值）。'
             '账号口令、令牌等敏感字段已自动脱敏。</div>')
    p.append('</div></body></html>')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(p), encoding='utf-8')
    print(f'written: {out_path} ({out_path.stat().st_size} bytes)')
    print(f'modules={len(feats)} scenes={grand} failed={len(bad)}')


if __name__ == '__main__':
    main()
