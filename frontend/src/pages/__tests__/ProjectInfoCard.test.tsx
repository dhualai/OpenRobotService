import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ProjectInfoCard from '../admin/ProjectInfoCard';
import {
  fetchInfoNodeChangeSummaryApi, fetchInfoNodeMarksApi, fetchInfoTree, toggleInfoNodeMarkApi,
  type ApiInfoNode,
} from '@/api/infoNodes';

// tdesign 的 Toast 只在关注失败时调用：桩掉避免渲染真实弹层
vi.mock('tdesign-mobile-react', () => ({ Toast: vi.fn() }));

// 卡片的数据源是后端信息树接口：这里 mock 出固定的树，验证加载/渲染/筛选/失败重试
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

// 红点水位按「项目 + 登录用户」存本机：固定登录用户，测出的水位 key 可预期
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string }) => unknown) => selector({ username: 'admin' }),
}));

const TS = '2026-09-14 10:00:00';
const node = (partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode => ({
  project_id: 'CODE-1',
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
    children: [
      node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 }),
      node({
        id: 'c2', parent_id: 'r1', title: '项目类型', content_type: 'select', sort_order: 1,
        // 选项是字段定义（节点上），selected 是本项目的值：服务端按值类型编码后下发，不再是 JSON 字符串
        options: ['PK 项目', '试点项目'],
        value: { selected: '', options: ['PK 项目', '试点项目'] },
      }),
    ],
  }),
  node({
    id: 'r2',
    title: '硬件',
    sort_order: 1,
    children: [
      node({
        id: 'c3', parent_id: 'r2', title: '载具类型', content_type: 'select', sort_order: 0,
        options: ['托盘', '料笼'],
        value: { selected: '托盘', options: ['托盘', '料笼'] },
      }),
    ],
  }),
];

const renderCard = (projectId: string, canEdit = true) =>
  render(
    <MemoryRouter>
      <ProjectInfoCard projectId={projectId} canEdit={canEdit} />
    </MemoryRouter>
  );

describe('ProjectInfoCard（项目信息管理卡）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(fetchInfoTree).mockResolvedValue(TREE);
    vi.mocked(fetchInfoNodeMarksApi).mockResolvedValue([]);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({});
  });

  it('加载后显示后端返回的一级标签', async () => {
    renderCard('CODE-1');
    expect(fetchInfoTree).toHaveBeenCalledWith('CODE-1');
    expect(await screen.findByRole('button', { name: /基础信息/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /硬件/ })).toBeTruthy();
  });

  it('文档式渲染：只展示已填写的信息，没填的字段不出占位', async () => {
    renderCard('CODE-1');
    expect(await screen.findByText('客户信息')).toBeTruthy();
    expect(screen.getByText('中力')).toBeTruthy();
    // c2（项目类型，未选择）没填过：不渲染它，也不出「未选择」这类占位
    expect(screen.queryByText('项目类型')).toBeNull();
    expect(screen.queryByText('未选择')).toBeNull();
    expect(screen.getByText('托盘')).toBeTruthy();
  });

  it('标题与内容同行（同一 .mac-doc__row 内），根节点各占一个一级分组', async () => {
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    const rows = Array.from(document.querySelectorAll('.mac-doc__row'));
    const rowOf = (title: string) =>
      rows.find((row) => row.querySelector('.mac-doc__label')?.textContent === title);

    // 标题与内容在同一行里，而不是标题一行、内容另起一行
    expect(rowOf('客户信息')?.querySelector('.mac-doc__value')?.textContent).toBe('中力');
    expect(rowOf('载具类型')?.querySelector('.mac-doc__value')?.textContent).toBe('托盘');
    // 分支节点只有标题、没有内容块
    expect(rowOf('基础信息')?.querySelector('.mac-doc__value')).toBeNull();

    // 每个根节点一个一级分组（相邻分组之间由 CSS 画浅灰横线）
    expect(document.querySelectorAll('.mac-doc__section--d1').length).toBe(2);
    // 二级节点整体缩进一级：只有填过值的那些（基础信息下 c2 空 → 只剩 1 个）
    expect(document.querySelectorAll('.mac-doc__section--d2').length).toBe(2);
  });

  it('整片没值的标签：点开不展示内容，提示信息不足请补充', async () => {
    const emptyTree: ApiInfoNode[] = [
      node({
        id: 'r1',
        title: '基础信息',
        sort_order: 0,
        children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: null, sort_order: 0 })],
      }),
      node({
        id: 'r2',
        title: '硬件',
        sort_order: 1,
        children: [
          node({
            id: 'c3', parent_id: 'r2', title: '载具类型', content_type: 'select', sort_order: 0,
            options: ['托盘', '料笼'],
            value: { selected: '托盘', options: ['托盘', '料笼'] },
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(emptyTree);
    renderCard('CODE-1');

    // 默认展示全部：空标签下的字段不渲染，有条目的标签照常展示
    await screen.findByText('载具类型');
    expect(screen.queryByText('客户信息')).toBeNull();

    // 点开空标签 → 不展示内容，改为提示补充
    fireEvent.click(screen.getByRole('button', { name: /^基础信息/ }));
    expect(screen.queryByText('客户信息')).toBeNull();
    expect(document.querySelector('.mac-doc')).toBeNull();
    expect(screen.getByText('信息不足请补充')).toBeTruthy();
    expect(screen.getByText(/「基础信息」下还没有任何已填写的信息/)).toBeTruthy();
    // 有编辑权限时给出补充入口（普通用户不回编辑页）
    expect(screen.getByRole('button', { name: /去补充信息/ })).toBeTruthy();
  });

  it('无编辑权限时提示里不给「去补充信息」入口', async () => {
    const emptyTree: ApiInfoNode[] = [
      node({
        id: 'r1',
        title: '基础信息',
        sort_order: 0,
        children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: null, sort_order: 0 })],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(emptyTree);
    renderCard('CODE-1', false);

    fireEvent.click(await screen.findByRole('button', { name: /^基础信息/ }));
    expect(screen.getByText('信息不足请补充')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /去补充信息/ })).toBeNull();
  });

  it('全树都没写过内容：不出文档空壳，直接提示补充', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r1',
        title: '基础信息',
        sort_order: 0,
        children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '   ', sort_order: 0 })],
      }),
    ]);
    renderCard('CODE-1');
    expect(await screen.findByText('信息不足请补充')).toBeTruthy();
    expect(screen.queryByText('客户信息')).toBeNull();
  });

  it('空分支（子节点全没值、自己也没值）连标题一起不渲染，不留下空壳', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r1',
        title: '基础信息',
        sort_order: 0,
        children: [
          node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 }),
          node({
            id: 'g1', parent_id: 'r1', title: '联系方式', sort_order: 1,
            children: [
              node({ id: 'd1', parent_id: 'g1', title: '电话', value: '', sort_order: 0 }),
              node({ id: 'd2', parent_id: 'g1', title: '邮箱', value: null, sort_order: 1 }),
            ],
          }),
        ],
      }),
    ]);
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    // 有值的字段照常；空分支的标题与它的空字段都不出现
    expect(screen.getByText('中力')).toBeTruthy();
    expect(screen.queryByText('联系方式')).toBeNull();
    expect(screen.queryByText('电话')).toBeNull();
    expect(screen.queryByText('邮箱')).toBeNull();
    expect(screen.queryByText('（未填写）')).toBeNull();
  });

  it('车型1（下拉 + 数量子节点）：型号已选、数量未填时，型号照常展示且分支不被裁掉', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValue([
      node({
        id: 'r2',
        title: '硬件',
        sort_order: 0,
        children: [
          node({
            id: 'v1', parent_id: 'r2', title: '车辆', sort_order: 0,
            children: [
              node({
                id: 'm1', parent_id: 'v1', title: '车型1', content_type: 'select', sort_order: 0,
                options: ['XC1051', 'XCD061'],
                value: { selected: 'XC1051', options: ['XC1051', 'XCD061'] },
                children: [
                  node({ id: 'q1', parent_id: 'm1', title: '数量', value: '', sort_order: 0 }),
                ],
              }),
            ],
          }),
        ],
      }),
    ]);
    renderCard('CODE-1');

    // 车型1 带子节点，但它自己的下拉选中项要展示出来（不是只当分支标题）
    expect(await screen.findByText('车型1')).toBeTruthy();
    const rows = Array.from(document.querySelectorAll('.mac-doc__row'));
    const modelRow = rows.find((row) => row.querySelector('.mac-doc__label')?.textContent === '车型1');
    expect(modelRow?.querySelector('.mac-doc__value')?.textContent).toBe('XC1051');
    // 没填的「数量」不出占位，但它的存在没把整条分支判成空
    expect(screen.queryByText('数量')).toBeNull();
    expect(screen.queryByText('信息不足请补充')).toBeNull();
  });

  it('点选一级标签只显示该标签下的内容', async () => {
    renderCard('CODE-1');
    fireEvent.click(await screen.findByRole('button', { name: /^硬件/ }));
    const doc = document.querySelector('.mac-doc') as HTMLElement;
    expect(within(doc).getByText('载具类型')).toBeTruthy();
    expect(within(doc).queryByText('项目类型')).toBeNull();
    expect(within(doc).queryByText('客户信息')).toBeNull();
  });

  it('加载失败时显示重试，点击后重新拉取', async () => {
    vi.mocked(fetchInfoTree).mockRejectedValueOnce(new Error('boom'));
    renderCard('CODE-1');
    expect(await screen.findByText('信息节点加载失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByRole('button', { name: /基础信息/ })).toBeTruthy();
    await waitFor(() => expect(fetchInfoTree).toHaveBeenCalledTimes(2));
  });

  it('无编辑权限（新建项目）时不显示「编辑」入口', async () => {
    renderCard('new', false);
    await screen.findByRole('button', { name: /基础信息/ });
    expect(screen.queryByText('编辑')).toBeNull();
  });

  // —— 关注星标（子节点右侧）：点它把节点订进「项目动态」 ——

  it('只给已填写信息的子节点出关注星标，一级标签本身没有', async () => {
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    expect(screen.getByRole('button', { name: '关注客户信息' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '关注载具类型' })).toBeTruthy();
    // 没填过的节点不渲染，自然也没有星标
    expect(screen.queryByRole('button', { name: '关注项目类型' })).toBeNull();
    // 根节点（基础信息 / 硬件）不出星标
    expect(screen.queryByRole('button', { name: /关注基础信息/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /关注硬件/ })).toBeNull();
  });

  it('后端标注列表点亮对应星标（aria-pressed），点星标调切换接口并翻转状态', async () => {
    vi.mocked(fetchInfoNodeMarksApi).mockResolvedValue(['c1']);
    vi.mocked(toggleInfoNodeMarkApi).mockResolvedValue(true);
    renderCard('CODE-1');

    const star = await screen.findByRole('button', { name: '取消关注客户信息' });
    expect(star.getAttribute('aria-pressed')).toBe('true');

    // 未标注的节点：点击 → 调接口 → 变为已关注
    const other = screen.getByRole('button', { name: '关注载具类型' });
    expect(other.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(other);
    // 必须带上 projectId：全局节点各项目共用同一 node_id，后端推不出是哪个项目里关注的
    expect(toggleInfoNodeMarkApi).toHaveBeenCalledWith('c3', 'CODE-1');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '取消关注载具类型' })).toBeTruthy();
    });
  });

  it('关注变化后通知外层（「项目动态」卡刷新）；接口失败回滚星标', async () => {
    const onMarkChange = vi.fn();
    vi.mocked(toggleInfoNodeMarkApi)
      .mockResolvedValueOnce(true)
      .mockRejectedValueOnce(new Error('boom'));
    render(
      <MemoryRouter>
        <ProjectInfoCard projectId="CODE-1" canEdit onMarkChange={onMarkChange} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '关注客户信息' }));
    await waitFor(() => expect(onMarkChange).toHaveBeenCalledTimes(1));

    // 第二次失败：乐观点亮后回滚为未关注
    fireEvent.click(screen.getByRole('button', { name: '关注载具类型' }));
    await waitFor(() => {
      expect(toggleInfoNodeMarkApi).toHaveBeenCalledWith('c3', 'CODE-1');
      expect(screen.getByRole('button', { name: '关注载具类型' }).getAttribute('aria-pressed')).toBe('false');
    });
    expect(onMarkChange).toHaveBeenCalledTimes(1);
  });

  // —— 标签池角标：红点 = 该标签下有本机没看过的操作记录（与编辑页行内红点同一套水位） ——

  const chipOf = (title: string) =>
    Array.from(document.querySelectorAll('.mac-tagpool__chip'))
      .find((chip) => chip.textContent?.startsWith(title)) as HTMLElement | undefined;

  it('子节点有未读变动时归到所在的一级标签：该标签右上角出红点，别的标签没有', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ c1: 'rec-1' });
    renderCard('CODE-1');
    await screen.findByText('客户信息');

    await waitFor(() => expect(chipOf('基础信息')?.querySelector('.mac-tagpool__dot')).toBeTruthy());
    expect(chipOf('硬件')?.querySelector('.mac-tagpool__dot')).toBeNull();
    expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledWith('CODE-1');
    // 同类标签同时带感叹号（c2 未选择）：感叹号仍在标签角上，红点挪到感叹号右上角
    expect(chipOf('基础信息')?.querySelector('.mac-tagpool__warn')).toBeTruthy();
    expect(chipOf('基础信息')?.querySelector('.mac-tagpool__dot--on-warn')).toBeTruthy();
    // 只有红点的标签不带挪位修饰
    expect(chipOf('硬件')?.querySelector('.mac-tagpool__dot--on-warn')).toBeNull();
  });

  it('一级标签自身的未读记录也出红点；已读（水位=最新记录 id）的标签不出', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r2: 'rec-r2', c1: 'rec-c1' });
    localStorage.setItem(
      'project-info-tree:history-seen:CODE-1:admin',
      JSON.stringify({ c1: 'rec-c1' }),
    );
    renderCard('CODE-1');
    await screen.findByText('客户信息');

    await waitFor(() => expect(chipOf('硬件')?.querySelector('.mac-tagpool__dot')).toBeTruthy());
    expect(chipOf('基础信息')?.querySelector('.mac-tagpool__dot')).toBeNull();
  });

  it('标签下方给出口径说明：感叹号=信息未填写，红点=有新变动（看过「历史」后消失）', async () => {
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    expect(screen.getByText('标签下有信息未填写')).toBeTruthy();
    expect(screen.getByText('标签下有新变动，点开对应节点的「历史」后消失')).toBeTruthy();
  });
});
