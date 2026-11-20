import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Toast } from 'tdesign-mobile-react';
import ProjectInfoLedgerSync from '../admin/ProjectInfoLedgerSync';
import {
  createInfoNodeApi,
  fetchLedgerSyncPreviewApi,
  setInfoNodeValueApi,
  type ApiInfoNode,
  type ApiLedgerSyncResult,
} from '@/api/infoNodes';
import type { ProjectInfoNode } from '@/shared/utils/projectInfoTree';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  createCustomInfoNodeApi: vi.fn(),
  setInfoNodeValueApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  parseImportFileApi: vi.fn(),
  fetchLedgerSyncPreviewApi: vi.fn(),
}));

vi.mock('tdesign-mobile-react', () => {
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  return { Popup, Toast: vi.fn() };
});

const TS = '2026-09-19 10:00:00';

const apiNode = (partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode => ({
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

const node = (partial: Partial<ProjectInfoNode> & { id: string }): ProjectInfoNode => ({
  project_id: 'P1',
  parent_id: null,
  title: '节点',
  content_type: 'text',
  value: '',
  sort_order: 0,
  created_at: TS,
  ...partial,
});

const NODES: ProjectInfoNode[] = [
  node({ id: 'r1', title: '基础信息' }),
  node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力' }),
  node({ id: 'p1', parent_id: 'r1', title: '订单信息' }),
  node({ id: 'c2', parent_id: 'p1', title: 'ERP', value: '' }),
];

const RESULT: ApiLedgerSyncResult = {
  project_id: 'P1',
  project_name: '中力越南项目',
  project_code: 'P1',
  ledger_updated_at: '2026-09-19 11:20',
  field_count: 3,
  mirror_field_total: 5,
  fill: [{
    node_id: 'c2', path: '基础信息 / 订单信息 / ERP', title: 'ERP',
    content_type: 'text', current: '', value: 'SAP ECC',
  }],
  overwrite: [{
    node_id: 'c1', path: '基础信息 / 客户信息', title: '客户信息',
    content_type: 'text', current: '中力', value: '浙江中力',
  }],
  unmatched: [
    // 有建议归属：勾选后建在它下面（这两条不受「不新建一级标签」影响）
    {
      title: '售后服务', value: '有质保',
      suggested_parent_id: 'p1', suggested_parent_path: '基础信息 / 订单信息',
    },
    // 没归属：同步不新建一级标签 → 置灰不勾，只作提醒
    {
      title: '项目类型', value: '普通项目',
      suggested_parent_id: null, suggested_parent_path: null,
      note: '树里已有同名节点「项目类型」，但它是下拉、可选项里没有这个值——先到编辑页给它补上选项，比新建一个同名节点合适',
    },
  ],
};

const renderDialog = (onApplied = vi.fn(), canEditTree = true) => {
  render(
    <ProjectInfoLedgerSync
      visible
      onClose={vi.fn()}
      projectId="P1"
      nodes={NODES}
      canEditTree={canEditTree}
      onApplied={onApplied}
    />,
  );
  return onApplied;
};

describe('ProjectInfoLedgerSync（企业微信台账同步）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('打开即拉预览：展示台账来源与三组结果，三组默认全勾', async () => {
    vi.mocked(fetchLedgerSyncPreviewApi).mockResolvedValue(RESULT);
    renderDialog();

    expect(fetchLedgerSyncPreviewApi).toHaveBeenCalledWith('P1');
    expect(screen.getByText('同步信息')).toBeTruthy();          // 弹层标题
    expect(await screen.findByText('将填写')).toBeTruthy();
    expect(screen.getByText('将覆盖')).toBeTruthy();
    expect(screen.getByText('未匹配到节点')).toBeTruthy();

    // 台账来源：本地库 + 更新时间和「几个字段参与比对」
    expect(screen.getByText(/台账数据来自本地库/)).toBeTruthy();
    expect(screen.getByText(/台账更新于 2026-09-19 11:20/)).toBeTruthy();
    expect(screen.getByText(/参与比对的字段 3 个（镜像共 5 列）/)).toBeTruthy();

    // 矛盾行显示「原内容 → 新内容」；缺少的节点显示后端给的说明
    expect(screen.getByText(/原内容：中力 →/)).toBeTruthy();
    expect(screen.getByText(/先到编辑页给它补上选项/)).toBeTruthy();

    // 用户口径「节点默认全选」：填写 1 + 覆盖 1 + 有归属的新建 1；没归属的那条置灰不勾
    expect(screen.getByText('确认同步（3）')).toBeTruthy();
    expect(screen.getByText(/建议归属：基础信息 \/ 订单信息/)).toBeTruthy();
    expect(screen.getByText(/同步不新建一级标签/)).toBeTruthy();
    expect((screen.getByLabelText('选择 项目类型') as HTMLInputElement).disabled).toBe(true);
  });

  it('预览取不到时弹层里说明原因并可重试，失败期间不显示任何预览', async () => {
    vi.mocked(fetchLedgerSyncPreviewApi).mockRejectedValueOnce(
      new Error('该项目还没有信息节点，请先在编辑页新建节点后再同步'),
    );
    renderDialog();

    const warn = await screen.findByRole('alert');
    expect(warn.textContent).toContain('还没有信息节点');
    expect(screen.queryByText('将填写')).toBeNull();

    // 重试：服务恢复后正常出预览
    vi.mocked(fetchLedgerSyncPreviewApi).mockResolvedValue(RESULT);
    fireEvent.click(screen.getByText('重试'));
    expect(await screen.findByText('将填写')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('确认同步：勾选项逐节点落库，取消勾选的矛盾节点不动', async () => {
    vi.mocked(fetchLedgerSyncPreviewApi).mockResolvedValue(RESULT);
    vi.mocked(setInfoNodeValueApi).mockImplementation(async (id, _projectId, value) => apiNode({ id, value }));
    vi.mocked(createInfoNodeApi).mockImplementation(async (_projectId, payload) =>
      apiNode({ id: 'server-1', title: payload.title ?? '', parent_id: payload.parent_id ?? null }));
    const onApplied = renderDialog();
    await screen.findByText('将填写');

    // 默认全勾（3 项），先把「将覆盖」那条取消掉——矛盾要用户点头才覆盖，取消即不写
    fireEvent.click(screen.getByLabelText('选择 基础信息 / 客户信息'));
    expect(screen.getByText('确认同步（2）')).toBeTruthy();
    fireEvent.click(screen.getByText('确认同步（2）'));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(setInfoNodeValueApi).toHaveBeenCalledWith('c2', 'P1', 'SAP ECC');
    // 有建议归属的未匹配条目：建在建议的父节点下（不再挂到兜底根）
    expect(createInfoNodeApi).toHaveBeenCalledWith('P1', expect.objectContaining({
      parent_id: 'p1', title: '售后服务',
    }));
    expect(setInfoNodeValueApi).toHaveBeenCalledWith('server-1', 'P1', '有质保');
    // 用户口径「不要新增根节点」：没有归属的条目一条也不建，连「导入信息」根节点都不该出现
    expect(createInfoNodeApi).not.toHaveBeenCalledWith('P1', expect.objectContaining({
      title: '导入信息',
    }));
    expect(createInfoNodeApi).toHaveBeenCalledTimes(1);
    expect(setInfoNodeValueApi).not.toHaveBeenCalledWith('server-1', 'P1', '普通项目');
    // 没勾的「将覆盖」不写库——矛盾要用户点头才覆盖
    expect(setInfoNodeValueApi).not.toHaveBeenCalledWith('c1', expect.anything(), expect.anything());
    expect(vi.mocked(Toast)).toHaveBeenCalledWith(expect.objectContaining({
      message: '已填写 1 项，覆盖 0 项，新增 1 项',
    }));
  });

  it('没有归属的条目：同步不新建一级标签，只作提醒（置灰、不计入按钮数字）', async () => {
    vi.mocked(fetchLedgerSyncPreviewApi).mockResolvedValue({
      ...RESULT,
      unmatched: [RESULT.unmatched[1]],
    });
    const onApplied = renderDialog();
    await screen.findByText('将填写');

    const box = screen.getByLabelText('选择 项目类型') as HTMLInputElement;
    expect(box.disabled).toBe(true);
    expect(box.checked).toBe(false);
    expect(screen.queryByText(/导入信息/)).toBeNull();          // 不再承诺「将自动创建」
    expect(screen.getByText(/没有归属的只作提醒/)).toBeTruthy();

    fireEvent.click(screen.getByText('确认同步（2）'));            // 填写 1 + 覆盖 1
    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(createInfoNodeApi).not.toHaveBeenCalled();
  });

  it('不是本项目的人：未匹配组置灰不可勾（新建节点要改树结构）', async () => {
    vi.mocked(fetchLedgerSyncPreviewApi).mockResolvedValue(RESULT);
    vi.mocked(setInfoNodeValueApi).mockImplementation(async (id, _projectId, value) => apiNode({ id, value }));
    renderDialog(vi.fn(), false);
    await screen.findByText('将填写');

    expect((screen.getByLabelText('选择 项目类型') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('选择 售后服务') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/只有该项目的人员/)).toBeTruthy();
    expect((screen.getByLabelText('选择 基础信息 / 订单信息 / ERP') as HTMLInputElement).disabled).toBe(false);
  });
});
