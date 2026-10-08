import { describe, it, expect, beforeEach } from 'vitest';
import {
  buildProjectBackgroundMarkdown,
  collectMissingInfoNodes,
  composeShareDoc,
  loadShareDocTags,
  mergeShareDoc,
  missingSelectedTags,
  replaceUserSection,
  saveShareDocTags,
  SHARE_DOC_DIVIDER,
  SHARE_DOC_SECTION_TEMPLATE,
  shareDocValueText,
  splitShareDoc,
} from '../shareDoc';
import { computeInfoCompleteness, type ProjectInfoNode } from '../projectInfoTree';

/** 构造页面节点（默认值可按需覆盖） */
function node(partial: Partial<ProjectInfoNode> & { id: string; title: string }): ProjectInfoNode {
  return {
    project_id: null,
    parent_id: null,
    content_type: 'text',
    value: '',
    sort_order: 0,
    created_at: '2026-09-30 10:00:00',
    ...partial,
  };
}

/**
 * 两棵标签：
 *  车端软件（software.vehicle）：软件版本=v2.3.1（有值）、控制器品牌=空  → 2 个可填，空 1 → 未过半
 *  调度软件（software.schedule）：数据同步方式=空、版本号=空               → 2 个可填，空 2 → 过半
 */
const NODES: ProjectInfoNode[] = [
  node({ id: 'r1', title: '车端软件', sort_order: 0 }),
  node({ id: 'r1c1', parent_id: 'r1', title: '软件版本', value: 'v2.3.1', sort_order: 0 }),
  node({ id: 'r1c2', parent_id: 'r1', title: '控制器品牌', sort_order: 1 }),
  node({ id: 'r2', title: '调度软件', sort_order: 1 }),
  node({ id: 'r2c1', parent_id: 'r2', title: '数据同步方式', sort_order: 0 }),
  node({ id: 'r2c2', parent_id: 'r2', title: '版本号', sort_order: 1 }),
];

describe('标签完整度：缺省过半（mostlyEmpty）', () => {
  it('空值恰好一半不算缺省过半，过半才算', () => {
    const map = computeInfoCompleteness(NODES);
    // 车端软件：2 个可填、空 1 个 → 1×2 不 > 2，不算过半（但确实 incomplete）
    expect(map.get('r1')).toMatchObject({ total: 2, empty: 1, incomplete: true, mostlyEmpty: false });
    // 调度软件：2 个可填、空 2 个 → 4 > 2，算过半
    expect(map.get('r2')).toMatchObject({ total: 2, empty: 2, incomplete: true, mostlyEmpty: true });
  });

  it('只统计勾选标签里缺省过半的那些', () => {
    expect(missingSelectedTags(NODES, ['r1', 'r2']).map((n) => n.title)).toEqual(['调度软件']);
    expect(missingSelectedTags(NODES, ['r1'])).toEqual([]);
  });
});

describe('共享文档系统段生成', () => {
  it('只带入勾选标签，且只写有信息的节点', () => {
    const md = buildProjectBackgroundMarkdown('江苏常州多摩川混场项目', NODES, ['r1']);
    expect(md).toContain('# 问题共享文档');
    expect(md).toContain('> 项目：江苏常州多摩川混场项目');
    expect(md).toContain('## 项目背景信息');
    expect(md).toContain('### 车端软件');
    expect(md).toContain('#### 软件版本');
    expect(md).toContain('v2.3.1');
    // 空节点「控制器品牌」不出现；未勾选的「调度软件」整段不出现
    expect(md).not.toContain('控制器品牌');
    expect(md).not.toContain('调度软件');
    expect(md).not.toContain('（未填写）');
  });

  it('includeEmpty=true 时写全并标注未填写占位', () => {
    const md = buildProjectBackgroundMarkdown('P', NODES, ['r1', 'r2'], { includeEmpty: true });
    expect(md).toContain('#### 控制器品牌');
    expect(md).toContain('（未填写）');
    expect(md).toContain('### 调度软件');
  });

  it('一个标签都不勾选时只剩标题骨架', () => {
    const md = buildProjectBackgroundMarkdown('P', NODES, []);
    expect(md).toContain('## 项目背景信息');
    expect(md).not.toContain('### 车端软件');
  });

  it('下拉与附件按选中项/文件名渲染，空值给对应占位', () => {
    expect(shareDocValueText(node({
      id: 's1', title: '区域', content_type: 'select', value: { selected: '大陆(China Mainland)', options: [] },
    }))).toBe('大陆(China Mainland)');
    expect(shareDocValueText(node({
      id: 's2', title: '区域', content_type: 'select', value: { selected: '', options: [] },
    }))).toBe('（未选择）');
    expect(shareDocValueText(node({
      id: 'f1', title: '图纸', content_type: 'file', value: { name: 'layout.pdf' },
    }))).toBe('layout.pdf');
    expect(shareDocValueText(node({ id: 't1', title: '备注', value: '  有内容  ' }))).toBe('有内容');
  });
});

describe('共享文档两段式合并', () => {
  const system = buildProjectBackgroundMarkdown('P', NODES, ['r1']);

  it('补充段在重算时原样保留', () => {
    const first = mergeShareDoc('', system);
    const withUser = `${first}\n## 问题描述\n\n现场偶发丢包。\n`;
    // 抽屉里补完「调度软件」的信息后重算：新标签进来，补充段原样留着
    const filled = NODES.map((item) => (item.id === 'r2c1' ? { ...item, value: 'MQTT' } : item));
    const second = mergeShareDoc(withUser, buildProjectBackgroundMarkdown('P', filled, ['r1', 'r2']));

    expect(second).toContain('### 调度软件');
    expect(second).toContain('MQTT');
    expect(second).toContain('## 问题描述');
    expect(second).toContain('现场偶发丢包。');
    // 新的系统段与旧的分隔线之间不留空档，只有一条分隔线
    expect(second.split(SHARE_DOC_DIVIDER).length).toBe(2);
  });

  it('勾选了但整支没信息的标签，不进文档（避免空壳章节）', () => {
    const md = buildProjectBackgroundMarkdown('P', NODES, ['r1', 'r2']);
    expect(md).toContain('### 车端软件');
    expect(md).not.toContain('### 调度软件');
  });

  it('没有补充内容时不写分隔线', () => {
    expect(mergeShareDoc('', system)).toBe(system);
    expect(mergeShareDoc('', system)).not.toContain(SHARE_DOC_DIVIDER);
  });

  it('上传的文档（没有系统段）整篇都算补充段', () => {
    const uploaded = '# 我自己的文档\n\n一些说明\n';
    expect(splitShareDoc(uploaded)).toEqual({ system: '', user: uploaded });
    const merged = mergeShareDoc(uploaded, system);
    expect(merged.startsWith(system.trimEnd())).toBe(true);
    expect(merged).toContain('# 我自己的文档');
  });

  it('用户正文里的其它 --- 不会被当成分隔线', () => {
    const doc = `${system}\n${SHARE_DOC_DIVIDER}\n\n<!-- 备注 -->\n---\n更多内容\n`;
    const { user } = splitShareDoc(doc);
    expect(user).toContain('更多内容');
    expect(splitShareDoc(doc).system).toBe(system.trimEnd());
  });

  it('首次生成时补充段铺结构化骨架，用户填的内容在重算时不丢', () => {
    const first = mergeShareDoc(SHARE_DOC_SECTION_TEMPLATE, system);
    expect(first).toContain('## 问题描述');
    expect(first).toContain('## 前因后果');
    expect(first).toContain('## 涉及人员');

    // 用户把现象写进去，之后再改勾选（系统段重算）→ 正文仍在
    const written = first.replace('（现象、发生时间、影响范围）', '10:20 起 A 区小车全部离线。');
    const next = mergeShareDoc(written, buildProjectBackgroundMarkdown('P', NODES, ['r1']));
    expect(next).toContain('10:20 起 A 区小车全部离线。');
    expect(next).toContain('## 涉及人员');
  });
});

describe('AI 生成内容写入补充段（确认后才替换）', () => {
  const system = buildProjectBackgroundMarkdown('P', NODES, ['r1']);

  it('只替换分隔线以下，系统段原样保留', () => {
    const doc = mergeShareDoc('我自己写的描述', system);
    const next = replaceUserSection(doc, '## 问题描述\n- A 区小车离线');

    expect(next.startsWith(system.trimEnd())).toBe(true);
    expect(next).toContain('A 区小车离线');
    expect(next).not.toContain('我自己写的描述');
    // 仍只有一条分隔线
    expect(next.split(SHARE_DOC_DIVIDER).length).toBe(2);
  });

  it('还没选项目（无系统段）时整篇就是补充段，不补分隔线', () => {
    expect(replaceUserSection('', '## 问题描述\n- x')).toBe('## 问题描述\n- x\n');
    expect(replaceUserSection('', '## 问题描述\n- x')).not.toContain(SHARE_DOC_DIVIDER);
  });

  it('上传的文档（无系统段）同样是整篇替换', () => {
    expect(replaceUserSection('# 我的文档\n\n内容\n', '## 问题描述\n- y')).toBe('## 问题描述\n- y\n');
  });

  it('composeShareDoc：补充段为空时不写分隔线', () => {
    expect(composeShareDoc(system, '')).toBe(system);
    expect(composeShareDoc(system, '   \n ')).toBe(system);
  });

  it('生成后系统段再重算，AI 写进去的内容不丢', () => {
    const written = replaceUserSection(mergeShareDoc('', system), '## 问题描述\n- 现象A');
    const filled = NODES.map((item) => (item.id === 'r2c1' ? { ...item, value: 'MQTT' } : item));
    const next = mergeShareDoc(written, buildProjectBackgroundMarkdown('P', filled, ['r1', 'r2']));

    expect(next).toContain('MQTT');
    expect(next).toContain('现象A');
  });
});

describe('待补充节点清单', () => {
  it('按树先序列出可填但为空的节点，带标签路径', () => {
    const missing = collectMissingInfoNodes(NODES, ['r1', 'r2']);
    expect(missing.map((m) => m.path)).toEqual([
      '车端软件 / 控制器品牌',
      '调度软件 / 数据同步方式',
      '调度软件 / 版本号',
    ]);
    expect(missing[0].rootTitle).toBe('车端软件');
  });

  it('未勾选的标签不进入清单', () => {
    expect(collectMissingInfoNodes(NODES, ['r1']).map((m) => m.path)).toEqual(['车端软件 / 控制器品牌']);
  });
});

describe('勾选记忆（按项目存本机）', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('没记过时返回空集合，存了能读回来', () => {
    expect(loadShareDocTags('P1').size).toBe(0);
    saveShareDocTags('P1', ['r1', 'r2']);
    expect([...loadShareDocTags('P1')].sort()).toEqual(['r1', 'r2']);
    // 按项目隔离
    expect(loadShareDocTags('P2').size).toBe(0);
  });
});
