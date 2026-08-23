// 权限管理 —— 权限项 CRUD + 启用开关 + 实时搜索，基于接口 /api/admin/permissions。
// 样式参考 macaron permissions 页：大号新建按钮 + 卡片搜索框 +
// surface-card 权限行卡（名称/编码/作用域 + 开关 + 删除）+ 弹层表单。
import { useState, useEffect, useCallback } from 'react';
import { Toast, Loading, Dialog, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { MacPlus, MacSearch } from '@/shared/components/macaronIcons';
import { MacSwitch } from '@/shared/components/macaronBits';

interface Permission { id: string; code: string; name: string; resource_type: string; action: string; enabled: boolean; }

const EMPTY_FORM = { name: '', resource_type: '', action: '', code: '' };

export default function PermissionManage() {
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading] = useState(true);

  // 搜索关键字
  const [keyword, setKeyword] = useState('');

  // 新建/编辑权限
  const [dialogVisible, setDialogVisible] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<{ code: string; name: string; resource_type: string; action: string }>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formOriginal, setFormOriginal] = useState<{ code: string; name: string; resource_type: string; action: string }>(EMPTY_FORM);

  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const fetchPermissions = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<Permission[]>('/permissions/');
      setPermissions(normalizeList<Permission>(data));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { fetchPermissions(); }, [fetchPermissions]);

  const togglePermission = async (perm: Permission) => {
    try {
      await request(`/permissions/${perm.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !perm.enabled }) });
      setPermissions((prev) => prev.map((p) => p.id === perm.id ? { ...p, enabled: !p.enabled } : p));
    } catch (e) { Toast({ message: String(e), theme: 'error' }); }
  };

  const openCreate = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setFormOriginal(EMPTY_FORM);
    setDialogVisible(true);
  };

  const openEdit = (perm: Permission) => {
    setEditingId(perm.id);
    const nextForm = { code: perm.code, name: perm.name, resource_type: perm.resource_type, action: perm.action };
    setForm(nextForm);
    setFormOriginal(nextForm);
    setDialogVisible(true);
  };

  // 创建/更新权限
  const handleSave = async () => {
    if (!form.name.trim()) { Toast({ message: '请输入权限名称', theme: 'warning' }); return; }
    if (!form.code.trim()) { Toast({ message: '请输入权限编码', theme: 'warning' }); return; }
    if (!form.resource_type.trim()) { Toast({ message: '请输入资源类型', theme: 'warning' }); return; }
    setSubmitting(true);
    try {
      if (editingId) {
        const changedFields: Record<string, string> = {};
        (Object.keys(form) as (keyof typeof form)[]).forEach((key) => {
          if (form[key] !== formOriginal[key]) {
            changedFields[key] = form[key];
          }
        });
        if (Object.keys(changedFields).length === 0) {
          Toast({ message: '未检测到修改', theme: 'warning' });
          setDialogVisible(false);
          return;
        }
        await request(`/permissions/${editingId}`, { method: 'PUT', body: JSON.stringify(changedFields) });
        Toast({ message: '权限已更新', theme: 'success' });
      } else {
        await request('/permissions/', { method: 'POST', body: JSON.stringify(form) });
        Toast({ message: '权限已创建', theme: 'success' });
      }
      setDialogVisible(false);
      setForm(EMPTY_FORM);
      setEditingId(null);
      fetchPermissions();
    } catch (err) {
      Toast({ message: `${editingId ? '更新' : '创建'}失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally { setSubmitting(false); }
  };

  // 删除权限
  const handleDelete = (perm: Permission) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除权限「${perm.name}」吗？`,
      onConfirm: async () => {
        try {
          await request(`/permissions/${perm.id}`, { method: 'DELETE' });
          Toast({ message: '已删除', theme: 'success' });
          fetchPermissions();
        } catch (err) {
          Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  if (loading) return <Loading text="加载权限列表..." />;

  // 模糊搜索：按 name 或 code
  const lowerKey = keyword.trim().toLowerCase();
  const filtered = lowerKey
    ? permissions.filter((p) =>
        p.name.toLowerCase().includes(lowerKey) || (p.code || '').toLowerCase().includes(lowerKey))
    : permissions;

  return (
    <div className="mac-page">
      <button type="button" className="mac-btn mac-btn--primary mac-btn--lg mac-btn--block" onClick={openCreate}>
        <MacPlus size={16} />
        新建权限
      </button>

      <div className="mac-search mac-search--card" style={{ marginTop: 12 }}>
        <MacSearch size={16} />
        <input
          className="mac-search__input"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索权限名称或编码"
        />
      </div>

      <div style={{ marginTop: 12 }}>
        {filtered.map((perm) => (
          <div
            key={perm.id}
            className="mac-perm-card"
            onClick={() => openEdit(perm)}
          >
            <div className="mac-perm-card__body">
              <div className="mac-perm-card__name">{perm.name}</div>
              <div className="mac-perm-card__code">{perm.code}</div>
              <div className="mac-perm-card__meta">{perm.resource_type} · {perm.action}</div>
            </div>
            <div className="mac-perm-card__actions" onClick={(e) => e.stopPropagation()}>
              <MacSwitch checked={perm.enabled} onChange={() => togglePermission(perm)} />
              <button
                type="button"
                className="mac-btn mac-btn--ghost"
                onClick={() => handleDelete(perm)}
              >
                删除
              </button>
            </div>
          </div>
        ))}

        {filtered.length === 0 && (
          <div className="mac-empty">暂无权限数据</div>
        )}
      </div>

      {/* 新建/编辑权限弹窗 */}
      <Popup visible={dialogVisible} onClose={() => setDialogVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">{editingId ? '编辑权限' : '新建权限'}</h4>

          <div className="mac-field">
            <span className="mac-field__label">权限编码</span>
            <div className="mac-field__content">
              <input
                className="mac-input"
                value={form.code}
                onChange={(e) => setForm((p) => ({ ...p, code: e.target.value }))}
                placeholder="如 backend:permission:base:read"
              />
            </div>
          </div>

          <div className="mac-field">
            <span className="mac-field__label">权限名称</span>
            <div className="mac-field__content">
              <input
                className="mac-input"
                value={form.name}
                onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
                placeholder="如 基础权限"
              />
            </div>
          </div>

          <div className="mac-field">
            <span className="mac-field__label">资源类型</span>
            <div className="mac-field__content">
              <input
                className="mac-input"
                value={form.resource_type}
                onChange={(e) => setForm((p) => ({ ...p, resource_type: e.target.value }))}
                placeholder="如 backend"
              />
            </div>
          </div>

          <div className="mac-field" style={{ borderBottom: 'none' }}>
            <span className="mac-field__label">操作</span>
            <div className="mac-field__content">
              <input
                className="mac-input"
                value={form.action}
                onChange={(e) => setForm((p) => ({ ...p, action: e.target.value }))}
                placeholder="如 read, write"
              />
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
            <button type="button" className="mac-btn mac-btn--outline mac-btn--block" onClick={() => setDialogVisible(false)}>
              取消
            </button>
            <button type="button" className="mac-btn mac-btn--primary mac-btn--block" disabled={submitting} onClick={handleSave}>
              {submitting ? '保存中...' : (editingId ? '保存' : '创建')}
            </button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
