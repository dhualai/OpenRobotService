// 我要摇人页的「车体扫码进入」链路（本层只测编排）：
//   解析 scene → 查一次码记录 → 仅「已发布（出厂）」才把车辆信息交给确认弹窗 → 确认后另起空白会话。
//   未携带 / 查无此码 / 非已发布 / 查询异常一律静默不打扰。
//
// 打桩边界（只打「进程边界」与「带副作用的子件」，渲染细节不在此层重复验）：
//   - 网络：@/api/qrcode、@/api/ai 打桩，不触网
//   - 确认弹窗：打桩成 props 探针 —— 只验「交给它什么车辆信息 + 两个回调怎么接」；
//     三行文案 / 缺项占位 / 确认中禁用等渲染契约由 VehicleConfirmDialog.test.tsx 守
//   - 与用例无关的重型子件（ChatPanel 会发请求，抽屉/头像菜单/关注提醒自带弹层）轻量替身
//   - 状态：workbench / auth 用真 store —— 断言跨组件契约，不用假 hook 替身去猜调用形状
//   - 路由：MemoryRouter 真跑（useSearchParams/useNavigate 走真实实现），「一次性摘掉 scene」才可断言
import { useState, type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { VehicleConfirmDialogProps } from '@/shared/components/VehicleConfirmDialog';

const mockFetchQrcodeByScene = vi.fn();

/** 确认弹窗探针：每次渲染记下编排层交来的 props；可见时留一个锚点，不可见时不留 DOM */
let dialogProps: VehicleConfirmDialogProps | null = null;

vi.mock('@/api/qrcode', () => ({
  fetchQrcodeByScene: (scene: string) => mockFetchQrcodeByScene(scene),
}));

vi.mock('@/api/ai', () => ({
  qaListTickets: vi.fn().mockResolvedValue({ data: { active_total: 0 } }),
}));

vi.mock('@/shared/components/VehicleConfirmDialog', () => ({
  default: (props: VehicleConfirmDialogProps) => {
    dialogProps = props;
    return props.visible ? <div data-testid="vehicle-confirm" /> : null;
  },
}));

vi.mock('@/shared/components/ChatPanel', () => ({ default: () => <div data-testid="chat-panel" /> }));
// 顶栏来自 tdesign：只保留 Navbar 一个最小替身（真实 tdesign 会让本文件收集阶段卡住）
vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <div>{title}</div>,
}));
vi.mock('@/shared/components/ConversationDrawer', () => ({ default: () => null }));
vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('@/shared/components/SubscriptionReminder', () => ({ default: () => null }));

import CallView, { resolveSceneCode } from '../call/CallView';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';

const SCENE = 'proj_abc123def456';

const publishedRecord = {
  scene_str: SCENE,
  status: 'published',
  project_name: '项目A',
  customer_name: '客户A',
  vehicle_model: 'XQE',
};

/** 外层探针：暴露当前地址栏 query + 可手动卸载/重挂 CallView（模拟切 Tab） */
function Harness() {
  const location = useLocation();
  const [mounted, setMounted] = useState(true);
  return (
    <>
      <div data-testid="search">{location.search}</div>
      <button data-testid="remount" onClick={() => setMounted((v) => !v)}>
        remount
      </button>
      {mounted && <CallView />}
    </>
  );
}

const renderCall = (search = '') =>
  render(
    <MemoryRouter initialEntries={[`/call${search}`]}>
      <Harness />
    </MemoryRouter>,
  );

/** 用户点「确认」/「暂不」：按钮本身的点击语义由弹窗自身用例覆盖，这里只验编排层接线 */
const clickConfirm = () => act(() => dialogProps?.onConfirm());
const clickCancel = () => act(() => dialogProps?.onCancel());

describe('resolveSceneCode（URL 场景值解析）', () => {
  it('取到合法 scene 并去空白', () => {
    expect(resolveSceneCode(new URLSearchParams(`scene=${SCENE}`))).toBe(SCENE);
    expect(resolveSceneCode(new URLSearchParams('scene=%20proj_x1%20'))).toBe('proj_x1');
  });

  it('未携带 / 空值 / 非法字符 / 超长一律返回 null', () => {
    expect(resolveSceneCode(new URLSearchParams(''))).toBeNull();
    expect(resolveSceneCode(new URLSearchParams('scene='))).toBeNull();
    expect(resolveSceneCode(new URLSearchParams('scene=proj%2Fadmin'))).toBeNull();
    expect(resolveSceneCode(new URLSearchParams(`scene=${'a'.repeat(65)}`))).toBeNull();
  });
});

describe('CallView 车体扫码进入', () => {
  beforeEach(() => {
    dialogProps = null;
    mockFetchQrcodeByScene.mockReset();
    useAuthStore.setState({ username: 'bob', isAdmin: false });
    useWorkbenchStore.setState({
      conversationId: 5,
      conversationTitle: '上一轮会话',
      pendingNewConversation: false,
      vehicleContext: null,
    });
  });

  it('未携带 scene：不发请求、不给弹窗，现有对话行为不变', async () => {
    renderCall();

    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeInTheDocument());
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
    expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument();
  });

  it('非法 scene：同样不发请求（白名单在请求之前挡掉）', async () => {
    renderCall('?scene=../../etc/passwd');

    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeInTheDocument());
    expect(mockFetchQrcodeByScene).not.toHaveBeenCalled();
  });

  it('码状态为「已发布」：查一次码，并把码记录映射成车辆信息交给弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(screen.getByTestId('vehicle-confirm')).toBeInTheDocument());
    // 弹窗拿到的是「这个 scene 对应的码记录」映射结果（三行怎么渲染见弹窗自己的用例）
    expect(dialogProps?.info).toEqual({
      scene: SCENE,
      projectName: '项目A',
      customerName: '客户A',
      vehicleModel: 'XQE',
    });
    // 同一个 scene 只查一次（幂等）
    expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1);
    expect(mockFetchQrcodeByScene).toHaveBeenCalledWith(SCENE);
  });

  it('码记录缺项：缺的字段折成空串交给弹窗（占位符由弹窗层补）', async () => {
    mockFetchQrcodeByScene.mockResolvedValue({
      ...publishedRecord,
      customer_name: null,
      vehicle_model: null,
    });
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(screen.getByTestId('vehicle-confirm')).toBeInTheDocument());
    expect(dialogProps?.info).toEqual({
      scene: SCENE,
      projectName: '项目A',
      customerName: '',
      vehicleModel: '',
    });
  });

  it('非「已发布」状态：静默降级，不给弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue({ ...publishedRecord, status: 'confirming' });
    renderCall(`?scene=${SCENE}`);

    // 等异步边界（打桩的查询）落地，再看 UI 结果
    await waitFor(() => expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument();
  });

  it('查无此码（api 返回 null）：静默降级，不给弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(null);
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument();
  });

  it('处理过就把 scene 从地址栏摘掉（保留其它参数），避免切 Tab 回来重弹', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}&openid=oXk4js`);

    await waitFor(() => expect(screen.getByTestId('vehicle-confirm')).toBeInTheDocument());

    await waitFor(() => expect(screen.getByTestId('search').textContent).not.toContain('scene='));
    // 其它参数原样保留（openid 后续还要用）
    expect(screen.getByTestId('search').textContent).toContain('openid=oXk4js');
  });

  it('卸载再挂载（等价于切 Tab 回来）不会重复查询、不会重弹弹窗', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(screen.getByTestId('vehicle-confirm')).toBeInTheDocument());
    clickCancel();
    await waitFor(() => expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument());
    expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1);

    // 卸载 → 重新挂载，地址栏此时已不含 scene
    fireEvent.click(screen.getByTestId('remount'));
    fireEvent.click(screen.getByTestId('remount'));

    await waitFor(() => expect(screen.getByTestId('chat-panel')).toBeInTheDocument());
    expect(mockFetchQrcodeByScene).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument();
  });

  it('点「确认」：另起空白会话 + 车辆上下文入 store + 弹窗关闭', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(screen.getByTestId('vehicle-confirm')).toBeInTheDocument());
    clickConfirm();

    const state = useWorkbenchStore.getState();
    // 「这次扫码专用」：清空当前会话，标记 pending 以拦住进入页的自动选最近会话
    expect(state.conversationId).toBeNull();
    expect(state.pendingNewConversation).toBe(true);
    expect(state.vehicleContext).toEqual({
      scene: SCENE,
      projectName: '项目A',
      customerName: '客户A',
      vehicleModel: 'XQE',
    });
    await waitFor(() => expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument());
  });

  it('点「暂不」：只关弹窗，不写车辆上下文、不动当前会话', async () => {
    mockFetchQrcodeByScene.mockResolvedValue(publishedRecord);
    renderCall(`?scene=${SCENE}`);

    await waitFor(() => expect(screen.getByTestId('vehicle-confirm')).toBeInTheDocument());
    clickCancel();

    const state = useWorkbenchStore.getState();
    expect(state.vehicleContext).toBeNull();
    expect(state.conversationId).toBe(5);
    await waitFor(() => expect(screen.queryByTestId('vehicle-confirm')).not.toBeInTheDocument());
  });
});
