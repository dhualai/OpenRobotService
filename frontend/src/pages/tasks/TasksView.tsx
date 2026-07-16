// 系统任务（供给视角）—— 上：AI 任务助手 / 下：工单卡片列表
// 卡片样式与输入卡片审美一致（白底 + 阴影 + 圆角）。
// 跨视图流转：消费 ticketDraft 自动建单；讨论按钮 → 带上下文跳回我要摇人。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, ActionSheet } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import Pagination from '@/shared/components/Pagination';
import SafeHtml from '@/shared/components/SafeHtml';
import ChatPanel from '@/shared/components/ChatPanel';
import { useWorkbenchStore } from '@/stores/workbench';
import { normalizeStatus, STATUS_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime, formatTime } from '@/shared/utils/url';

interface Attachment { id: string; url: string; }
interface Comment { id: string; content: string; author_name: string; created_at: string; }
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; ai_summary?: string; comments?: Comment[];
}

const pageSize = 20;
const statusTheme = (s: string): 'success' | 'primary' | 'warning' =>
  s === 'closed' ? 'success' : s === 'new' ? 'primary' : 'warning';

export default function TasksView() {
  const { id: routeId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const {
    tasksRefreshKey, selectedTicketId, ticketDraft,
    consumeTicketDraft, setSelectedTicketId, refreshTasks, goToTab, setChatContext,
  } = useWorkbenchStore();

  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const [detailId, setDetailId] = useState<string | null>(routeId || selectedTicketId);
  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [moreSheetId, setMoreSheetId] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', description: '' });

  const fetchTickets = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page), size: String(pageSize),
        ...(search && { keyword: search }),
        ...(statusFilter !== 'all' && { status: statusFilter }),
      });
      const data = await request<{ items: Ticket[]; total: number }>(`/?${params.toString()}`);
      setTickets(data.items || []);
      setTotal(data.total || 0);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [page, search, statusFilter]);

  useEffect(() => { fetchTickets(); }, [fetchTickets]);
  useEffect(() => { if (tasksRefreshKey > 0) fetchTickets(); }, [tasksRefreshKey]);

  // 消费 AI 转单草稿：自动建单 → 刷新 → 选中
  useEffect(() => {
    const draft = consumeTicketDraft();
    if (!draft) return;
    (async () => {
      try {
        const created = await request<Ticket>('/', { method: 'POST', body: JSON.stringify(draft) });
        Toast({ message: '工单已创建', theme: 'success' });
        refreshTasks();
        setSelectedTicketId(created.id);
        setDetailId(created.id);
        setPage(1);
      } catch (err) {
        Toast({ message: `建单失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticketDraft]);

  useEffect(() => {
    if (selectedTicketId) { setDetailId(selectedTicketId); setSelectedTicketId(null); }
  }, [selectedTicketId, setSelectedTicketId]);

  useEffect(() => {
    if (!detailId) { setDetail(null); return; }
    setDetailLoading(true);
    request<Ticket>(`/${detailId}?load_comments=true`)
      .then(setDetail)
      .catch((err) => Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setDetailLoading(false));
  }, [detailId]);

  const openDetail = (id: string) => { setDetailId(id); navigate(`/app/tasks/${id}`); };
  const closeDetail = () => { setDetailId(null); setDetail(null); navigate('/app/tasks'); };

  const handleUrge = async (t: Ticket) => {
    try { await request(`/${t.id}/urge`, { method: 'POST' }).catch(() => {}); Toast({ message: '已催办', theme: 'success' }); }
    catch { Toast({ message: '催办失败', theme: 'error' }); }
  };
  const handleEscalate = async (t: Ticket) => {
    try { await request(`/${t.id}/escalate`, { method: 'POST' }).catch(() => {}); Toast({ message: '已升级上报', theme: 'success' }); }
    catch { Toast({ message: '上报失败', theme: 'error' }); }
  };
  const copyId = (id: string) => {
    navigator.clipboard?.writeText(id).then(() => Toast({ message: '已复制工单号', theme: 'success' }));
  };
  const startEdit = () => { if (!detail) return; setEditForm({ title: detail.title, description: detail.description }); setEditing(true); };
  const saveEdit = async () => {
    if (!detail) return;
    try {
      await request(`/${detail.id}`, { method: 'PUT', body: JSON.stringify(editForm) });
      Toast({ message: '修改成功', theme: 'success' });
      setEditing(false);
      setDetailId(detail.id);
    } catch (err) {
      Toast({ message: `修改失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };
  const discuss = (t: Ticket) => {
    setChatContext({ ticketId: t.id, title: t.title, description: t.description });
    goToTab('call', { chatContext: { ticketId: t.id, title: t.title, description: t.description } });
    navigate('/app/call');
  };

  const statusTabs = [{ value: 'all', label: '全部' },
    ...Object.entries(STATUS_DISPLAY_MAP).map(([value, label]) => ({ value, label }))];

  return (
    <div className="tasks-view">
      <Navbar title="系统任务" fixed />

      {/* 上：AI 任务助手 */}
      <div className="tasks-top-chat">
        <ChatPanel scene="tasks" compact />
      </div>

      {/* 下：工单卡片列表 */}
      <div className="tasks-list-section">
        <div className="tasks-view__filters">
          <input
            className="tasks-search"
            placeholder="搜索工单…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          />
          <div className="tasks-tabs">
            {statusTabs.map((t) => (
              <button
                key={t.value}
                className={`tasks-tab ${statusFilter === t.value ? 'is-active' : ''}`}
                onClick={() => { setStatusFilter(t.value); setPage(1); }}
              >{t.label}</button>
            ))}
          </div>
        </div>

        <div className="tasks-cards">
          {loading ? <Loading text="加载中…" /> : tickets.length === 0 ? (
            <div className="tasks-empty">暂无工单</div>
          ) : tickets.map((t) => (
            <div key={t.id} className="task-card2" onClick={() => openDetail(t.id)}>
              <div className="task-card2__head">
                <Tag theme={statusTheme(t.status)}>{normalizeStatus(t.status)}</Tag>
                <span className="task-card2__type">{TICKET_TYPE_DISPLAY_MAP[t.ticket_type] || t.ticket_type || '其他'}</span>
                <span className="task-card2__more" onClick={(e) => { e.stopPropagation(); setMoreSheetId(t.id); }}>⋮</span>
              </div>
              <div className="task-card2__title">{t.title}</div>
              <div className="task-card2__meta">
                <span>#{String(t.id).slice(0, 8)}</span>
                {t.project_name && <span>· {t.project_name}</span>}
                <span>· {formatDateTime(t.created_at).slice(0, 10)}</span>
              </div>
            </div>
          ))}
          <Pagination current={page} total={total} pageSize={pageSize} onChange={setPage} />
        </div>
      </div>

      {/* 「更多」操作面板 */}
      <ActionSheet
        visible={!!moreSheetId}
        items={['一键催办', '复制工单号', '取消']}
        onClose={() => setMoreSheetId(null)}
        onSelected={(_, index) => {
          const t = tickets.find((x) => x.id === moreSheetId);
          if (index === 0 && t) handleUrge(t);
          else if (index === 1 && moreSheetId) copyId(moreSheetId);
          setMoreSheetId(null);
        }}
      />

      {/* 工单详情（覆盖层） */}
      <Popup visible={!!detailId} onClose={closeDetail} placement="right" showOverlay>
        <div className="ticket-detail">
          {detailLoading ? <Loading text="加载中…" /> : !detail ? <div className="tasks-empty">工单不存在</div> : (
            <>
              <Navbar title="工单详情" leftArrow onLeftClick={closeDetail} />
              <div className="ticket-detail__body">
                {detail.attachments?.[0] && (
                  <img src={detail.attachments[0].url} alt="附件" className="ticket-detail__cover" />
                )}
                <div className="ticket-detail__meta">
                  <Tag theme="primary">{TICKET_TYPE_DISPLAY_MAP[detail.ticket_type] || detail.ticket_type || '其他'}</Tag>
                  <span className="ticket-detail__id">#{detail.id}</span>
                </div>
                <h2 className="ticket-detail__title">{detail.title}</h2>
                <div className="ticket-detail__desc">
                  <h4>问题描述</h4>
                  <SafeHtml html={detail.description || '<p style="color:#999">无描述</p>'} />
                </div>

                <div className="ticket-detail__summary">
                  <h4>🤖 AI 讨论摘要</h4>
                  <p>{detail.ai_summary || 'AI 摘要生成中…'}</p>
                </div>

                {(detail.contact || detail.reporter_name || detail.assignee_name) && (
                  <div className="ticket-detail__contact">
                    <h4>联系方式</h4>
                    {detail.contact && <div>📞 {detail.contact}</div>}
                    {detail.reporter_name && <div>提交人：{detail.reporter_name}</div>}
                    {detail.assignee_name && <div>处理人：{detail.assignee_name}</div>}
                  </div>
                )}

                {detail.comments && detail.comments.length > 0 && (
                  <div className="ticket-detail__comments">
                    <h4>讨论（{detail.comments.length}）</h4>
                    {detail.comments.map((c) => (
                      <div key={c.id} className="ticket-comment">
                        <div className="ticket-comment__head">
                          <strong>{c.author_name}</strong>
                          <span>{formatTime(c.created_at)}</span>
                        </div>
                        <SafeHtml html={c.content} />
                      </div>
                    ))}
                  </div>
                )}

                <div className="ticket-detail__actions">
                  <Button size="small" theme="primary" onClick={() => handleUrge(detail)}>一键催办</Button>
                  <Button size="small" theme="default" onClick={startEdit}>修改工单</Button>
                  <Button size="small" theme="primary" onClick={() => discuss(detail)}>讨论</Button>
                  <Button size="small" theme="danger" onClick={() => handleEscalate(detail)}>升级上报</Button>
                </div>
                <div className="ticket-detail__footer">
                  创建：{formatDateTime(detail.created_at)} · 更新：{formatDateTime(detail.updated_at)}
                </div>
              </div>

              <Popup visible={editing} onClose={() => setEditing(false)} placement="bottom" showOverlay>
                <div className="ticket-edit">
                  <h4>修改工单</h4>
                  <input className="tasks-search" value={editForm.title} onChange={(e) => setEditForm((p) => ({ ...p, title: e.target.value }))} placeholder="标题" />
                  <Textarea value={editForm.description} onChange={(v) => setEditForm((p) => ({ ...p, description: String(v) }))} autosize={{ minRows: 4, maxRows: 10 }} placeholder="描述" />
                  <div className="ticket-edit__btns">
                    <Button theme="default" onClick={() => setEditing(false)}>取消</Button>
                    <Button theme="primary" onClick={saveEdit}>保存</Button>
                  </div>
                </div>
              </Popup>
            </>
          )}
        </div>
      </Popup>
    </div>
  );
}
