import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import ProjectInfoEdit from '../admin/ProjectInfoEdit';
import {
  resetProjectInfoTreeApi,
  createInfoNodeApi,
  createCustomInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoNodeChangeSummaryApi,
  fetchInfoNodeChangesApi,
  fetchInfoTree,
  setInfoNodeValueApi,
  updateInfoNodeApi,
  type ApiInfoNode,
  type ApiInfoNodeChange,
} from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  createCustomInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  setInfoNodeValueApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  resetProjectInfoTreeApi: vi.fn(),
  // 编辑历史：进页面会拉一次「各节点最新记录时间」算小红点，缺了页面会直接崩
  fetchInfoNodeChangesApi: vi.fn(),
  fetchInfoNodeChangeSummaryApi: vi.fn(),
}));

// 页头副标题会拉一次项目详情、上传走资源管理服务：统一给个空实现
vi.mock('@/api/client', () => ({
  createRequest: () => vi.fn(async () => ({ name: '演示项目' })),
  ApiError: class MockApiError extends Error { statusCode = 0; },
  clearCache: vi.fn(),
}));

// 登录态可切换。默认管理员（本项目成员的判据无关紧要——admin 直通）。
//   permissions  —— 含 'admin' 即管理员（与后端一致）
//   projectIds   —— 当前用户在哪些项目下有角色（user_project_roles 的键）=「这个项目下的人」
//   canEditTemplate —— 详情模板权限码（后端按全局角色 开发者/超级管理员 派生，非 admin 也 != false）
const authState = vi.hoisted(() => ({
  permissions: ['admin'] as string[],
  projectIds: ['P1'] as string[],
  canEditTemplate: false,
}));
vi.mock('@/stores/auth', () => ({
  PERM_PROJECT_INFO_TEMPLATE: 'frontend:admin:project-info-template:show',
  useAuthStore: (selector: (s: {
    username: string;
    permissions: string[];
    roles: Record<string, string[]>;
    hasPermission: (code: string) => boolean;
  }) => unknown) =>
    selector({
      username: 'admin',
      permissions: authState.permissions,
      roles: Object.fromEntries(authState.projectIds.map((pid) => [pid, ['role-x']])),
      hasPermission: (code: string) =>
        authState.permissions.includes('admin')
        || (code === 'frontend:admin:project-info-template:show' && authState.canEditTemplate),
    }),
}));

vi.mock('tdesign-mobile-react', () => {
  const Navbar = ({ title }: { title?: ReactNode }) => <div>{title}</div>;
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  const Input = ({ value, onChange, placeholder }: { value?: string; onChange?: (v: string) => void; placeholder?: string }) => (
    <input value={value ?? ''} onChange={(e) => onChange?.(e.target.value)} placeholder={placeholder} />
  );
  // 一键回到顶部按钮：无交互逻辑可测，渲染占位即可
  const BackTop = () => <div data-testid="backtop" />;
  // Toast 是 spy：提示文案（成功/失败）也要能断言
  return { Navbar, Popup, Input, Toast: vi.fn(), BackTop };
});

const TS = '2026-09-14 10:00:00';
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

const TREE: ApiInfoNode[] = [
  node({
    id: 'r1',
    title: '基础信息',
    sort_order: 0,
    children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 })],
  }),
];

/** 构造一条后端操作记录（默认值可按需覆盖） */
const change = (partial: Partial<ApiInfoNodeChange> & { id: string }): ApiInfoNodeChange => ({
  node_id: 'r1',
  parent_id: null,
  node_title: '基础信息',
  action: 'update',
  operator: null,
  operator_name: null,
  detail: null,
  created_at: '2026-09-14 11:00:00',
  ...partial,
});

const renderEdit = () =>
  render(
    <MemoryRouter initialEntries={['/admin/project-detail/P1/edit']}>
      <Routes>
        <Route path="/admin/project-detail/:id/edit" element={<ProjectInfoEdit />} />
        <Route path="/admin/project-info-template" element={<div>模板页占位</div>} />
      </Routes>
    </MemoryRouter>
  );

describe('ProjectInfoEdit（信息树编辑页）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    authState.permissions = ['admin'];
    authState.projectIds = ['P1'];
    authState.canEditTemplate = false;
    vi.mocked(fetchInfoTree).mockResolvedValue(TREE);
    // 默认没有操作记录：不出小红点，历史弹层显示空态
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({});
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([]);
  });

  it('打开页面读取后端信息树并渲染节点', async () => {
    renderEdit();
    expect(fetchInfoTree).toHaveBeenCalledWith('P1');
    expect(await screen.findByText('基础信息')).toBeTruthy();
    expect(screen.getByText('客户信息')).toBeTruthy();
    expect((screen.getByLabelText('客户信息内容') as HTMLTextAreaElement).value).toBe('中力');
  });

  it('管理员可见「详情模板」入口，点击进入模板编辑页', async () => {
    renderEdit();
    await screen.findByText('基础信息');
    fireEvent.click(screen.getByRole('button', { name: '详情模板' }));
    expect(await screen.findByText('模板页占位')).toBeTruthy();
  });

  it('开发者/超级管理员（有模板权限码、不是 admin）也能进「详情模板」', async () => {
    authState.permissions = [];
    authState.projectIds = [];      // 与项目无关：模板是全局的
    authState.canEditTemplate = true;
    renderEdit();
    await screen.findByText('基础信息');
    fireEvent.click(screen.getByRole('button', { name: '详情模板' }));
    expect(await screen.findByText('模板页占位')).toBeTruthy();
    // 但不在这个项目下，仍然改不了这棵树
    expect(screen.queryByRole('button', { name: '新标签' })).toBeNull();
    expect(screen.queryByLabelText('编辑基础信息')).toBeNull();
  });

  it('没有模板权限码就不显示「详情模板」入口（哪怕是本项目成员）', async () => {
    authState.permissions = [];
    authState.canEditTemplate = false;
    authState.projectIds = ['P1'];   // 本项目成员：能改树，但看不到全局模板
    renderEdit();
    await screen.findByText('基础信息');
    expect(screen.queryByRole('button', { name: '详情模板' })).toBeNull();
    expect(screen.getByRole('button', { name: '新标签' })).toBeTruthy();
  });

  it('行内改名写回后端（PUT 节点）', async () => {
    vi.mocked(updateInfoNodeApi).mockResolvedValue(node({ id: 'r1', title: '基础信息2' }));
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({ id: 'r1', title: '基础信息', sort_order: 0, is_custom: true }),
    ]);
    renderEdit();
    fireEvent.click(await screen.findByLabelText('编辑基础信息'));

    const input = document.querySelector('.mac-info-row__input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '基础信息2' } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(updateInfoNodeApi).toHaveBeenCalledWith('r1', { title: '基础信息2' });
    });
    expect(await screen.findByText('基础信息2')).toBeTruthy();
  });

  it('本项目成员（不是管理员）：同样能「新标签」并改名本项目的增补节点', async () => {
    authState.permissions = [];
    authState.projectIds = ['P1'];      // user_project_roles 里 P1 下有角色
    vi.mocked(updateInfoNodeApi).mockResolvedValue(node({ id: 'r1', title: '基础信息2', is_custom: true }));
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({ id: 'r1', title: '基础信息', sort_order: 0, is_custom: true }),
    ]);
    renderEdit();

    // 结构类入口都在（不再只有管理员看得到）
    expect(await screen.findByRole('button', { name: '新标签' })).toBeTruthy();
    expect(screen.getByLabelText('长按拖动调整从属')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('编辑基础信息'));
    const input = document.querySelector('.mac-info-row__input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '基础信息2' } });
    fireEvent.blur(input);

    await waitFor(() => expect(updateInfoNodeApi).toHaveBeenCalledWith('r1', { title: '基础信息2' }));
  });

  it('全局字段（非本项目增补）：成员也没有改名/删除入口，只能填值、看历史、往下增补', async () => {
    authState.permissions = [];
    authState.projectIds = ['P1'];
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({ id: 'r1', title: '基础信息', sort_order: 0 }),   // is_custom 缺省 = 全局字段
    ]);
    renderEdit();
    await screen.findByText('基础信息');

    // 结构按钮只随 is_custom 出：全局字段定义改一次全体项目生效，成员也改不动（后端 403，
    // 只能在「详情模板」里改）
    expect(screen.queryByLabelText('编辑基础信息')).toBeNull();
    expect(screen.queryByLabelText('更多操作')).toBeNull();
    expect(screen.queryByLabelText('长按拖动调整从属')).toBeNull();
    // 值、历史、往下增补照旧
    expect(screen.getByLabelText('基础信息内容')).toBeTruthy();
    expect(screen.getByLabelText('在基础信息下新增')).toBeTruthy();
    expect(screen.getByLabelText('查看基础信息的编辑历史')).toBeTruthy();
  });

  it('车型1（下拉 + 数量子节点）：选中型号写进值，数量子节点照常可填', async () => {
    // 车型改下拉后，型号是**值**不是节点名：全局节点各项目共用同一行，
    // 把型号写进标题会被后端 403（全局字段定义只能在「详情模板」改），
    // 而且会串改所有项目的车型。
    const vehicleTree: ApiInfoNode[] = [
      node({
        id: 'h1', title: '硬件', sort_order: 0,
        children: [
          node({
            id: 'v1', parent_id: 'h1', title: '车辆', sort_order: 0,
            children: [
              node({
                id: 'm1', parent_id: 'v1', title: '车型1', content_type: 'select', sort_order: 0,
                options: ['XC1051', 'XCD061'],
                value: { selected: '', options: ['XC1051', 'XCD061'] },
                children: [node({ id: 'q1', parent_id: 'm1', title: '数量', value: '2', sort_order: 0 })],
              }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(vehicleTree);
    vi.mocked(setInfoNodeValueApi).mockResolvedValue(
      node({ id: 'm1', title: '车型1', content_type: 'select' }),
    );
    renderEdit();

    // 非末级节点照样渲染自己的值编辑器：下拉与「数量」子行同时在
    const select = (await screen.findByLabelText('车型1内容')) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(['', 'XC1051', 'XCD061']);
    expect(screen.getByText('数量')).toBeTruthy();

    fireEvent.change(select, { target: { value: 'XC1051' } });
    await waitFor(() => expect(setInfoNodeValueApi).toHaveBeenCalledWith(
      'm1', 'P1', { selected: 'XC1051', options: ['XC1051', 'XCD061'] },
    ));
    // 改的是值，节点定义一个字都不动
    expect(updateInfoNodeApi).not.toHaveBeenCalled();
  });

  it('「新标签」先建后端节点再进入改名态', async () => {
    // 节点 id 由后端生成（增补节点要按 node_key 保证同项目内唯一），页面拿返回值入列
    vi.mocked(createInfoNodeApi).mockResolvedValue(
      node({ id: 'server-1', title: '未命名节点', parent_id: null, sort_order: 10 }),
    );
    renderEdit();
    fireEvent.click(await screen.findByText('新标签'));

    await waitFor(() => {
      expect(createInfoNodeApi).toHaveBeenCalledWith('P1', {
        parent_id: null, title: '未命名节点', content_type: 'text', sort_order: 10,
      });
    });
    // 改名态要等建节点返回并入列后才渲染：单独 waitFor 拿 DOM，别跟上面的接口断言挤一个
    await waitFor(() => expect(document.querySelector('.mac-info-row__input')).toBeTruthy());
  });

  it('删除节点走 DELETE 接口（含子树提示）', async () => {
    // 结构操作只对本项目增补的节点开放：全局字段定义只能回「详情模板」改
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r1', title: '基础信息', sort_order: 0, is_custom: true,
        children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 })],
      }),
    ]);
    vi.mocked(deleteInfoNodeApi).mockResolvedValue(undefined);
    renderEdit();
    // 树里每行都有「更多操作」按钮，这里取第一个（根节点）
    fireEvent.click((await screen.findAllByLabelText('更多操作'))[0]);
    fireEvent.click(screen.getByText('删除节点'));
    expect(screen.getByText(/及其所有子节点/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => {
      expect(deleteInfoNodeApi).toHaveBeenCalledWith('r1');
    });
    expect(screen.queryByText('基础信息')).toBeNull();
  });

  it('一键清空：先确认再清（值清掉、增补节点删掉、模板字段还在），并报出两个计数', async () => {
    vi.mocked(resetProjectInfoTreeApi).mockResolvedValue({ cleared: 1, nodesRemoved: 1 });
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [
          node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 }),
          // 导入/同步加进来的增补节点：一键清空后连节点一起没（恢复成模板结构）
          node({ id: 'x1', parent_id: 'r1', title: '导入信息', is_custom: true, value: '台账同步进来的', sort_order: 1 }),
        ],
      }),
    ]);
    renderEdit();
    await screen.findByText('基础信息');

    fireEvent.click(screen.getByRole('button', { name: '一键清空' }));
    // 确认弹层：没点「清空」之前不发请求
    expect(screen.getByText(/恢复成模板的样子/)).toBeTruthy();
    expect(resetProjectInfoTreeApi).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '清空' }));
    await waitFor(() => expect(resetProjectInfoTreeApi).toHaveBeenCalledWith('P1'));
    await waitFor(() => {
      expect((screen.getByLabelText('客户信息内容') as HTMLTextAreaElement).value).toBe('');
    });
    // 全局（模板）字段留着，增补节点没了
    expect(screen.getByText('基础信息')).toBeTruthy();
    expect(screen.getByText('客户信息')).toBeTruthy();
    expect(screen.queryByText('导入信息')).toBeNull();
    // 提示把两个数都报出来（清掉的内容 + 删掉的增补节点）
    await waitFor(() => {
      expect(vi.mocked(Toast)).toHaveBeenCalledWith({
        message: '已恢复为模板结构：删除 1 个增补节点、清空 1 项已填内容',
        theme: 'success',
      });
    });
  });

  it('一键清空：确认前点「取消」不发请求', async () => {
    renderEdit();
    await screen.findByText('基础信息');
    fireEvent.click(screen.getByRole('button', { name: '一键清空' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(resetProjectInfoTreeApi).not.toHaveBeenCalled();
  });

  it('不是这个项目的人看不到「一键清空」（值写入门槛之外的重操作）', async () => {
    authState.permissions = [];
    authState.projectIds = [];       // 只在别的项目下有角色
    renderEdit();
    await screen.findByText('基础信息');
    expect(screen.queryByRole('button', { name: '一键清空' })).toBeNull();
  });

  it('一键清空：什么都没有可清时提示「本来就与模板一致」而不是「清空 0 项」', async () => {
    vi.mocked(resetProjectInfoTreeApi).mockResolvedValue({ cleared: 0, nodesRemoved: 0 });
    renderEdit();
    await screen.findByText('基础信息');
    fireEvent.click(screen.getByRole('button', { name: '一键清空' }));
    fireEvent.click(screen.getByRole('button', { name: '清空' }));
    await waitFor(() => {
      expect(vi.mocked(Toast)).toHaveBeenCalledWith({
        message: '本来就与模板一致，没有可清的内容',
        theme: 'success',
      });
    });
  });

  it('全局字段定义不给结构操作，只留历史（改定义要走「详情模板」）', async () => {
    renderEdit();
    await screen.findByText('基础信息');
    expect(screen.queryByLabelText('编辑基础信息')).toBeNull();
    expect(screen.queryByLabelText('更多操作')).toBeNull();
    // 新增子节点是结构操作，但管理员可以在这里加（后端落成全局定义）
    expect(screen.getByLabelText('在基础信息下新增')).toBeTruthy();
    expect(screen.getByLabelText('查看基础信息的编辑历史')).toBeTruthy();
  });

  it('区域选项选大陆 → 出现省份/地区；改选其它区域 → 换成具体国家', async () => {
    const regionTree: ApiInfoNode[] = [
      node({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [
          node({
            id: 'p1', parent_id: 'r1', title: '项目区域/地点', sort_order: 0,
            children: [
              node({
                id: 'd1', parent_id: 'p1', title: '区域选项', content_type: 'select', sort_order: 0,
                options: ['大陆(China Mainland)', '亚洲Asia'],
                value: { selected: '', options: ['大陆(China Mainland)', '亚洲Asia'] },
              }),
              node({ id: 's1', parent_id: 'p1', title: '省份', value: '浙江省', sort_order: 1 }),
              node({ id: 'a1', parent_id: 'p1', title: '地区', value: '安吉县', sort_order: 2 }),
              node({ id: 'c1', parent_id: 'p1', title: '具体国家', value: '', sort_order: 3 }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(regionTree);
    // 选值走值写入接口：选项落在节点定义上，页面拿到返回值后重新回填
    vi.mocked(updateInfoNodeApi).mockImplementation(async (nodeId, updates) =>
      node({ id: nodeId, content_type: 'select', options: updates.options }),
    );
    renderEdit();

    // 未选择区域：三个细分字段都不显示
    await screen.findByText('区域选项');
    expect(screen.queryByText('省份')).toBeNull();
    expect(screen.queryByText('具体国家')).toBeNull();

    const select = screen.getByLabelText('区域选项内容') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '大陆(China Mainland)' } });
    // 选值后先乐观更新、再等接口返回回填，两次渲染之间查询会扑空，统一用 waitFor 等稳
    await waitFor(() => {
      expect(screen.getByText('省份')).toBeTruthy();
      expect(screen.getByText('地区')).toBeTruthy();
    });
    expect(screen.queryByText('具体国家')).toBeNull();

    fireEvent.change(screen.getByLabelText('区域选项内容'), { target: { value: '亚洲Asia' } });
    await waitFor(() => expect(screen.getByText('具体国家')).toBeTruthy());
    expect(screen.queryByText('省份')).toBeNull();
    expect(screen.queryByText('地区')).toBeNull();
  });

  it('加载失败时给出重试入口', async () => {
    vi.mocked(fetchInfoTree).mockRejectedValueOnce(new Error('network'));
    renderEdit();
    expect(await screen.findByText('信息节点加载失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('基础信息')).toBeTruthy();
  });

  it('空树时给出角色对应的空态（管理员引导建节点，别人引导找管理员）', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValue([]);
    renderEdit();

    expect(await screen.findByText('还没有信息节点，点击右上角「新标签」创建')).toBeTruthy();
    expect(screen.queryByText('按预设模板初始化')).toBeNull();
  });

  it('不在这个项目下的人看不到「新标签」，空态改提示联系管理员', async () => {
    authState.permissions = [];
    authState.projectIds = [];
    vi.mocked(fetchInfoTree).mockResolvedValue([]);
    renderEdit();

    expect(await screen.findByText('信息模板还没有配置节点，请联系管理员')).toBeTruthy();
    expect(screen.queryByText('新标签')).toBeNull();
  });

  it('「增补信息」：不在项目下的人也能在任意节点下加本项目字段，但不给结构操作入口', async () => {
    authState.permissions = [];
    authState.projectIds = [];
    // 夹具里没有任何 allow_custom：所有节点都能增补（2026-09-18 取消了逐节点开关）
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 })],
      }),
    ]);
    vi.mocked(createCustomInfoNodeApi).mockResolvedValue(
      node({ id: 'server-9', parent_id: 'r1', title: '合同编号', sort_order: 10, is_custom: true }),
    );
    vi.mocked(updateInfoNodeApi).mockResolvedValue(
      node({ id: 'server-9', parent_id: 'r1', title: '合同编号', content_type: 'select', sort_order: 10, is_custom: true }),
    );
    renderEdit();

    fireEvent.click(await screen.findByLabelText('在基础信息下增补信息'));
    fireEvent.change(screen.getByPlaceholderText('信息名称，例如「临时调试口令」'), { target: { value: '合同编号' } });
    fireEvent.click(screen.getByRole('button', { name: '下拉选择' }));
    fireEvent.click(screen.getByRole('button', { name: '增补' }));

    await waitFor(() => {
      expect(createCustomInfoNodeApi).toHaveBeenCalledWith('P1', {
        parent_id: 'r1', title: '合同编号', content_type: 'text', sort_order: 10,
      });
    });
    // 内容形式跟着切到下拉（类型的调整走改定义接口）
    await waitFor(() => expect(updateInfoNodeApi).toHaveBeenCalledWith('server-9', { content_type: 'select' }));
    expect(await screen.findByText('合同编号')).toBeTruthy();
    // 普通用户没有结构操作入口
    expect(screen.queryByLabelText('编辑基础信息')).toBeNull();
    expect(screen.queryByLabelText('更多操作')).toBeNull();
  });

  it('任何节点下都能增补，到第 4 层封顶', async () => {
    authState.permissions = [];
    authState.projectIds = [];
    // 层级：基础信息 1 → 车辆 2 → 车型3 3 → 数量 4；夹具不带 allow_custom，一律可增补
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [node({
          id: 'c1', parent_id: 'r1', title: '车辆', sort_order: 0,
          children: [node({
            id: 'c2', parent_id: 'c1', title: '车型3', sort_order: 0,
            children: [node({ id: 'c3', parent_id: 'c2', title: '数量', sort_order: 0 })],
          })],
        })],
      }),
    ]);
    renderEdit();

    // 模板里没开过增补的位置（车辆）和增补出来的节点（车型3）一样能加
    expect(await screen.findByLabelText('在车辆下增补信息')).toBeTruthy();
    expect(screen.getByLabelText('在车型3下增补信息')).toBeTruthy();
    // 第 4 层不再给入口：层数是唯一的边界（后端同样会拒）
    expect(screen.queryByLabelText('在数量下增补信息')).toBeNull();
  });

  it('一级标签的历史：拉整棵子树的记录，按节点分组渲染成 Markdown', async () => {
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({
        id: 'h1',
        operator: 'zhangsan',
        operator_name: '张三',
        detail: '把标题从「基础」改为「基础信息」',
        created_at: '2026-09-14 11:00:00',
      }),
      // 子节点的变动：一级标签的「修改记录」里按「父 / 子」路径单列一节
      change({
        id: 'h2',
        node_id: 'c1',
        parent_id: 'r1',
        node_title: '客户信息',
        operator_name: '李四',
        detail: '把内容从「空」改为「中力」',
        created_at: '2026-09-14 10:30:00',
      }),
      // 已从树里删掉的子节点：标题用记录里的名称快照
      change({
        id: 'h3',
        node_id: 'gone',
        parent_id: 'r1',
        node_title: '旧地址',
        action: 'delete',
        operator_name: '王五',
        detail: '删除节点「旧地址」',
        created_at: '2026-09-14 10:00:00',
      }),
    ]);
    renderEdit();

    fireEvent.click(await screen.findByLabelText('查看基础信息的编辑历史'));

    // 根节点取的是整棵子树（比单节点多 include），子节点自己那份历史不受影响
    expect(fetchInfoNodeChangesApi).toHaveBeenCalledWith('P1', 'r1', { includeDescendants: true, limit: 200 });
    // 渲染成文档而不是原样吐 md 源码：标题是 h1，节点分组是 h2（路径当小标题）
    const title = await screen.findByText('基础信息 · 修改记录');
    expect(title.tagName).toBe('H1');
    expect(Array.from(document.querySelectorAll('.mac-history__md h2')).map((el) => el.textContent)).toEqual([
      '基础信息',
      '基础信息 / 客户信息',
      '已删除 · 旧地址',
    ]);
    expect(screen.getByText(/共 3 条记录 · 涉及 3 个节点/)).toBeTruthy();
    // 每条记录：时间 / 操作人 / 动作 / 变动
    expect(Array.from(document.querySelectorAll('.mac-history__md li')).map((el) => el.textContent)).toEqual([
      '2026-09-14 11:00:00 张三 · 修改：把标题从「基础」改为「基础信息」',
      '2026-09-14 10:30:00 李四 · 修改：把内容从「空」改为「中力」',
      '2026-09-14 10:00:00 王五 · 删除：删除节点「旧地址」',
    ]);
  });

  it('识别不到操作人时回退成「未知用户」，没有 detail 时按节点标题兜底', async () => {
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([change({ id: 'h1', action: 'create', detail: null })]);
    renderEdit();
    fireEvent.click(await screen.findByLabelText('查看基础信息的编辑历史'));

    expect(await screen.findByText(/未知用户 · 新增：新增节点「基础信息」/)).toBeTruthy();
  });

  it('该节点有新记录时历史按钮出小红点，打开看过之后消失（已读水位存本机）', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: '01a0a8f3-9f64-7103-82df-90d24a472f2c', operator_name: '张三', detail: '把内容从「空」改为「中力」' }),
    ]);
    renderEdit();

    const historyBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    await waitFor(() => expect(historyBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    // 没有记录的节点不出红点
    expect(screen.getByLabelText('查看客户信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();

    fireEvent.click(historyBtn);
    expect(await screen.findByText(/张三 · 修改：把内容从「空」改为「中力」/)).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
    // 已读水位 = 该节点最新记录 id，按「项目 + 登录用户」存，互不影响
    expect(JSON.parse(localStorage.getItem('project-info-tree:history-seen:P1:admin') ?? '{}')).toEqual({
      r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c',
    });
  });

  it('已看过（水位就是最新记录）的节点不出小红点', async () => {
    localStorage.setItem(
      'project-info-tree:history-seen:P1:admin',
      JSON.stringify({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' }),
    );
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' });
    renderEdit();

    const historyBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    await waitFor(() => expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalled());
    expect(historyBtn.querySelector('.mac-info-row__op-dot')).toBeNull();
  });

  it('保存成功后立即重算历史：自己刚改的节点也带上小红点（没点开过就一直带）', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi)
      .mockResolvedValueOnce({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' }) // 进入页面：r1 已读
      .mockResolvedValue({ r1: '01a0a8f3-9f64-7105-b1c2-3d4e5f607182' }); // 保存后：r1 有了新记录
    localStorage.setItem(
      'project-info-tree:history-seen:P1:admin',
      JSON.stringify({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' }),
    );
    vi.mocked(updateInfoNodeApi).mockResolvedValue(node({ id: 'r1', title: '基础信息2' }));
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({ id: 'r1', title: '基础信息', sort_order: 0, is_custom: true }),
    ]);
    renderEdit();

    const historyBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    await waitFor(() => expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledTimes(1));
    expect(historyBtn.querySelector('.mac-info-row__op-dot')).toBeNull();

    // 改名保存成功 → 立即再拉一次各节点最新记录 → 小红点出现
    fireEvent.click(await screen.findByLabelText('编辑基础信息'));
    const input = document.querySelector('.mac-info-row__input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '基础信息2' } });
    fireEvent.blur(input);

    await waitFor(() => expect(updateInfoNodeApi).toHaveBeenCalledWith('r1', { title: '基础信息2' }));
    await waitFor(() => {
      expect(screen.getByLabelText('查看基础信息2的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
    });
    expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledTimes(2); // 进页面 1 次 + 保存成功后 1 次
  });

  it('子节点有新变动时，所在的一级节点（根节点）同样出小红点', async () => {
    // 只有子节点 c1 有新记录，根节点 r1 自己没有
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ c1: 'h-c1' });
    renderEdit();

    const rootBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    const childBtn = screen.getByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    expect(rootBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy();
  });

  it('点开子节点历史后，子节点与根节点的红点一起消失（根节点的点只汇总未读的变动）', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ c1: 'h-c1' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: 'h-c1', node_id: 'c1', parent_id: 'r1', node_title: '客户信息', detail: '把内容从「空」改为「中力」' }),
    ]);
    renderEdit();

    const childBtn = await screen.findByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();

    fireEvent.click(childBtn);
    // 子节点仍是「只看自己」的逐条列表，不带子树口径
    expect(fetchInfoNodeChangesApi).toHaveBeenCalledWith('P1', 'c1', {});
    expect(await screen.findByText('把内容从「空」改为「中力」')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看客户信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
      expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
  });

  it('根节点自身还有别的未读变动时，点开某一处后根节点的红点保留', async () => {
    // c1 与根节点 r1 都有未读记录
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r1: 'h-r1', c1: 'h-c1' });
    vi.mocked(fetchInfoNodeChangesApi).mockImplementation(async (_projectId, nodeId) =>
      nodeId === 'c1'
        ? [change({ id: 'h-c1', node_id: 'c1', parent_id: 'r1', node_title: '客户信息', detail: '改了子节点' })]
        : [change({ id: 'h-r1', node_id: 'r1', node_title: '基础信息', detail: '改了根节点' })],
    );
    renderEdit();

    const childBtn = await screen.findByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());

    fireEvent.click(childBtn);
    expect(await screen.findByText('改了子节点')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看客户信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
    // 根节点自己的记录还没看过 → 红点还在
    expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
  });

  it('深层节点（第 4 层）有新变动时，它的每一层上级都出小红点', async () => {
    const deepTree: ApiInfoNode[] = [
      node({
        id: 'h1', title: '硬件', sort_order: 0,
        children: [
          node({
            id: 'v1', parent_id: 'h1', title: '车辆', sort_order: 0,
            children: [
              node({
                id: 'm1', parent_id: 'v1', title: '车型1', sort_order: 0,
                children: [node({ id: 'q1', parent_id: 'm1', title: '数量', value: '2', sort_order: 0 })],
              }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(deepTree);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ q1: 'h-q1' });
    renderEdit();

    const leafBtn = await screen.findByLabelText('查看数量的编辑历史');
    await waitFor(() => expect(leafBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    // 数量 → 车型1 → 车辆 → 硬件，一路都带点
    expect(screen.getByLabelText('查看车型1的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
    expect(screen.getByLabelText('查看车辆的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
    expect(screen.getByLabelText('查看硬件的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
  });

  it('点开链上任意一处的历史，整条小红点一起消失（子树整体标记已读）', async () => {
    const deepTree: ApiInfoNode[] = [
      node({
        id: 'h1', title: '硬件', sort_order: 0,
        children: [
          node({
            id: 'v1', parent_id: 'h1', title: '车辆', sort_order: 0,
            children: [
              node({
                id: 'm1', parent_id: 'v1', title: '车型1', sort_order: 0,
                children: [node({ id: 'q1', parent_id: 'm1', title: '数量', value: '2', sort_order: 0 })],
              }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(deepTree);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ q1: 'h-q1' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: 'h-q1', node_id: 'q1', parent_id: 'm1', node_title: '数量', detail: '把内容从「2」改为「6」' }),
    ]);
    renderEdit();

    const carBtn = await screen.findByLabelText('查看车辆的编辑历史');
    await waitFor(() => expect(carBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());

    // 点的是用户看得见的那个点（车辆），不是真正变动的叶子节点
    fireEvent.click(carBtn);
    expect(await screen.findByText('把内容从「2」改为「6」')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看车辆的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
      expect(screen.getByLabelText('查看数量的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
      expect(screen.getByLabelText('查看车型1的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
      expect(screen.getByLabelText('查看硬件的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
  });

  it('已删除节点的记录（树里没有这一行）不会把红点挂到别处', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ gone: 'h-gone', c1: 'h-c1' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: 'h-c1', node_id: 'c1', parent_id: 'r1', node_title: '客户信息', detail: '改了子节点' }),
    ]);
    renderEdit();

    const childBtn = await screen.findByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());

    fireEvent.click(childBtn);
    await waitFor(() => {
      expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
  });
});
