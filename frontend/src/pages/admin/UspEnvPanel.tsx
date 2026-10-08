/** 开发者模式 · 可达 USP 内网环境（一期：SSH 拉日志） */
import { useCallback, useEffect, useState } from 'react';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

export interface UspEnvRow {
  id: number;
  name: string;
  code?: string | null;
  enabled: boolean;
  notes?: string | null;
  project_id?: string | null;
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  ssh_auth_type: 'key' | 'password' | string;
  ssh_private_key_path?: string | null;
  ssh_password_set?: boolean;
  ssh_connect_timeout_s: number;
  export_script: string;
  export_workdir: string;
  log_interval_min: number;
  docker_container?: string | null;
  docker_sudo?: boolean;
  capabilities: string[];
}

type FormState = {
  name: string;
  code: string;
  enabled: boolean;
  notes: string;
  project_id: string;
  ssh_host: string;
  ssh_port: string;
  ssh_user: string;
  ssh_auth_type: 'key' | 'password';
  ssh_private_key_path: string;
  ssh_password: string;
  ssh_connect_timeout_s: string;
  export_script: string;
  export_workdir: string;
  log_interval_min: string;
  docker_container: string;
  docker_sudo: boolean;
};

const emptyForm = (): FormState => ({
  name: '',
  code: '',
  enabled: true,
  notes: '',
  project_id: '',
  ssh_host: '',
  ssh_port: '22',
  ssh_user: '',
  ssh_auth_type: 'password',
  ssh_private_key_path: '',
  ssh_password: '',
  ssh_connect_timeout_s: '8',
  export_script: '',
  export_workdir: '',
  log_interval_min: '15',
  docker_container: '',
  docker_sudo: false,
});

function rowToForm(row: UspEnvRow): FormState {
  return {
    name: row.name || '',
    code: row.code || '',
    enabled: !!row.enabled,
    notes: row.notes || '',
    project_id: row.project_id || '',
    ssh_host: row.ssh_host || '',
    ssh_port: String(row.ssh_port ?? 22),
    ssh_user: row.ssh_user || '',
    ssh_auth_type: row.ssh_auth_type === 'key' ? 'key' : 'password',
    ssh_private_key_path: row.ssh_private_key_path || '',
    ssh_password: '',
    ssh_connect_timeout_s: String(row.ssh_connect_timeout_s ?? 8),
    export_script: row.export_script || '',
    export_workdir: row.export_workdir || '',
    log_interval_min: String(row.log_interval_min ?? 15),
    docker_container: row.docker_container || '',
    docker_sudo: !!row.docker_sudo,
  };
}

export default function UspEnvPanel() {
  const [rows, setRows] = useState<UspEnvRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [showForm, setShowForm] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = unwrap<UspEnvRow[]>(await request('/dispatch-dev/usp-envs', { skipCache: true }));
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      Toast({ message: `加载失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm());
    setShowForm(true);
  };

  const openEdit = (row: UspEnvRow) => {
    setEditingId(row.id);
    setForm(rowToForm(row));
    setShowForm(true);
  };

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const save = async () => {
    if (!form.name.trim() || !form.ssh_host.trim() || !form.ssh_user.trim()) {
      Toast({ message: '请填写名称、SSH 主机与用户', theme: 'warning' });
      return;
    }
    if (!form.export_script.trim() || !form.export_workdir.trim()) {
      Toast({ message: '请填写 export_logs 脚本路径与可写目录', theme: 'warning' });
      return;
    }
    if (form.ssh_auth_type === 'key' && !form.ssh_private_key_path.trim()) {
      Toast({ message: 'key 认证需要私钥路径', theme: 'warning' });
      return;
    }
    if (form.ssh_auth_type === 'password' && !editingId && !form.ssh_password.trim()) {
      Toast({ message: '新建时 password 认证需要填写密码', theme: 'warning' });
      return;
    }
    const payload: Record<string, unknown> = {
      name: form.name.trim(),
      code: form.code.trim() || null,
      enabled: form.enabled,
      notes: form.notes.trim() || null,
      project_id: form.project_id.trim() || null,
      ssh_host: form.ssh_host.trim(),
      ssh_port: Number(form.ssh_port) || 22,
      ssh_user: form.ssh_user.trim(),
      ssh_auth_type: form.ssh_auth_type,
      ssh_private_key_path: form.ssh_private_key_path.trim() || null,
      ssh_connect_timeout_s: Number(form.ssh_connect_timeout_s) || 8,
      export_script: form.export_script.trim(),
      export_workdir: form.export_workdir.trim(),
      log_interval_min: Number(form.log_interval_min) || 15,
      docker_container: form.docker_container.trim() || null,
      docker_sudo: form.docker_sudo,
      capabilities: ['ssh_export_logs'],
    };
    if (form.ssh_password.trim()) {
      payload.ssh_password = form.ssh_password.trim();
    }
    setSaving(true);
    try {
      if (editingId == null) {
        await request('/dispatch-dev/usp-envs', { method: 'POST', body: JSON.stringify(payload) });
        Toast({ message: '已新建', theme: 'success' });
      } else {
        await request(`/dispatch-dev/usp-envs/${editingId}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        });
        Toast({ message: '已保存', theme: 'success' });
      }
      setShowForm(false);
      await load();
    } catch (e) {
      Toast({ message: `保存失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: number) => {
    if (!window.confirm('确认删除该环境？')) return;
    try {
      await request(`/dispatch-dev/usp-envs/${id}`, { method: 'DELETE' });
      Toast({ message: '已删除', theme: 'success' });
      await load();
    } catch (e) {
      Toast({ message: `删除失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    }
  };

  const testSsh = async (id: number) => {
    try {
      Toast({ message: '正在测试 SSH…', theme: 'loading' });
      const data = unwrap<{ ok?: boolean; error?: string; stdout?: string; host?: string }>(
        await request(`/dispatch-dev/usp-envs/${id}/test-ssh`, { method: 'POST', timeout: 30000 }),
      );
      if (data?.ok) {
        Toast({ message: `SSH 通：${data.host || ''} ${data.stdout || 'ok'}`, theme: 'success' });
      } else {
        Toast({
          message: `SSH 失败：${data?.error || data?.stdout || '未知错误'}`,
          theme: 'error',
        });
      }
    } catch (e) {
      Toast({ message: `测试失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    }
  };

  return (
    <section className="dispatch-dev__card">
      <div className="dispatch-dev__head">
        <span className="dispatch-dev__title">可达内网环境</span>
        <div className="dispatch-dev__head-actions">
          <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void load()} disabled={loading}>
            刷新
          </button>
          <button type="button" className="dispatch-dev__btn" onClick={openCreate}>
            新建
          </button>
        </div>
      </div>
      <p className="dispatch-dev__hint">
        配置保存在数据库。SSH 密码只留在服务端，页面只显示是否已填写。
        SSH 连宿主机；若 USP 在 Docker 里，填写「Docker 容器名」（如 usp_app），拉日志会自动 docker exec。
        需要 sudo docker 时勾选「docker 使用 sudo」（须已配置 NOPASSWD）。
      </p>

      {showForm ? (
        <div className="usp-env-form">
          <div className="usp-env-form__grid">
            <label>显示名*<input value={form.name} onChange={(e) => setField('name', e.target.value)} /></label>
            <label>短码<input value={form.code} onChange={(e) => setField('code', e.target.value)} placeholder="可选唯一码" /></label>
            <label>项目 ID（预留）<input value={form.project_id} onChange={(e) => setField('project_id', e.target.value)} /></label>
            <label className="usp-env-form__check">
              <input type="checkbox" checked={form.enabled} onChange={(e) => setField('enabled', e.target.checked)} />
              启用（出现在讨论区选项）
            </label>
            <label className="usp-env-form__full">备注<textarea value={form.notes} onChange={(e) => setField('notes', e.target.value)} rows={2} /></label>
            <label>SSH 主机*<input value={form.ssh_host} onChange={(e) => setField('ssh_host', e.target.value)} /></label>
            <label>端口<input value={form.ssh_port} onChange={(e) => setField('ssh_port', e.target.value)} /></label>
            <label>用户*<input value={form.ssh_user} onChange={(e) => setField('ssh_user', e.target.value)} /></label>
            <label>
              认证方式
              <select
                value={form.ssh_auth_type}
                onChange={(e) => setField('ssh_auth_type', e.target.value === 'key' ? 'key' : 'password')}
              >
                <option value="password">密码</option>
                <option value="key">私钥路径</option>
              </select>
            </label>
            {form.ssh_auth_type === 'key' ? (
              <label className="usp-env-form__full">
                私钥路径*
                <input value={form.ssh_private_key_path} onChange={(e) => setField('ssh_private_key_path', e.target.value)} />
              </label>
            ) : (
              <label className="usp-env-form__full">
                密码{editingId ? '（留空不改）' : '*'}
                <input
                  type="password"
                  value={form.ssh_password}
                  onChange={(e) => setField('ssh_password', e.target.value)}
                  placeholder={editingId ? '已配置则留空保持' : ''}
                  autoComplete="new-password"
                />
              </label>
            )}
            <label>连接超时(秒)<input value={form.ssh_connect_timeout_s} onChange={(e) => setField('ssh_connect_timeout_s', e.target.value)} /></label>
            <label>日志窗(分钟)<input value={form.log_interval_min} onChange={(e) => setField('log_interval_min', e.target.value)} /></label>
            <label>
              Docker 容器名
              <input
                value={form.docker_container}
                onChange={(e) => setField('docker_container', e.target.value)}
                placeholder="如 usp_app；空=直接在宿主机跑脚本"
              />
            </label>
            <label className="usp-env-form__check">
              <input type="checkbox" checked={form.docker_sudo} onChange={(e) => setField('docker_sudo', e.target.checked)} />
              docker 使用 sudo（NOPASSWD）
            </label>
            <label className="usp-env-form__full">
              export_logs.sh 绝对路径*（容器内或宿主机）
              <input value={form.export_script} onChange={(e) => setField('export_script', e.target.value)} />
            </label>
            <label className="usp-env-form__full">
              可写目录*（容器内与宿主机同路径更省事，如 /tmp）
              <input value={form.export_workdir} onChange={(e) => setField('export_workdir', e.target.value)} />
            </label>
          </div>
          <div className="dispatch-dev__head-actions" style={{ marginTop: 12 }}>
            <button type="button" className="dispatch-dev__btn" disabled={saving} onClick={() => void save()}>
              {saving ? '保存中…' : '保存'}
            </button>
            <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" disabled={saving} onClick={() => setShowForm(false)}>
              取消
            </button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="dispatch-dev__empty"><Loading text="加载环境…" /></div>
      ) : rows.length === 0 ? (
        <div className="dispatch-dev__empty-row">还没有环境，先新建一条可达的 USP SSH 配置</div>
      ) : (
        <ul className="usp-env-list">
          {rows.map((r) => (
            <li key={r.id} className="usp-env-list__item">
              <div className="usp-env-list__main">
                <strong>{r.name}</strong>
                <span className="usp-env-list__meta">
                  {r.enabled ? '启用' : '停用'} · {r.ssh_user}@{r.ssh_host}:{r.ssh_port}
                  {r.docker_container ? ` · docker:${r.docker_container}${r.docker_sudo ? '(sudo)' : ''}` : ''}
                  {r.project_id ? ` · 项目 ${r.project_id}` : ''}
                  {r.ssh_password_set || r.ssh_auth_type === 'key' ? '' : ' · 缺凭据'}
                </span>
                {r.notes ? <span className="usp-env-list__notes">{r.notes}</span> : null}
              </div>
              <div className="dispatch-dev__head-actions">
                <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void testSsh(r.id)}>测 SSH</button>
                <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => openEdit(r)}>编辑</button>
                <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void remove(r.id)}>删除</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
