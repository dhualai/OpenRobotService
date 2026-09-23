import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  computeInfoCompleteness,
  countInfoValues,
  createInfoNode,
  deleteInfoNode,
  encodeInfoValue,
  flattenInfoTree,
  hasFieldValue,
  importInfoTree,
  loadHistoryLatest,
  loadHistorySeen,
  loadInfoNodeChanges,
  loadInfoNodeMarks,
  loadInfoNodes,
  loadProjectActivity,
  moveInfoNode,
  normalizeImportNodes,
  patchInfoNode,
  REGION_MAINLAND,
  removeInfoNode,
  resetInfoTreeToTemplate,
  saveHistorySeen,
  setInfoNodeValue,
  SUBTREE_HISTORY_LIMIT,
  subtreeNodeIds,
  toggleInfoNodeMark,
  unseenHistoryChain,
  unseenHistoryNodes,
  unseenHistoryRoots,
  updateInfoNode,
  visibleInfoNodes,
  type ProjectInfoNode,
} from '../projectInfoTree';
import {
  createCustomInfoNodeApi,
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoNodeChangeSummaryApi,
  fetchInfoNodeChangesApi,
  fetchInfoNodeMarksApi,
  fetchInfoTree,
  fetchProjectActivityApi,
  importInfoTreeApi,
  moveInfoNodeApi,
  setInfoNodeValueApi,
  toggleInfoNodeMarkApi,
  updateInfoNodeApi,
  type ApiInfoNode,
} from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  createCustomInfoNodeApi: vi.fn(),
  setInfoNodeValueApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  fetchInfoNodeChangesApi: vi.fn(),
  fetchInfoNodeChangeSummaryApi: vi.fn(),
  fetchInfoNodeMarksApi: vi.fn(),
  toggleInfoNodeMarkApi: vi.fn(),
  fetchProjectActivityApi: vi.fn(),
}));

const TS = '2026-09-14 10:00:00';

/** 构造后端行（默认值可按需覆盖） */
function apiNode(partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode {
  return {
    project_id: 'CODE-A',
    parent_id: null,
    title: '未命名节点',
    content_type: 'text',
    value: null,
    sort_order: 0,
    created_at: TS,
    updated_at: TS,
    ...partial,
  };
}

const selectOf = (node: ProjectInfoNode) => node.value as { selected: string; options: string[] };

describe('后端行 <-> 页面节点（编解码）', () => {
  it('text 原样透传（含看起来像 JSON 的内容），select / file 按 content_type 解码', () => {
    const tree: ApiInfoNode[] = [
      apiNode({
        id: 'r1',
        title: '基础信息',
        sort_order: 0,
        children: [
          apiNode({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '{"a":1}', sort_order: 0 }),
          apiNode({
            id: 'c2', parent_id: 'r1', title: '项目类型', content_type: 'select', sort_order: 1,
            value: { selected: 'PK 项目', options: ['PK 项目', '试点项目'] },
          }),
          apiNode({ id: 'c3', parent_id: 'r1', title: '坏数据', content_type: 'select', value: 'not-json', sort_order: 2 }),
          apiNode({ id: 'c4', parent_id: 'r1', title: '附件', content_type: 'file', sort_order: 3, value: { name: 'a.pdf', resource_id: '7', size: 100 } }),
        ],
      }),
    ];
    const flat = flattenInfoTree(tree);
    const byId = new Map(flat.map((node) => [node.id, node]));

    expect(byId.get('c1')!.value).toBe('{"a":1}');
    expect(selectOf(byId.get('c2')!)).toEqual({ selected: 'PK 项目', options: ['PK 项目', '试点项目'] });
    expect(selectOf(byId.get('c3')!)).toEqual({ selected: '', options: [] });
    expect(byId.get('c4')!.value).toEqual({ name: 'a.pdf', resource_id: '7', size: 100 });
    // 空 value 的 text 节点按空字符串处理
    expect(byId.get('r1')!.value).toBe('');
    // parent_id 以树的层级为准
    expect(byId.get('c2')!.parent_id).toBe('r1');
    expect(byId.get('r1')!.parent_id).toBeNull();
  });

  it('节点定义类字段（options / titleOptions / allow_custom / is_custom）随行下发', () => {
    const [node] = flattenInfoTree([apiNode({
      id: 'r1', title: '基础信息', allow_custom: true,
      options: ['托盘', '料笼'],
      titleOptions: ['基础信息', '项目信息'],
    })]);
    expect(node.options).toEqual(['托盘', '料笼']);
    expect(node.titleOptions).toEqual(['基础信息', '项目信息']);
    expect(node.allow_custom).toBe(true);
    expect(node.is_custom).toBe(false); // project_id 为空的全局字段
  });

  it('encodeInfoValue：字符串去空白，空串归 null，结构化值原样交给服务端编码', () => {
    expect(encodeInfoValue('abc')).toBe('abc');
    expect(encodeInfoValue('   ')).toBeNull();
    expect(encodeInfoValue(null)).toBeNull();
    expect(encodeInfoValue({ selected: '是', options: ['是', '否'] })).toEqual({ selected: '是', options: ['是', '否'] });
  });
});

describe('节点 CRUD（走 /api/admin/info-nodes）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadInfoNodes 读取整树并展平、按 sort_order 升序', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValue([
      apiNode({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [
          apiNode({ id: 'c2', parent_id: 'r1', title: '第二', sort_order: 1 }),
          apiNode({ id: 'c1', parent_id: 'r1', title: '第一', sort_order: 0 }),
        ],
      }),
    ]);
    const nodes = await loadInfoNodes('P1');
    expect(fetchInfoTree).toHaveBeenCalledWith('P1');
    expect(nodes.map((node) => node.id)).toEqual(['r1', 'c1', 'c2']);
  });

  it('创建节点：id 由后端生成，「改树」与「增补」走两个入口', async () => {
    vi.mocked(createInfoNodeApi).mockResolvedValue(
      apiNode({ id: 'server-1', project_id: null, parent_id: 'parent-1', title: '新节点', sort_order: 30 }),
    );
    vi.mocked(createCustomInfoNodeApi).mockResolvedValue(
      apiNode({ id: 'server-2', project_id: 'P1', parent_id: 'parent-1', title: '增补字段', sort_order: 40 }),
    );

    const created = await createInfoNode('P1', 'parent-1', 30, '新节点', true);
    // 载荷里不再带前端生成的 id（服务端按 node_key 保证唯一）
    expect(createInfoNodeApi).toHaveBeenCalledWith('P1', {
      parent_id: 'parent-1', title: '新节点', content_type: 'text', sort_order: 30,
    });
    expect(created.id).toBe('server-1');
    expect(created.title).toBe('新节点');

    const custom = await createInfoNode('P1', 'parent-1', 40, '增补字段', false);
    expect(createCustomInfoNodeApi).toHaveBeenCalledWith('P1', {
      parent_id: 'parent-1', title: '增补字段', content_type: 'text', sort_order: 40,
    });
    expect(custom.id).toBe('server-2');
    expect(custom.project_id).toBe('P1');
    expect(custom.is_custom).toBe(false); // 接口返回的是后端口径，页面不自行推断
  });

  it('setInfoNodeValue 结构化值不序列化，直接交服务端按值类型编码', async () => {
    const node: ProjectInfoNode = {
      id: 'n1', project_id: null, parent_id: null, title: '项目类型', content_type: 'select',
      value: { selected: '', options: [] }, sort_order: 0, created_at: TS,
    };
    vi.mocked(setInfoNodeValueApi).mockResolvedValue(
      apiNode({ id: 'n1', project_id: null, content_type: 'select', value: { selected: '试点项目', options: ['试点项目'] } }),
    );
    const updated = await setInfoNodeValue(node, { selected: '试点项目', options: ['试点项目'] }, 'P1');
    expect(setInfoNodeValueApi).toHaveBeenCalledWith('n1', 'P1', { selected: '试点项目', options: ['试点项目'] });
    expect(selectOf(updated).selected).toBe('试点项目');
  });

  it('updateInfoNode 只改定义（名称/类型/选项），不带值字段', async () => {
    const node: ProjectInfoNode = {
      id: 'n1', project_id: 'P1', parent_id: null, title: '项目类型', content_type: 'select',
      value: { selected: '', options: [] }, sort_order: 0, created_at: TS, is_custom: true,
    };
    vi.mocked(updateInfoNodeApi).mockResolvedValue(
      apiNode({ id: 'n1', project_id: 'P1', title: '项目类型', content_type: 'select', options: ['甲', '乙'] }),
    );
    const updated = await updateInfoNode(node, { title: '项目类型', options: ['甲', '乙'] });
    expect(updateInfoNodeApi).toHaveBeenCalledWith('n1', { title: '项目类型', options: ['甲', '乙'] });
    expect(updated.options).toEqual(['甲', '乙']);
  });

  it('moveInfoNode / deleteInfoNode 调用对应接口', async () => {
    const node = { id: 'n1', project_id: 'P1', parent_id: null, title: 'x', content_type: 'text' as const, value: '', sort_order: 0, created_at: TS };
    vi.mocked(moveInfoNodeApi).mockResolvedValue(apiNode({ id: 'n1', parent_id: 'p2', sort_order: 1 }));
    vi.mocked(deleteInfoNodeApi).mockResolvedValue(undefined);

    const moved = await moveInfoNode(node, 'p2', 1);
    expect(moveInfoNodeApi).toHaveBeenCalledWith('n1', 'p2', 1);
    expect(moved.parent_id).toBe('p2');

    await deleteInfoNode('n1');
    expect(deleteInfoNodeApi).toHaveBeenCalledWith('n1');
  });

  it('importInfoTree 归一化后调用导入接口并返回写入数量', async () => {
    vi.mocked(importInfoTreeApi).mockResolvedValue(2);
    const imported = await importInfoTree('P1', { nodes: [{ title: '父', children: [{ title: '子' }] }] });
    expect(importInfoTreeApi).toHaveBeenCalledWith('P1', [
      expect.objectContaining({ title: '父', children: [expect.objectContaining({ title: '子' })] }),
    ]);
    expect(imported).toBe(2);
  });
});

describe('导入内容归一化', () => {
  it('接受数组 / {nodes} / {info_nodes} 三种格式', () => {
    const raw = [{ title: 'A' }];
    expect(normalizeImportNodes(raw)[0].title).toBe('A');
    expect(normalizeImportNodes({ nodes: raw })[0].title).toBe('A');
    expect(normalizeImportNodes({ info_nodes: raw })[0].title).toBe('A');
  });

  it('补序号 / 缺省字段，保留字符串值，结构化值原样提交由后端按类型编码', () => {
    const [node] = normalizeImportNodes([
      { title: '下拉', content_type: 'select', value: { selected: '是', options: ['是'] } },
      { title: '文字', value: '原文' },
    ]);
    expect(node.sort_order).toBe(10);
    expect(node.value).toEqual({ selected: '是', options: ['是'] });
  });

  it('无法识别的格式直接抛错', () => {
    expect(() => normalizeImportNodes({ foo: 1 })).toThrow();
  });

  it('「标题: 内容」紧凑映射：文字 / 数组(下拉) / 对象(子节点) 三种写法可混用', () => {
    const tree = normalizeImportNodes({
      info_nodes: {
        基础信息: {
          客户信息: '',
          订单信息: { ERP: '' },
          项目类型: ['试点项目', '大客户项目'],
        },
      },
    });

    expect(tree.map((node) => node.title)).toEqual(['基础信息']);
    const children = tree[0].children!;
    expect(children.map((node) => node.title)).toEqual(['客户信息', '订单信息', '项目类型']);

    expect(children[0].content_type).toBe('text');
    expect(children[0].value).toBe('');

    // 对象 → 子节点（递归）
    expect(children[1].children!.map((node) => node.title)).toEqual(['ERP']);

    // 数组 → 下拉节点，选项进节点定义（不入值）
    expect(children[2].content_type).toBe('select');
    expect(children[2].value).toEqual({ selected: '', options: ['试点项目', '大客户项目'] });
  });

  it('options 清单（后端 YAML 模板写法）自动补成下拉值', () => {
    const [node] = normalizeImportNodes([{ title: '载具类型', options: ['托盘', '料笼'] }]);
    expect(node.content_type).toBe('select');
    expect(node.value).toEqual({ selected: '', options: ['托盘', '料笼'] });
  });
});

describe('区域细分字段联动（区域选项 → 省份/地区 | 具体国家）', () => {
  /** 项目区域/地点 下：区域选项(下拉) + 省份 + 地区 + 具体国家 + 用户自建字段 */
  const regionNodes = (selected: string, options = [REGION_MAINLAND, '亚洲Asia']): ProjectInfoNode[] => [
    { id: 'p', project_id: 'P1', parent_id: null, title: '项目区域/地点', content_type: 'text', value: '', sort_order: 0, created_at: TS },
    { id: 'd', project_id: 'P1', parent_id: 'p', title: '区域选项', content_type: 'select', value: { selected, options }, sort_order: 0, created_at: TS },
    { id: 's', project_id: 'P1', parent_id: 'p', title: '省份', content_type: 'text', value: '浙江省', sort_order: 1, created_at: TS },
    { id: 'a', project_id: 'P1', parent_id: 'p', title: '地区', content_type: 'text', value: '安吉县', sort_order: 2, created_at: TS },
    { id: 'c', project_id: 'P1', parent_id: 'p', title: '具体国家', content_type: 'text', value: '', sort_order: 3, created_at: TS },
    { id: 'x', project_id: 'P1', parent_id: 'p', title: '自建字段', content_type: 'text', value: '', sort_order: 4, created_at: TS },
  ];
  const titlesOf = (nodes: ProjectInfoNode[]) => visibleInfoNodes(nodes).map((node) => node.title);

  it('未选择区域：省份/地区与具体国家都不显示', () => {
    expect(titlesOf(regionNodes(''))).toEqual(['项目区域/地点', '区域选项', '自建字段']);
  });

  it('选中大陆：显示省份/地区，隐藏具体国家（原值保留在数据里）', () => {
    const nodes = regionNodes(REGION_MAINLAND);
    expect(titlesOf(nodes)).toEqual(['项目区域/地点', '区域选项', '省份', '地区', '自建字段']);
    expect(nodes.find((node) => node.id === 's')?.value).toBe('浙江省');
  });

  it('选中其它区域：显示具体国家，隐藏省份/地区', () => {
    expect(titlesOf(regionNodes('亚洲Asia'))).toEqual(['项目区域/地点', '区域选项', '具体国家', '自建字段']);
  });

  it('换成普通下拉（选项里没有大陆）：不做联动，细分字段照常显示', () => {
    expect(titlesOf(regionNodes('甲', ['甲', '乙']))).toContain('省份');
    expect(titlesOf(regionNodes('甲', ['甲', '乙']))).toContain('具体国家');
  });
});

describe('编辑历史（操作记录读接口 + 本机已读水位）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadInfoNodeChanges / loadHistoryLatest 按项目与节点查后端', async () => {
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      {
        id: 'h1', node_id: 'n1', parent_id: null, node_title: '客户信息', action: 'update',
        operator: 'zhangsan', operator_name: '张三', detail: '把内容从「空」改为「中力」', created_at: TS,
      },
    ]);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ n1: 'h1' });

    const records = await loadInfoNodeChanges('P1', 'n1');
    expect(fetchInfoNodeChangesApi).toHaveBeenCalledWith('P1', 'n1', {});
    expect(records[0].operator_name).toBe('张三');
    expect(records[0].detail).toBe('把内容从「空」改为「中力」');

    // 一级标签：连整棵子树的记录一起取，条数上限也放宽（后端上限 500）
    await loadInfoNodeChanges('P1', 'r1', { includeDescendants: true, limit: SUBTREE_HISTORY_LIMIT });
    expect(fetchInfoNodeChangesApi).toHaveBeenLastCalledWith('P1', 'r1', {
      includeDescendants: true,
      limit: SUBTREE_HISTORY_LIMIT,
    });

    await expect(loadHistoryLatest('P1')).resolves.toEqual({ n1: 'h1' });
    expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledWith('P1');
  });

  it('已读水位按「项目 + 用户」分别存本机，坏数据回退空表', () => {
    expect(loadHistorySeen('CODE-A', 'zhang')).toEqual({});
    saveHistorySeen('CODE-A', { n1: 'h1' }, 'zhang');
    expect(loadHistorySeen('CODE-A', 'zhang')).toEqual({ n1: 'h1' });
    expect(loadHistorySeen('CODE-B', 'zhang')).toEqual({}); // 别的项目不受影响
    // 别人的已读状态与本机用户无关：换个登录用户，未看过的照样出红点
    expect(loadHistorySeen('CODE-A', 'li')).toEqual({});

    localStorage.setItem('project-info-tree:history-seen:CODE-A:zhang', 'not-json');
    expect(loadHistorySeen('CODE-A', 'zhang')).toEqual({});
  });

  it('unseenHistoryNodes：最新记录 id 与已读水位不一致（或从没看过）即出新红点', () => {
    // 记录 id 是时间有序的 UUIDv7：同秒内的新记录 id 也不同，不会漏
    const latest = { n1: 'a-2', n2: 'b-2', n3: 'c-1' };
    const seen = { n1: 'a-2', n2: 'b-1', n3: '' };
    // n1 看过的就是最新那条 → 不冒红点；n2 看过之后又有新记录 → 冒；n3 本机没水位 → 冒
    expect([...unseenHistoryNodes(latest, seen)].sort()).toEqual(['n2', 'n3']);
    // 没有任何记录的节点不参与
    expect(unseenHistoryNodes({}, {})).toEqual(new Set());
    expect(unseenHistoryNodes({ n9: '' }, {})).toEqual(new Set());
  });

  it('unseenHistoryRoots：未读变动归到所在的一级标签（多层上溯），删除的节点不归', () => {
    const nodes = [
      { id: 'r1', parent_id: null },
      { id: 'c1', parent_id: 'r1' },
      { id: 'g1', parent_id: 'c1' },
      { id: 'r2', parent_id: null },
    ];
    // 孙节点归到 r1，根自身未读归自己；无关的根不出现
    expect(unseenHistoryRoots(nodes, new Set(['g1', 'r2']))).toEqual(new Set(['r1', 'r2']));
    // 一个标签下多个未读子节点只出一个根
    expect(unseenHistoryRoots(nodes, new Set(['c1', 'g1']))).toEqual(new Set(['r1']));
    // 树里已删除（只剩记录）的节点不往上归
    expect(unseenHistoryRoots(nodes, new Set(['missing']))).toEqual(new Set());
    expect(unseenHistoryRoots(nodes, new Set())).toEqual(new Set());
  });

  it('unseenHistoryChain：未读节点自己 + 每一层上级都出点', () => {
    const nodes = [
      { id: 'r1', parent_id: null },
      { id: 'c1', parent_id: 'r1' },
      { id: 'g1', parent_id: 'c1' },
      { id: 'r2', parent_id: null },
    ];
    // g1 未读：g1 → c1 → r1 三行都带点；r2 那支不受影响
    expect(unseenHistoryChain(nodes, new Set(['g1']))).toEqual(new Set(['g1', 'c1', 'r1']));
    // 根自身未读只带自己；两处未读合并且不重复
    expect(unseenHistoryChain(nodes, new Set(['r2']))).toEqual(new Set(['r2']));
    expect(unseenHistoryChain(nodes, new Set(['g1', 'r2']))).toEqual(new Set(['g1', 'c1', 'r1', 'r2']));
    // 树里已删除（只剩记录）的节点不出点、也不往上挂
    expect(unseenHistoryChain(nodes, new Set(['missing']))).toEqual(new Set());
  });

  it('subtreeNodeIds：节点自己 + 全部子孙（点开一处历史 = 这棵子树都算看过）', () => {
    const nodes = [
      { id: 'r1', parent_id: null },
      { id: 'c1', parent_id: 'r1' },
      { id: 'g1', parent_id: 'c1' },
      { id: 'r2', parent_id: null },
    ];
    expect(subtreeNodeIds(nodes, 'r1').sort()).toEqual(['c1', 'g1', 'r1']);
    expect(subtreeNodeIds(nodes, 'c1').sort()).toEqual(['c1', 'g1']);
    expect(subtreeNodeIds(nodes, 'r2')).toEqual(['r2']);
    expect(subtreeNodeIds(nodes, 'missing')).toEqual(['missing']); // 树里没有的 id 只回它自己
  });
});

describe('关注（星标）与项目动态', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadInfoNodeMarks / toggleInfoNodeMark 直通关注接口', async () => {
    vi.mocked(fetchInfoNodeMarksApi).mockResolvedValue(['n1', 'n2']);
    vi.mocked(toggleInfoNodeMarkApi).mockResolvedValueOnce(true).mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    await expect(loadInfoNodeMarks('P1')).resolves.toEqual(['n1', 'n2']);
    expect(fetchInfoNodeMarksApi).toHaveBeenCalledWith('P1');

    // 展示卡只给 nodeId（后端按已有标注切换），编辑页会带上 projectId
    await expect(toggleInfoNodeMark('n1')).resolves.toBe(true);
    await expect(toggleInfoNodeMark('n1')).resolves.toBe(false); // 再点即取消
    expect(toggleInfoNodeMarkApi).toHaveBeenCalledWith('n1', undefined);
    await expect(toggleInfoNodeMark('n1', 'P1')).resolves.toBe(true);
    expect(toggleInfoNodeMarkApi).toHaveBeenLastCalledWith('n1', 'P1');
  });

  it('loadProjectActivity 返回被关注节点的最新变动（只含变动内容所需字段）', async () => {
    vi.mocked(fetchProjectActivityApi).mockResolvedValue([
      {
        node_id: 'n1', node_title: '客户信息', root_title: '基础信息', action: 'update',
        detail: '把内容从「空」改为「中力」', created_at: TS,
      },
    ]);
    const list = await loadProjectActivity('P1');
    expect(fetchProjectActivityApi).toHaveBeenCalledWith('P1');
    expect(list[0].detail).toBe('把内容从「空」改为「中力」');
    expect(list[0].root_title).toBe('基础信息');
  });
});

describe('本地纯函数', () => {
  const base: ProjectInfoNode[] = [
    { id: 'a', project_id: 'P1', parent_id: null, title: 'A', content_type: 'text', value: 'x', sort_order: 0, created_at: TS },
    { id: 'b', project_id: 'P1', parent_id: 'a', title: 'B', content_type: 'text', value: '', sort_order: 0, created_at: TS },
    { id: 'c', project_id: 'P1', parent_id: 'b', title: 'C', content_type: 'text', value: '', sort_order: 0, created_at: TS },
    { id: 'd', project_id: 'P1', parent_id: null, title: 'D', content_type: 'text', value: '', sort_order: 1, created_at: TS },
  ];

  it('patchInfoNode 只改目标节点', () => {
    const next = patchInfoNode(base, 'b', { title: 'B2' });
    expect(next.find((node) => node.id === 'b')!.title).toBe('B2');
    expect(next.find((node) => node.id === 'a')).toEqual(base[0]);
  });

  it('removeInfoNode 连带删除整棵子树', () => {
    expect(removeInfoNode(base, 'b').map((node) => node.id)).toEqual(['a', 'd']);
  });

  it('computeInfoCompleteness 按一级标签统计末级填写情况', () => {
    const completeness = computeInfoCompleteness(base);
    expect(completeness.get('a')).toEqual({ total: 1, empty: 1, incomplete: true }); // a → b → c，末级只有 C 且未填写
    expect(completeness.get('d')).toEqual({ total: 1, empty: 1, incomplete: true });
  });

  it('resetInfoTreeToTemplate 删掉增补节点、清掉全局字段的值，下拉选项保留', () => {
    const tree: ProjectInfoNode[] = [
      { ...base[0], value: '中力' },                                             // 文字：清成空串
      { ...base[1], content_type: 'select', value: { selected: '试点项目', options: ['试点项目', 'PK项目'] } },
      { ...base[2], content_type: 'file', value: { name: 'a.pdf', resource_id: '7', size: 100 } },
      { ...base[3], value: '' },                                                 // 本来就没填：原样返回
      { ...base[0], id: 'x1', is_custom: true, value: '导入/同步加进来的' },       // 增补节点：连节点一起没
      { ...base[1], id: 'x2', is_custom: true, parent_id: 'x1', value: '' },      // 增补节点的子节点同样没
    ];
    const next = resetInfoTreeToTemplate(tree);

    expect(next.map((node) => node.id)).toEqual(['a', 'b', 'c', 'd']);            // 只剩全局（模板）字段
    expect(next[0].value).toBe('');
    expect(next[1].value).toEqual({ selected: '', options: ['试点项目', 'PK项目'] });  // 选项属于字段定义，不能清
    expect(next[2].value).toBeNull();
    expect(next[3]).toBe(tree[3]);                                              // 空值节点没被重建
    expect(tree[0].value).toBe('中力');                                          // 不改原数组（乐观更新后再回滚得回来）
    expect(tree).toHaveLength(6);                                               // 原数组本身一个不少
  });
});

describe('已填写信息统计（展示页裁剪空内容的依据）', () => {
  const leaf = (id: string, parent: string | null, title: string, value: unknown, type = 'text'): ProjectInfoNode =>
    ({ id, project_id: 'P1', parent_id: parent, title, content_type: type as ProjectInfoNode['content_type'], value, sort_order: 0, created_at: TS });

  // a ─┬─ b（有值）  a 自身没值：只剩一个空的父节点，父节点不该被当成字段
  //    └─ e ─┬─ f（有值）
  //          └─ g（空，两种类型各测一遍）
  const tree: ProjectInfoNode[] = [
    leaf('a', null, '基础信息', ''),
    leaf('b', 'a', '客户信息', '中力'),
    leaf('e', 'a', '区域', ''),
    leaf('f', 'e', '省份', '浙江'),
    leaf('g', 'e', '地区', ''),
    leaf('h', null, '硬件', ''),
    leaf('i', 'h', '载具', '', 'select'),
  ];

  it('只数末级字段，父节点按子树里已填写的条数累计', () => {
    const counts = countInfoValues(tree);
    expect(counts.get('b')).toBe(1);
    expect(counts.get('g')).toBe(0);
    expect(counts.get('e')).toBe(1); // e 自己不落值，靠 f
    expect(counts.get('a')).toBe(2); // b + f
    expect(counts.get('h')).toBe(0); // 底下只有一个空的下拉
  });

  it('hasFieldValue 按类型判空：下拉看选中项、附件看文件名、文本去空白', () => {
    expect(hasFieldValue(leaf('t1', null, 'A', ' x '))).toBe(true);
    expect(hasFieldValue(leaf('t2', null, 'A', '   '))).toBe(false);
    expect(hasFieldValue(leaf('t3', null, 'A', ''))).toBe(false);
    expect(hasFieldValue(leaf('t4', null, 'A', null))).toBe(false);
    expect(hasFieldValue(leaf('s1', null, 'A', { selected: '托盘', options: [] }, 'select'))).toBe(true);
    expect(hasFieldValue(leaf('s2', null, 'A', { selected: '', options: ['托盘'] }, 'select'))).toBe(false);
    expect(hasFieldValue(leaf('f1', null, 'A', { name: '方案.pdf' }, 'file'))).toBe(true);
    expect(hasFieldValue(leaf('f2', null, 'A', { name: '' }, 'file'))).toBe(false);
    expect(hasFieldValue(leaf('f3', null, 'A', {}, 'image'))).toBe(false);
  });

  // 「有值又有子节点」的节点：车型1 是下拉（选中型号），下面还挂着「数量」
  const withValues: ProjectInfoNode[] = [
    leaf('h', null, '硬件', ''),
    leaf('v', 'h', '车辆', ''),
    leaf('m', 'v', '车型1', { selected: 'XC1051', options: ['XC1051'] }, 'select'),
    leaf('q', 'm', '数量', ''), // 数量没填
  ];

  it('节点自己的值也计入：车型1 选了型号、数量没填时整条分支仍然有值', () => {
    const counts = countInfoValues(withValues);
    expect(counts.get('m')).toBe(1); // 型号本身，数量为空不算
    expect(counts.get('v')).toBe(1); // 车辆自己没有值，靠车型1
    expect(counts.get('h')).toBe(1);
  });

  it('完整度统计把带值的非末级节点算成一条：数量空 → 该标签信息不全', () => {
    // 可填的是 车型1（已填）与 数量（空）；纯文本的 硬件 / 车辆 是分组，不算条目
    expect(computeInfoCompleteness(withValues).get('h'))
      .toEqual({ total: 2, empty: 1, incomplete: true });
  });

  it('车型1 与数量都填好时该标签信息完整', () => {
    const filled = withValues.map((node) =>
      node.id === 'q' ? { ...node, value: '6 台' } : node);
    expect(computeInfoCompleteness(filled).get('h'))
      .toEqual({ total: 2, empty: 0, incomplete: false });
  });
});
