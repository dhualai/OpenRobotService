import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import TicketShareDocSetting from '../TicketShareDocSetting';
import { fetchInfoTree, type ApiInfoNode } from '@/api/infoNodes';
import { createTicket } from '@/api/ticket';

// 弹层与 Toast 桩掉：只验证逻辑，不渲染真实浮层
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
  Popup: ({ visible, children }: { visible?: boolean; children?: React.ReactNode }) =>
    (visible ? <div>{children}</div> : null),
}));

// 数据源是后端信息树接口：mock 出固定的树（与 ProjectInfoCard 测试同一套桩）
vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  createCustomInfoNodeApi: vi.fn(),
  setInfoNodeValueApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  fetchInfoNodeMarksApi: vi.fn(),
  toggleInfoNodeMarkApi: vi.fn(),
  fetchProjectActivityApi: vi.fn(),
  fetchInfoNodeChangeSummaryApi: vi.fn(),
}));

// 信息树编辑页在单测里不必真渲染（抽屉内容）
vi.mock('@/pages/admin/ProjectInfoEdit', () => ({
  default: () => <div data-testid="info-editor" />,
}));

// 文档编辑入口：桩成占位，避免拖进 markdown 编辑器
vi.mock('@/shared/components/SpecDocField', () => ({
  default: ({ value }: { value: { content: string } | null }) => (
    <div data-testid="spec-doc-field">{value?.content ?? ''}</div>
  ),
}));

// 选人：桩成一个按钮，直接回调固定的人，便于验证「建补充工单」
vi.mock('@/shared/components/UserSelect', () => ({
  default: ({ onChange }: { onChange: (u: { id: string; username: string; name: string }) => void }) => (
    <button type="button" onClick={() => onChange({ id: 'u1', username: 'zhangsan', name: '张三' })}>
      选张三
    </button>
  ),
}));

vi.mock('@/api/client', () => ({
  createRequest: () => vi.fn().mockResolvedValue([]),
}));

vi.mock('@/api/ticket', () => ({
  createTicket: vi.fn().mockResolvedValue({ id: 9527 }),
}));

const TS = '2026-09-30 10:00:00';
const node = (partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode => ({
  project_id: 'P1',
  parent_id: null,
  title: '节点',
  content_type: 'text',
  value: null,
  sort_order: 0,
  created_at: TS,
  updated_at: TS,
  ...partial,
});

/**
 * 车端软件：软件版本=v2.3.1（有值）+ 控制器品牌=空     → 2 个可填、空 1 → 不过半
 * 调度软件：数据同步方式=空 + 版本号=空                 → 2 个可填、空 2 → 过半（出感叹号）
 */
const TREE: ApiInfoNode[] = [
  node({
    id: 'r1',
    title: '车端软件',
    sort_order: 0,
    children: [
      node({ id: 'r1c1', parent_id: 'r1', title: '软件版本', value: 'v2.3.1', sort_order: 0 }),
      node({ id: 'r1c2', parent_id: 'r1', title: '控制器品牌', sort_order: 1 }),
    ],
  }),
  node({
    id: 'r2',
    title: '调度软件',
    sort_order: 1,
    children: [
      node({ id: 'r2c1', parent_id: 'r2', title: '数据同步方式', sort_order: 0 }),
      node({ id: 'r2c2', parent_id: 'r2', title: '版本号', sort_order: 1 }),
    ],
  }),
];

const renderSetting = (onChange = vi.fn()) => {
  const utils = render(
    <TicketShareDocSetting
      projectId="P1"
      projectName="江苏常州多摩川混场项目"
      value={null}
      onChange={onChange}
    />,
  );
  return { ...utils, onChange };
};

/**
 * 等「标签池刚进 DOM」的那一瞬间就返回，不等 React 的 passive effect 跑完。
 * 用来复现 CI 慢机器上的时序：RTL 的 findBy 靠 MutationObserver 触发，可能在
 * 「标签已渲染、初始化勾选还没生效」的窗口里就返回了（deploy-split gate 就是这样偶发红的）。
 */
const waitFirstTagPoolRender = () =>
  new Promise<void>((resolve) => {
    const observer = new MutationObserver(() => {
      if (screen.queryByRole('button', { name: '全选' })) {
        observer.disconnect();
        resolve();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  });

describe('问题共享文档设置（提单弹窗内）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(fetchInfoTree).mockResolvedValue(TREE);
  });

  it('渲染项目的一级标签，缺省过半的标签带感叹号', async () => {
    renderSetting();
    const vehicle = await screen.findByRole('button', { name: /车端软件/ });
    const schedule = screen.getByRole('button', { name: /调度软件/ });

    // 缺省过半才出感叹号：车端软件 2 个可填只空 1 个 → 不出
    expect(await within(schedule).findByLabelText('信息缺失')).toBeTruthy();
    expect(within(vehicle).queryByLabelText('信息缺失')).toBeNull();
  });

  it('默认全不选：无缺信息提示条，两个操作按钮常驻、暂时跳过默认在', async () => {
    renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });

    // 默认全不选 → 没有勾选中的标签 → 不出缺信息提示条
    expect(screen.queryByText(/当前问题缺少有效信息/)).toBeNull();
    // 但「补充信息 / 提单给他人补充 / 暂时跳过」依旧常驻（不再只在缺信息时出现）
    expect(screen.getByRole('button', { name: '补充信息' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '提单给他人补充' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '暂时跳过' })).toBeTruthy();
    // 全选 / 全部取消 一键操作
    expect(screen.getByRole('button', { name: '全选' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '全部取消' })).toBeTruthy();
  });

  it('全选后提示条列出缺省过半的标签，并给出三种处理', async () => {
    renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });

    fireEvent.click(screen.getByRole('button', { name: '全选' }));

    // 全选后只有「调度软件」缺省过半
    expect(await screen.findByText(/当前问题缺少有效信息/)).toBeTruthy();
    expect(screen.getByText(/（调度软件）/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '补充信息' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '提单给他人补充' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '暂时跳过' })).toBeTruthy();
  });

  it('标签池下方常驻感叹号口径说明', async () => {
    renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });

    // 与项目信息管理页同一套图例：提醒用户标签旁 ! 的含义
    expect(screen.getByText(/标签旁的 ! = 该标签下过半信息未填写/)).toBeTruthy();
  });

  it('全选后才把项目背景信息写进在线文档（只带有信息的节点）', async () => {
    const { onChange } = renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });

    // 默认全不选：不自动写文档
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '全选' }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const draft = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(draft.content).toContain('> 项目：江苏常州多摩川混场项目');
    expect(draft.content).toContain('### 车端软件');
    expect(draft.content).toContain('v2.3.1');
    expect(draft.content).not.toContain('控制器品牌');
    // 分隔线以下预置结构化骨架（问题描述 / 前因后果 / 涉及人员）
    expect(draft.content).toContain('## 问题描述');
    expect(draft.content).toContain('## 前因后果');
    expect(draft.content).toContain('## 涉及人员');
  });

  // CI 慢机器竞态回归（deploy-split gate 曾挂在这里）：在「标签刚进 DOM、初始化勾选还没生效」
  // 的窗口里点「全选」，勾选不能被随后的初始化覆盖 —— 覆盖了就不再有 system 段、onChange 不再触发。
  it('标签首帧刚进 DOM 就点「全选」，勾选不会被初始化覆盖', async () => {
    const { onChange } = renderSetting();
    await waitFirstTagPoolRender();
    fireEvent.click(screen.getByRole('button', { name: '全选' }));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const draft = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(draft.content).toContain('### 车端软件');
    expect(screen.getByRole('button', { name: /车端软件/ }).getAttribute('aria-pressed')).toBe('true');
  });

  it('「全部取消」清空勾选，写过的文档内容保留不被清掉', async () => {
    const { onChange } = renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    onChange.mockClear();

    fireEvent.click(screen.getByRole('button', { name: '全部取消' }));

    const vehicle = screen.getByRole('button', { name: /车端软件/ });
    expect(vehicle.getAttribute('aria-pressed')).toBe('false');
    // 勾选为空时不再重算文档（已有内容保持原样，不额外触发 onChange）
    expect(onChange).not.toHaveBeenCalled();
  });

  it('记忆按项目隔离：localStorage 有记录时恢复上次的勾选', async () => {
    localStorage.setItem('share-doc:selected-tags:P1', JSON.stringify(['r1']));
    renderSetting();
    const vehicle = await screen.findByRole('button', { name: /车端软件/ });
    const schedule = screen.getByRole('button', { name: /调度软件/ });

    // 恢复依赖「拿到信息树后」的第二次渲染（与默认勾选同一时序，CI 慢机器上要等），
    // 这里必须 waitFor 等 effect 真正生效，否则 aria-pressed 可能还是初始的 false
    await waitFor(() => expect(vehicle.getAttribute('aria-pressed')).toBe('true'));
    // 上次只选了「车端软件」→ 恢复这一个；「调度软件」仍未选
    expect(schedule.getAttribute('aria-pressed')).toBe('false');
    // 车端软件不过半 → 不出缺信息提示条
    expect(screen.queryByText(/当前问题缺少有效信息/)).toBeNull();
  });

  it('取消勾选后该标签从文档里消失', async () => {
    const { onChange } = renderSetting();
    const vehicle = await screen.findByRole('button', { name: /车端软件/ });
    fireEvent.click(screen.getByRole('button', { name: '全选' }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    onChange.mockClear();

    fireEvent.click(vehicle);

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const draft = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(draft.content).not.toContain('### 车端软件');
    expect(draft.content).toContain('# 问题共享文档');
  });

  it('「暂时跳过」后本页不再提示缺失信息，另两个按钮仍在', async () => {
    renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });
    fireEvent.click(screen.getByRole('button', { name: '全选' }));

    // 提示条依赖「全选后」的第二次渲染（标签先出现、提示条后出现），
    // 这里必须用 findBy 等按钮真正挂上，否则 CI 慢机器上会偶发找不到
    fireEvent.click(await screen.findByRole('button', { name: '暂时跳过' }));
    await waitFor(() => expect(screen.queryByText(/当前问题缺少有效信息/)).toBeNull());
    // 暂时跳过本次不再显示；补充信息 / 提单给他人补充 常驻不受影响
    expect(screen.queryByRole('button', { name: '暂时跳过' })).toBeNull();
    expect(screen.getByRole('button', { name: '补充信息' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '提单给他人补充' })).toBeTruthy();
  });

  it('「提单给他人补充」选人后建一张 support 工单，附待补充节点清单', async () => {
    renderSetting();
    await screen.findByRole('button', { name: /车端软件/ });
    fireEvent.click(screen.getByRole('button', { name: '全选' }));

    fireEvent.click(await screen.findByRole('button', { name: '提单给他人补充' }));
    fireEvent.click(await screen.findByRole('button', { name: '选张三' }));
    fireEvent.click(screen.getByRole('button', { name: '发送补充工单' }));

    await waitFor(() => expect(vi.mocked(createTicket)).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(createTicket).mock.calls[0][0];
    expect(payload.ticket_type).toBe('support');
    expect(payload.assigned_to).toBe('u1');
    expect(payload.project_id).toBe('P1');
    expect(payload.description).toContain('车端软件 / 控制器品牌');
    expect(payload.description).toContain('调度软件 / 数据同步方式');
    const meta = payload.metadata_info as { info_supplement: { node_count: number; nodes: unknown[] } };
    expect(meta.info_supplement.node_count).toBe(3);
    expect(meta.info_supplement.nodes).toHaveLength(3);

    // 已经交给别人了，本次提单不再拦着提醒
    await waitFor(() => expect(screen.queryByText(/当前问题缺少有效信息/)).toBeNull());
  });
});
