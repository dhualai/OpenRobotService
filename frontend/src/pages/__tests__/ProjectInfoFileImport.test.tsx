import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { Toast } from 'tdesign-mobile-react';
import ProjectInfoFileImport from '../admin/ProjectInfoFileImport';
import {
  createInfoNodeApi,
  parseImportFileApi,
  setInfoNodeValueApi,
  type ApiImportParseResult,
  type ApiInfoNode,
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
}));

vi.mock('tdesign-mobile-react', () => {
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  return { Popup, Toast: vi.fn() };
});

const TS = '2026-09-15 10:00:00';

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

const RESULT: ApiImportParseResult = {
  file_name: '需求.docx',
  model: 'deepseek-v4-flash',
  text_length: 120,
  truncated: false,
  extracted: 3,
  project_name: '中力越南项目',
  file_project_name: '中力越南项目',
  name_mismatch: false,
  fill: [{
    node_id: 'c2', path: '基础信息 / 订单信息 / ERP', title: 'ERP',
    content_type: 'text', current: '', value: 'SAP ECC',
  }],
  overwrite: [{
    node_id: 'c1', path: '基础信息 / 客户信息', title: '客户信息',
    content_type: 'text', current: '中力', value: '浙江中力',
  }],
  unmatched: [{
    title: '设备数量', value: '3 台',
    suggested_parent_id: 'p1', suggested_parent_path: '基础信息 / 订单信息',
  }],
};

const pickFile = () => {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(['x'], '需求.docx')] } });
};

const renderDialog = (onApplied = vi.fn()) => {
  render(
    <ProjectInfoFileImport
      visible
      onClose={vi.fn()}
      projectId="P1"
      nodes={NODES}
      onApplied={onApplied}
    />,
  );
  return onApplied;
};

describe('ProjectInfoFileImport（文件导入 AI 识别）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('上传后显示正在识别，识别完成展示三组结果与前后变化/建议归属', async () => {
    let resolve!: (value: ApiImportParseResult) => void;
    vi.mocked(parseImportFileApi).mockImplementation(() => new Promise((r) => { resolve = r; }));
    renderDialog();
    pickFile();

    expect(parseImportFileApi).toHaveBeenCalledWith('P1', expect.any(File));
    expect(await screen.findByText('正在识别…')).toBeTruthy();

    resolve(RESULT);
    expect(await screen.findByText('将填写')).toBeTruthy();
    expect(screen.getByText('将覆盖')).toBeTruthy();
    expect(screen.getByText('未匹配到节点')).toBeTruthy();
    expect(screen.getAllByText('（1）')).toHaveLength(3);

    // 三组提示语
    expect(screen.getByText('节点当前为空，勾选后直接填入')).toBeTruthy();
    expect(screen.getByText('节点已有内容，勾选后才会覆盖')).toBeTruthy();
    expect(screen.getByText('勾选后作为新节点创建')).toBeTruthy();

    // 覆盖行显示「原内容 → 新内容」；未匹配行显示建议归属
    expect(screen.getByText(/原内容：中力 →/)).toBeTruthy();
    expect(screen.getByText('浙江中力')).toBeTruthy();
    expect(screen.getByText('建议归属：基础信息 / 订单信息')).toBeTruthy();

    // 默认只勾选「将填写」（1 项），覆盖/新建需用户主动勾选
    expect(screen.getByText('确认导入（1）')).toBeTruthy();
  });

  it('确认导入：勾选项逐节点落库（填写/覆盖走值写入，未匹配走新建+写值）', async () => {
    vi.mocked(parseImportFileApi).mockResolvedValue(RESULT);
    // 值写入接口按「节点 id + 归属项目 + 值」调用，返回写入后的节点
    vi.mocked(setInfoNodeValueApi).mockImplementation(async (id, _projectId, value) =>
      apiNode({ id, value }));
    vi.mocked(createInfoNodeApi).mockImplementation(async (_projectId, payload) =>
      apiNode({ id: 'server-1', title: payload.title ?? '', parent_id: payload.parent_id ?? null }));
    const onApplied = renderDialog();
    pickFile();
    await screen.findByText('将填写');

    fireEvent.click(screen.getByLabelText('选择 设备数量'));
    expect(screen.getByText('确认导入（2）')).toBeTruthy();
    fireEvent.click(screen.getByText('确认导入（2）'));

    await waitFor(() => expect(onApplied).toHaveBeenCalled());
    expect(setInfoNodeValueApi).toHaveBeenCalledWith('c2', 'P1', 'SAP ECC');
    expect(createInfoNodeApi).toHaveBeenCalledWith('P1', expect.objectContaining({
      parent_id: 'p1', title: '设备数量',
    }));
    // 未匹配的新节点：先建节点，再把值写到它自己身上
    expect(setInfoNodeValueApi).toHaveBeenCalledWith('server-1', 'P1', '3 台');
    // 未勾选的「将覆盖」不落库
    expect(setInfoNodeValueApi).not.toHaveBeenCalledWith('c1', expect.anything(), expect.anything());
    expect(vi.mocked(Toast)).toHaveBeenCalledWith(expect.objectContaining({
      message: '已填写 1 项，覆盖 0 项，新增 1 项',
    }));
  });

  it('没有建议归属的未匹配条目挂在「导入信息」兜底根下新建', async () => {
    vi.mocked(parseImportFileApi).mockResolvedValue({
      ...RESULT,
      fill: [],
      overwrite: [],
      unmatched: [{ title: '临时信息', value: 'x', suggested_parent_id: null, suggested_parent_path: null }],
    });
    let created = 0;
    vi.mocked(createInfoNodeApi).mockImplementation(async (_projectId, payload) => {
      created += 1;
      return apiNode({ id: `server-${created}`, title: payload.title ?? '', parent_id: payload.parent_id ?? null });
    });
    vi.mocked(setInfoNodeValueApi).mockImplementation(async (id, _projectId, value) => apiNode({ id, value }));
    renderDialog();
    pickFile();
    await screen.findByText('未匹配到节点');

    expect(screen.getByText('建议归属：导入信息（将自动创建）')).toBeTruthy();
    fireEvent.click(screen.getByLabelText('选择 临时信息'));
    fireEvent.click(screen.getByText('确认导入（1）'));

    // 先建兜底根「导入信息」，再在它下面建「临时信息」
    await waitFor(() => expect(createInfoNodeApi).toHaveBeenCalledTimes(2));
    expect(createInfoNodeApi).toHaveBeenNthCalledWith(1, 'P1', expect.objectContaining({
      parent_id: null, title: '导入信息',
    }));
    expect(createInfoNodeApi).toHaveBeenNthCalledWith(2, 'P1', expect.objectContaining({
      title: '临时信息',
    }));
    expect(setInfoNodeValueApi).toHaveBeenCalledWith('server-2', 'P1', 'x');
  });

  it('文件项目名与当前项目不一致时，显著提醒可能导错文件（可继续或取消）', async () => {
    vi.mocked(parseImportFileApi).mockResolvedValue({
      ...RESULT,
      file_project_name: '杭叉智能仓储项目',
      name_mismatch: true,
    });
    renderDialog();
    pickFile();

    const warn = await screen.findByRole('alert');
    expect(warn.textContent).toContain('杭叉智能仓储项目');
    expect(warn.textContent).toContain('中力越南项目');
    expect(warn.textContent).toContain('可能导错了文件');
    // 提醒不妨碍继续导入：确认按钮与取消按钮都在
    expect(screen.getByText('确认导入（1）')).toBeTruthy();
    expect(screen.getByText('取消')).toBeTruthy();
  });

  it('识别失败时提示错误且不出现预览', async () => {
    vi.mocked(parseImportFileApi).mockRejectedValue(new Error('仅支持 Word（.docx）文件'));
    renderDialog();
    pickFile();

    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalledWith(
      expect.objectContaining({ message: '仅支持 Word（.docx）文件' }),
    ));
    expect(screen.queryByText('将填写')).toBeNull();
    expect(screen.queryByText('确认导入（0）')).toBeNull();
  });
});
