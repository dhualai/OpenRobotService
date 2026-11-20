// 后台「公告管理」（活动中心数据源）
//
// 列表 + 状态筛选 + 关键字搜索 + 新建/编辑（底部弹层）+ 上线/下线 + 删除。
// 前台只读：首页固定公告条取「当前生效的一条」，活动中心抽屉取「已发布公告列表」。
// 权限：frontend:admin:announcement:manage（后端 require_permission 同名校验）
// 入口：后台管理 → 其他（AdminEntries）
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Dialog, Loading, Navbar, Popup, Switch, Tag, Textarea, Toast } from 'tdesign-mobile-react';
import { Plus } from 'lucide-react';

import {
  adminCreateAnnouncement,
  adminDeleteAnnouncement,
  adminListAnnouncements,
  adminPublishAnnouncement,
  adminOfflineAnnouncement,
  adminUpdateAnnouncement,
  ANNOUNCEMENT_STATUS_LABELS,
  PERM_ANNOUNCEMENT,
  type Announcement,
  type AnnouncementStatus,
} from '@/api/announcement';
import { useAuthStore } from '@/stores/auth';
import { formatDateTimeShort } from '@/shared/utils/url';

/** 列表一次拉满：公告量级很小（运营手发），不做分页 */
const PAGE_LIMIT = 100;

/** 状态胶囊配色（草稿灰 / 已发布蓝 / 已下线暗） */
const STATUS_TAG_STYLE: Record<AnnouncementStatus, { bg: string; color: string }> = {
  draft: { bg: 'var(--secondary)', color: 'var(--muted-foreground)' },
  published: { bg: 'var(--primary-soft)', color: 'var(--sky)' },
  offline: { bg: 'var(--secondary)', color: 'var(--muted-foreground)' },
};

interface EditorState {
  visible: boolean;
  /** 编辑中的公告 id；null 表示新建 */
  id: number | null;
  title: string;
  content: string;
  isPinned: boolean;
  status: AnnouncementStatus;
}

const EMPTY_EDITOR: EditorState = {
  visible: false,
  id: null,
  title: '',
  content: '',
  isPinned: false,
  status: 'draft',
};

export default function AnnouncementManage() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_ANNOUNCEMENT));

  const [items, setItems] = useState<Announcement[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<AnnouncementStatus | ''>('');
  const [keyword, setKeyword] = useState('');
  const [appliedKeyword, setAppliedKeyword] = useState('');

  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<Announcement | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);
  /** 正在上下线的公告 id：按钮禁用防连点 */
  const [togglingId, setTogglingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminListAnnouncements({
        status: statusFilter,
        keyword: appliedKeyword,
        skip: 0,
        limit: PAGE_LIMIT,
      });
      setItems(res.items);
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '加载公告失败', theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [statusFilter, appliedKeyword]);

  useEffect(() => {
    if (allowed) load();
  }, [allowed, load]);

  const openCreate = () => setEditor({ ...EMPTY_EDITOR, visible: true });

  const openEdit = (item: Announcement) => setEditor({
    visible: true,
    id: item.id,
    title: item.title,
    content: item.content,
    isPinned: item.is_pinned,
    status: item.status,
  });

  const closeEditor = () => {
    if (saving) return;
    setEditor(EMPTY_EDITOR);
  };

  const saveEditor = async () => {
    const title = editor.title.trim();
    const content = editor.content.trim();
    if (!title) { Toast({ message: '请填写公告标题', theme: 'warning' }); return; }
    if (!content) { Toast({ message: '请填写公告正文', theme: 'warning' }); return; }

    setSaving(true);
    const payload = { title, content, is_pinned: editor.isPinned, status: editor.status };
    const saved = editor.id === null
      ? await adminCreateAnnouncement(payload)
      : await adminUpdateAnnouncement(editor.id, payload);
    setSaving(false);

    if (!saved) {
      Toast({ message: '保存失败，请稍后重试', theme: 'error' });
      return;
    }
    setEditor(EMPTY_EDITOR);
    Toast({ message: editor.id === null ? '公告已创建' : '公告已更新', theme: 'success' });
    load();
  };

  const toggleStatus = async (item: Announcement) => {
    if (togglingId !== null) return;
    setTogglingId(item.id);
    const toOffline = item.status === 'published';
    const updated = toOffline
      ? await adminOfflineAnnouncement(item.id)
      : await adminPublishAnnouncement(item.id);
    setTogglingId(null);

    if (!updated) {
      Toast({ message: toOffline ? '下线失败，请稍后重试' : '上线失败，请稍后重试', theme: 'error' });
      return;
    }
    Toast({ message: toOffline ? '已下线' : '已发布', theme: 'success' });
    load();
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setDeletingBusy(true);
    try {
      await adminDeleteAnnouncement(deleting.id);
      Toast({ message: '已删除', theme: 'success' });
      setDeleting(null);
      load();
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '删除失败', theme: 'error' });
    } finally {
      setDeletingBusy(false);
    }
  };

  if (!allowed) {
    return (
      <div className="admin-view">
        <Navbar title="公告管理" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />
        <div className="admin-no-perm">您没有权限访问此页面</div>
      </div>
    );
  }

  return (
    <div className="admin-view">
      <Navbar title="公告管理" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />

      <div className="ann-manage">
        {/* 工具条：状态筛选 + 关键字搜索 + 新建 */}
        <div className="ann-toolbar">
          <select
            className="ann-toolbar__select"
            value={statusFilter}
            aria-label="状态筛选"
            onChange={(e) => setStatusFilter(e.target.value as AnnouncementStatus | '')}
          >
            <option value="">全部状态</option>
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
            <option value="offline">已下线</option>
          </select>
          <input
            className="ann-toolbar__input"
            value={keyword}
            placeholder="搜索标题或正文"
            aria-label="搜索公告"
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') setAppliedKeyword(keyword.trim()); }}
          />
          <Button size="small" theme="primary" onClick={() => setAppliedKeyword(keyword.trim())}>搜索</Button>
          <Button size="small" onClick={openCreate} icon={<Plus size={14} strokeWidth={2} />}>新建</Button>
        </div>

        {loading ? (
          <div className="ann-loading"><Loading text="加载中..." /></div>
        ) : items.length === 0 ? (
          <div className="ann-empty">暂无公告，点右上「新建」发布第一条</div>
        ) : items.map((item) => {
          const tagStyle = STATUS_TAG_STYLE[item.status] ?? STATUS_TAG_STYLE.draft;
          return (
            <section key={item.id} className="ann-card" data-testid={`ann-card-${item.id}`}>
              <header className="ann-card__head">
                <span className="ann-card__title">{item.title}</span>
                {item.is_pinned && (
                  <Tag theme="default" style={{ background: 'var(--primary-soft)', color: 'var(--sky)', border: 'none', fontSize: 11, borderRadius: 999 }}>
                    置顶
                  </Tag>
                )}
                <Tag
                  theme="default"
                  style={{ background: tagStyle.bg, color: tagStyle.color, border: 'none', fontSize: 11, borderRadius: 999 }}
                >
                  {ANNOUNCEMENT_STATUS_LABELS[item.status] ?? item.status}
                </Tag>
              </header>
              <p className="ann-card__content">{item.content}</p>
              <div className="ann-card__meta">
                {item.published_at ? `发布于 ${formatDateTimeShort(item.published_at)}` : '未发布'}
                {item.created_by ? ` · ${item.created_by}` : ''}
              </div>
              <div className="ann-card__actions">
                <Button size="small" variant="outline" onClick={() => openEdit(item)}>编辑</Button>
                <Button
                  size="small"
                  variant="outline"
                  loading={togglingId === item.id}
                  onClick={() => toggleStatus(item)}
                >
                  {item.status === 'published' ? '下线' : '发布'}
                </Button>
                <Button size="small" theme="danger" variant="outline" onClick={() => setDeleting(item)}>删除</Button>
              </div>
            </section>
          );
        })}
      </div>

      {/* 新建 / 编辑弹层（底部） */}
      <Popup visible={editor.visible} onClose={closeEditor} placement="bottom" showOverlay>
        <div className="ann-editor">
          <h4 className="ann-editor__title">{editor.id === null ? '新建公告' : '编辑公告'}</h4>
          <label className="ann-editor__field">
            <span className="ann-editor__label">标题</span>
            <input
              className="ann-editor__input"
              value={editor.title}
              maxLength={200}
              placeholder="最多 200 字"
              onChange={(e) => setEditor((prev) => ({ ...prev, title: e.target.value }))}
            />
          </label>
          <label className="ann-editor__field">
            <span className="ann-editor__label">正文</span>
            <Textarea
              value={editor.content}
              placeholder="支持换行"
              autosize={{ minRows: 4, maxRows: 10 }}
              onChange={(value) => setEditor((prev) => ({ ...prev, content: String(value) }))}
            />
          </label>
          <label className="ann-editor__field">
            <span className="ann-editor__label">状态</span>
            <select
              className="ann-editor__input"
              value={editor.status}
              onChange={(e) => setEditor((prev) => ({ ...prev, status: e.target.value as AnnouncementStatus }))}
            >
              <option value="draft">草稿（前台不可见）</option>
              <option value="published">已发布（前台可见）</option>
              <option value="offline">已下线</option>
            </select>
          </label>
          <div className="ann-editor__switch-row">
            <span className="ann-editor__label">置顶（首页公告条优先展示）</span>
            <Switch
              value={editor.isPinned}
              onChange={(v) => setEditor((prev) => ({ ...prev, isPinned: Boolean(v) }))}
            />
          </div>
          <div className="ann-editor__btns">
            <Button block theme="default" disabled={saving} onClick={closeEditor}>取消</Button>
            <Button block theme="primary" loading={saving} disabled={saving} onClick={saveEditor}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 删除确认 */}
      <Dialog
        visible={!!deleting}
        title="删除公告"
        width={360}
        style={{ maxWidth: '88vw' }}
        cancelBtn={{ content: '取消', disabled: deletingBusy }}
        confirmBtn={{ content: '删除', loading: deletingBusy, disabled: deletingBusy }}
        onCancel={() => setDeleting(null)}
        onOverlayClick={() => setDeleting(null)}
        onConfirm={confirmDelete}
      >
        确定删除公告「{deleting?.title}」吗？删除后不可恢复。
      </Dialog>
    </div>
  );
}
