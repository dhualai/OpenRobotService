// 我要摇人 —— 全屏 AI 对话 + 左侧会话抽屉 + 右上角历史工单入口 + 车体扫码进入的车辆信息确认
import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import { Menu, CalendarDays } from 'lucide-react';

import ChatPanel from '@/shared/components/ChatPanel';
import ConversationDrawer from '@/shared/components/ConversationDrawer';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import SubscriptionReminder from '@/shared/components/SubscriptionReminder';
import VehicleConfirmDialog from '@/shared/components/VehicleConfirmDialog';
import { qaListTickets } from '@/api/ai';
import { fetchQrcodeByScene } from '@/api/qrcode';
import { useWorkbenchStore, type VehicleContext } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';

/** 码记录状态「已发布」即车辆已出厂，才进入信息确认流程（其余状态一律静默不打扰） */
const STATUS_PUBLISHED = 'published';

/** 场景值白名单：2026-09-30 起 scene = str(id)（纯数字）；这里放宽为 [A-Za-z0-9_-]（1~64），
 *  非数字 scene 由后端 by-scene 400 拦下（查不到同样静默降级，见下方 effect） */
const SCENE_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

/**
 * 从 URL 查询串解析车体码（场景值）。
 *
 * 只认后端拼进跳转链接的 scene（`/app/call?scene=xxx`）；未携带或不合规返回 null，
 * 调用方据此完全保持现有普通对话行为（不发请求、不额外渲染）。
 */
export function resolveSceneCode(search: URLSearchParams): string | null {
  const raw = (search.get('scene') ?? '').trim();
  return SCENE_PATTERN.test(raw) ? raw : null;
}

export default function CallView() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [unread, setUnread] = useState(0);
  const {
    tasksRefreshKey,
    drawerOpen,
    setDrawerOpen,
    conversationTitle,
    requestNewConversation,
    setVehicleContext,
  } = useWorkbenchStore();
  const username = useAuthStore((s) => s.username);
  const isAdmin = useAuthStore((s) => s.isAdmin);

  // 车体扫码进入：本次访问携带的场景值（未携带/不合规为 null）
  const sceneCode = useMemo(() => resolveSceneCode(searchParams), [searchParams]);
  // 待确认的车辆信息 + 弹窗开合 + 确认中（挡住同一帧内的重复点击）
  const [vehicleInfo, setVehicleInfo] = useState<VehicleContext | null>(null);
  const [vehicleDialogVisible, setVehicleDialogVisible] = useState(false);
  const [vehicleConfirming, setVehicleConfirming] = useState(false);
  // 同一个场景值只查一次、只弹一次（用户关掉后本次访问不再重弹）
  const handledSceneRef = useRef<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        // 复用列表接口（limit=1 最小开销）取「除已关闭外」总数，无需额外统计接口
        const filters = !isAdmin && username ? { username } : undefined;
        const res = await qaListTickets(0, 1, filters);
        setUnread(res?.data?.active_total ?? 0);
      } catch { /* ignore */ }
    })();
  }, [tasksRefreshKey, username, isAdmin]);

  // 车体扫码进入监听：解析 scene → 查一次码记录 → 仅「已发布（出厂）」才弹车辆信息确认。
  // 未携带 / 查无此码 / 查询异常 / 非已发布 一律静默降级，不打扰用户，也不影响现有对话。
  useEffect(() => {
    if (!sceneCode || handledSceneRef.current === sceneCode) return;
    handledSceneRef.current = sceneCode;

    // 一次性消费这个深链参数：处理过就把 scene 从地址栏摘掉（replace，不留历史记录）。
    // 否则切 Tab 再回摇人页会重新挂载本组件，而地址里仍挂着 scene → 重复查询 + 重复弹窗；
    // 更糟的是那时用户可能已在这个新会话里聊了几句，再点一次确认会把该会话清空。
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('scene');
    setSearchParams(nextParams, { replace: true });

    (async () => {
      const record = await fetchQrcodeByScene(sceneCode);
      if (!record || record.status !== STATUS_PUBLISHED) return;
      setVehicleInfo({
        scene: sceneCode,
        projectName: record.project_name ?? '',
        customerName: record.customer_name ?? '',
        vehicleModel: record.vehicle_model ?? '',
      });
      setVehicleDialogVisible(true);
    })();
  }, [sceneCode, searchParams, setSearchParams]);

  /** 点「暂不」/点遮罩：只关弹窗，保持当前对话不动 */
  const handleVehicleCancel = useCallback(() => {
    setVehicleDialogVisible(false);
    setVehicleConfirming(false);
  }, []);

  /** 点「确认」：另起一个空白会话（这次扫码专用），再把车辆上下文投给 ChatPanel 注入引导消息 */
  const handleVehicleConfirm = useCallback(() => {
    const info = vehicleInfo;
    if (!info) return;
    setVehicleConfirming(true);
    // 两次 store 更新会被 React 批到同一次 render：ChatPanel 中「清空对话」的 effect 声明在
    // 「注入引导消息」的 effect 之前，因此顺序是先清空、再追加，引导消息不会被清空动作冲掉。
    requestNewConversation();
    setVehicleContext(info);
    setVehicleDialogVisible(false);
  }, [vehicleInfo, requestNewConversation, setVehicleContext]);

  return (
    <div className="app-shell">
      <SubscriptionReminder username={username} />
      {/* 内容区（抽屉打开时右挤）。
          ChatPanel 始终 mounted（showHistory 时 display:none 隐藏而非卸载，
          避免切历史后回来消息丢失 */}
      <div className={`app-shell__content ${drawerOpen ? 'is-shifted' : ''}`}>
        <div className="chat-view">
          <Navbar
            title={<span className="call-navbar-title">{conversationTitle}</span>}
            fixed
            left={
              <button className="navbar-menu-btn" onClick={() => setDrawerOpen(!drawerOpen)} aria-label="会话列表">
                <Menu size={20} strokeWidth={2} />
              </button>
            }
            right={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <button className="navbar-history-btn" onClick={() => navigate('/call/history')} aria-label="历史工单">
                  <CalendarDays size={20} strokeWidth={2} />
                  {unread > 0 && <span className="navbar-history-badge">{unread > 99 ? '99+' : unread}</span>}
                </button>
                <UserAvatarMenu />
              </div>
            }
          />
          <div className="call-full-chat">
            <ChatPanel scene="call" />
          </div>
        </div>
      </div>
      {/* 会话抽屉 + 遮罩 */}
      <ConversationDrawer visible={drawerOpen} onClose={() => setDrawerOpen(false)} />
      {/* 车体扫码进入：车辆信息确认（仅该码「已发布/已出厂」时出现，用户关掉后本次访问不再弹） */}
      <VehicleConfirmDialog
        visible={vehicleDialogVisible}
        info={vehicleInfo}
        confirming={vehicleConfirming}
        onConfirm={handleVehicleConfirm}
        onCancel={handleVehicleCancel}
      />
    </div>
  );
}
