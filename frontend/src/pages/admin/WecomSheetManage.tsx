// 企业微信表格数据源管理。
//
// 为什么有这一页：以前每接一张企微智能表格就要写一个 adapter（含 30 个中文字段硬编码）
// + 一条专用路由 + 一个 Airflow DAG。现在一张表 = 一条配置：填 docid / sheet_id，
// 「测试连接」拿到真实列名，保存后「立即同步」把数据镜像进库。
//
// 第一步的「测试连接」是全页重点：列名来自表格实际返回，不是手打，
// 避免中文列名拼错导致同步静默丢数据。
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import {
  PERM_WECOM_SHEETS,
  fetchSheetSources, createSheetSource, updateSheetSource, deleteSheetSource,
  previewSheet, syncSheet, fetchSheetRecords,
  createWecomDoc, fetchWecomDocSheets,
} from '@/api/wecomSheets';
import type { WecomSheetSource, SheetPreview, MirrorRecord, WecomDocSheet } from '@/api/wecomSheets';

type FormState = {
  key: string;
  display_name: string;
  docid: string;
  sheet_id: string;
  enabled: boolean;
  sync_interval_min: string;
  notes: string;
};

const emptyForm = (): FormState => ({
  key: '',
  display_name: '',
  docid: '',
  sheet_id: '',
  enabled: true,
  sync_interval_min: '30',
  notes: '',
});

/** 企微时间戳是 UTC+0 的 ISO 串，直接显示会差 8 小时，这里转成本地时间 */
function fmtTime(v?: string | null): string {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export default function WecomSheetManage() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_WECOM_SHEETS));

  const [rows, setRows] = useState<WecomSheetSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  // 新建 / 编辑表单
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  // 测试连接结果（列名 + 样例）
  const [preview, setPreview] = useState<SheetPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // 在企微里新建智能表格（唯一能拿到 docid 的途径）
  const [showCreateDoc, setShowCreateDoc] = useState(false);
  const [cdName, setCdName] = useState('');
  const [cdAdmins, setCdAdmins] = useState('');
  const [creating, setCreating] = useState(false);
  const [createdDoc, setCreatedDoc] = useState<{ docid: string; url: string } | null>(null);

  // 镜像数据查看
  const [viewId, setViewId] = useState<number | null>(null);
  const [viewName, setViewName] = useState('');
  const [records, setRecords] = useState<MirrorRecord[]>([]);
  const [recordTotal, setRecordTotal] = useState(0);
  const [recordLoading, setRecordLoading] = useState(false);
  const [recordQ, setRecordQ] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRows(await fetchSheetSources());
    } catch (e) {
      Toast({ message: `加载失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (allowed) void load();
  }, [allowed, load]);

  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }));

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm());
    setPreview(null);
    setShowCreateDoc(false);
    setCreatedDoc(null);
    setShowForm(true);
  };

  const openEdit = (r: WecomSheetSource) => {
    setEditingId(r.id);
    setForm({
      key: r.key,
      display_name: r.display_name,
      docid: r.docid,
      sheet_id: r.sheet_id,
      enabled: r.enabled,
      sync_interval_min: String(r.sync_interval_min ?? 30),
      notes: r.notes || '',
    });
    setPreview(null);
    setShowCreateDoc(false);
    setCreatedDoc(null);
    setShowForm(true);
  };

  /** 测试连接：用表单里还没保存的 docid/sheet_id 试拉几条 */
  const testConnection = async () => {
    if (!form.docid.trim() || !form.sheet_id.trim()) {
      Toast({ message: '先填 docid 与 sheet_id', theme: 'warning' });
      return;
    }
    setPreviewing(true);
    setPreview(null);
    try {
      const data = await previewSheet(form.docid.trim(), form.sheet_id.trim(), 3);
      setPreview(data);
      if (!data.columns.length) {
        Toast({ message: '连上了，但没读到任何列（检查子表是否有数据）', theme: 'warning' });
      } else {
        Toast({ message: `连通：${data.columns.length} 列 / 共 ${data.total} 行`, theme: 'success' });
      }
    } catch (e) {
      Toast({ message: `连接失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setPreviewing(false);
    }
  };

  /** 在企微新建智能表格：docid 只返回一次，创建后立刻回填表单等用户保存 */
  const createSmartSheet = async () => {
    if (!cdName.trim()) {
      Toast({ message: '先填新表格名称', theme: 'warning' });
      return;
    }
    setCreating(true);
    try {
      const d = await createWecomDoc({
        doc_name: cdName.trim(),
        admin_users: cdAdmins.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean),
      });
      setField('docid', d.docid);
      if (!form.display_name.trim()) setField('display_name', d.doc_name);
      if (d.sheets?.length) setField('sheet_id', d.sheets[0].sheet_id);
      setCreatedDoc({ docid: d.docid, url: d.url });
      setShowCreateDoc(false);
      setCdName('');
      setCdAdmins('');
      Toast({ message: '已创建，docid 已填入表单 —— 记得点保存', theme: 'success' });
    } catch (e) {
      Toast({ message: `建表失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setCreating(false);
    }
  };

  /** 已有 docid 时拉它的子表列表，直接选 sheet_id，不用去 URL 上抠 tab 参数 */
  const pickSheets = async () => {
    if (!form.docid.trim()) {
      Toast({ message: '先填 docid', theme: 'warning' });
      return;
    }
    try {
      const sheets: WecomDocSheet[] = await fetchWecomDocSheets(form.docid.trim());
      if (!sheets.length) {
        Toast({ message: '没查到子表，请手动填 sheet_id', theme: 'warning' });
        return;
      }
      setField('sheet_id', sheets[0].sheet_id);
      Toast({ message: `已选子表 ${sheets[0].title || sheets[0].sheet_id}`, theme: 'success' });
    } catch (e) {
      Toast({ message: `查子表失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    }
  };

  const save = async () => {
    if (!form.key.trim() || !form.display_name.trim() || !form.docid.trim() || !form.sheet_id.trim()) {
      Toast({ message: 'key / 显示名 / docid / sheet_id 必填', theme: 'warning' });
      return;
    }
    setBusy(true);
    try {
      const payload = {
        display_name: form.display_name.trim(),
        docid: form.docid.trim(),
        sheet_id: form.sheet_id.trim(),
        enabled: form.enabled,
        sync_interval_min: Number(form.sync_interval_min) || 30,
        notes: form.notes.trim() || null,
      };
      if (editingId == null) {
        await createSheetSource({ ...payload, key: form.key.trim() });
        Toast({ message: '已新建', theme: 'success' });
      } else {
        await updateSheetSource(editingId, payload);
        Toast({ message: '已保存', theme: 'success' });
      }
      setShowForm(false);
      await load();
    } catch (e) {
      Toast({ message: `保存失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (r: WecomSheetSource) => {
    if (!window.confirm(`删除「${r.display_name}」？该数据源已镜像的 ${r.record_count} 行会一并清除。`)) return;
    try {
      await deleteSheetSource(r.id);
      Toast({ message: '已删除', theme: 'success' });
      await load();
    } catch (e) {
      Toast({ message: `删除失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    }
  };

  const runSync = async (r: WecomSheetSource) => {
    Toast({ message: '正在同步…', theme: 'loading' });
    try {
      const s = await syncSheet(r.id);
      if (s.reason) {
        Toast({ message: s.reason, theme: 'warning' });
      } else {
        Toast({
          message: `完成：新增 ${s.created ?? 0} / 更新 ${s.updated ?? 0} / 未变 ${s.unchanged ?? 0}（共 ${s.fetched ?? 0} 行）`,
          theme: 'success',
        });
      }
      await load();
      if (viewId === r.id) void openRecords(r);
    } catch (e) {
      Toast({ message: `同步失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    }
  };

  const openRecords = async (r: WecomSheetSource) => {
    setViewId(r.id);
    setViewName(r.display_name);
    setRecordQ('');
    setRecordLoading(true);
    try {
      const data = await fetchSheetRecords(r.id, { limit: 20 });
      setRecords(data.items);
      setRecordTotal(data.total);
    } catch (e) {
      Toast({ message: `加载镜像失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setRecordLoading(false);
    }
  };

  const searchRecords = async () => {
    if (viewId == null) return;
    setRecordLoading(true);
    try {
      const data = await fetchSheetRecords(viewId, { limit: 20, q: recordQ });
      setRecords(data.items);
      setRecordTotal(data.total);
    } catch (e) {
      Toast({ message: `查询失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setRecordLoading(false);
    }
  };

  if (!allowed) {
    return (
      <div className="admin-view">
        <Navbar title="企微表格" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />
        <div className="admin-no-perm">您没有权限访问此页面</div>
      </div>
    );
  }

  return (
    <div className="admin-view">
      <Navbar title="企微表格" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />
      <div className="wsm-wrap">
        <p className="wsm-hint">
          一张企业微信智能表格 = 一条数据源。填文档 ID 与子表 ID，先「测试连接」确认读到的列，
          再保存并同步。数据落在统一的镜像表里，按 record_id 去重、靠内容哈希判变，
          不会重复插入也不会把已改过的行当新增。
          <br />
          没有 docid？点「新建智能表格」在企微里建一张——手工建的表格拿不到 docid
          （链接里的 s3_xxx 是 URL ID，不是 docid）。
        </p>

        <div className="wsm-toolbar">
          <button type="button" className="wsm-btn wsm-btn--primary" onClick={openCreate}>新增表格</button>
          <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => void load()} disabled={loading}>刷新</button>
        </div>

        {showForm ? (
          <div className="wsm-card wsm-form">
            <div className="wsm-form__grid">
              <label className="wsm-field">
                唯一标识 key{editingId ? '（不可改）' : ' *'}
                <input
                  className="wsm-input"
                  value={form.key}
                  disabled={editingId != null}
                  onChange={(e) => setField('key', e.target.value)}
                  placeholder="如 usp_projects"
                />
              </label>
              <label className="wsm-field">
                显示名 *
                <input className="wsm-input" value={form.display_name} onChange={(e) => setField('display_name', e.target.value)} placeholder="如 USP 项目台账" />
              </label>
              <label className="wsm-field">
                文档 ID docid *
                <input
                  className="wsm-input"
                  value={form.docid}
                  onChange={(e) => setField('docid', e.target.value)}
                  placeholder="dc- 开头，由「新建智能表格」生成"
                />
                <span className="wsm-help">浏览器链接里的 s3_xxx 是 URL ID 不是 docid，填了会报 301085</span>
              </label>
              <label className="wsm-field">
                子表 ID sheet_id *
                <input className="wsm-input" value={form.sheet_id} onChange={(e) => setField('sheet_id', e.target.value)} />
              </label>
              <label className="wsm-field">
                同步间隔（分钟）
                <input className="wsm-input" value={form.sync_interval_min} onChange={(e) => setField('sync_interval_min', e.target.value)} />
              </label>
              <label className="wsm-field wsm-field--check">
                <input type="checkbox" checked={form.enabled} onChange={(e) => setField('enabled', e.target.checked)} />
                启用定时同步
              </label>
              <label className="wsm-field wsm-field--full">
                备注
                <textarea className="wsm-input" rows={2} value={form.notes} onChange={(e) => setField('notes', e.target.value)} />
              </label>
            </div>

            <div className="wsm-form__actions">
              <button type="button" className="wsm-btn wsm-btn--secondary" onClick={() => setShowCreateDoc((v) => !v)}>
                {showCreateDoc ? '收起建表' : '新建智能表格'}
              </button>
              <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => void pickSheets()}>
                拉取子表
              </button>
              <button type="button" className="wsm-btn wsm-btn--secondary" disabled={previewing} onClick={() => void testConnection()}>
                {previewing ? '连接中…' : '测试连接'}
              </button>
              <button type="button" className="wsm-btn wsm-btn--primary" disabled={busy} onClick={() => void save()}>
                {busy ? '保存中…' : '保存'}
              </button>
              <button type="button" className="wsm-btn wsm-btn--ghost" disabled={busy} onClick={() => setShowForm(false)}>取消</button>
            </div>

            {showCreateDoc ? (
              <div className="wsm-createdoc">
                <div className="wsm-form__grid">
                  <label className="wsm-field">
                    新表格名称 *
                    <input
                      className="wsm-input"
                      value={cdName}
                      onChange={(e) => setCdName(e.target.value)}
                      placeholder="如 USP 项目台账"
                    />
                  </label>
                  <label className="wsm-field">
                    文档管理员 userid（逗号分隔，可留空）
                    <input
                      className="wsm-input"
                      value={cdAdmins}
                      onChange={(e) => setCdAdmins(e.target.value)}
                      placeholder="zhangsan,lisi"
                    />
                  </label>
                </div>
                <div className="wsm-form__actions">
                  <button type="button" className="wsm-btn wsm-btn--primary" disabled={creating} onClick={() => void createSmartSheet()}>
                    {creating ? '创建中…' : '在企微创建并填入 docid'}
                  </button>
                  <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => setShowCreateDoc(false)}>取消</button>
                </div>
                <p className="wsm-hint wsm-hint--sm">
                  手工在企微里建的表格拿不到 docid，必须走这里创建。docid 企微只返回一次，
                  创建后会立刻填入上方表单——请务必保存本条数据源，否则只能重建文档。
                </p>
              </div>
            ) : null}

            {createdDoc ? (
              <div className="wsm-createdoc__result">
                已创建 <code>{createdDoc.docid}</code>
                {createdDoc.url ? (
                  <>
                    {' · '}
                    <a href={createdDoc.url} target="_blank" rel="noreferrer">在企微打开</a>
                  </>
                ) : null}
              </div>
            ) : null}

            {preview ? (
              <div className="wsm-preview">
                <div className="wsm-preview__head">
                  读到 {preview.columns.length} 列 · 表内共 {preview.total} 行
                </div>
                <div className="wsm-preview__cols">
                  {preview.columns.map((c) => (
                    <span key={c} className="wsm-chip">{c}</span>
                  ))}
                </div>
                {preview.records.length ? (
                  <div className="wsm-preview__sample">
                    <table className="wsm-table">
                      <thead>
                        <tr>{preview.columns.slice(0, 6).map((c) => <th key={c}>{c}</th>)}</tr>
                      </thead>
                      <tbody>
                        {preview.records.map((r) => (
                          <tr key={r.record_id}>
                            {preview.columns.slice(0, 6).map((c) => (
                              <td key={c}>{cell(r.values?.[c])}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="wsm-empty">没有样例行（子表可能是空的）</div>
                )}
              </div>
            ) : null}
          </div>
        ) : null}

        {loading ? (
          <div className="wsm-loading"><Loading text="加载数据源…" /></div>
        ) : rows.length === 0 ? (
          <div className="wsm-empty">还没有数据源，点「新增表格」接一张企微智能表格进来</div>
        ) : (
          <div className="wsm-list">
            {rows.map((r) => (
              <div key={r.id} className="wsm-card">
                <div className="wsm-card__head">
                  <span className="wsm-card__title">{r.display_name}</span>
                  <span className={`wsm-badge${r.enabled ? ' is-on' : ''}`}>{r.enabled ? '启用' : '停用'}</span>
                </div>
                <div className="wsm-card__meta">
                  key <code>{r.key}</code> · docid <code>{r.docid}</code> · sheet <code>{r.sheet_id}</code>
                </div>
                <div className="wsm-card__meta">
                  镜像 {r.record_count} 行 · 上次同步 {fmtTime(r.last_sync_at)}
                  {r.last_stats
                    ? ` · 新增 ${r.last_stats.created ?? 0} / 更新 ${r.last_stats.updated ?? 0} / 未变 ${r.last_stats.unchanged ?? 0}`
                    : ''}
                </div>
                {r.last_error ? <div className="wsm-error">上次失败：{r.last_error}</div> : null}

                <div className="wsm-card__actions">
                  <button type="button" className="wsm-btn wsm-btn--secondary" onClick={() => void runSync(r)}>立即同步</button>
                  <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => void openRecords(r)}>查看数据</button>
                  <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => openEdit(r)}>编辑</button>
                  <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => void remove(r)}>删除</button>
                </div>
              </div>
            ))}
          </div>
        )}

        {viewId != null ? (
          <div className="wsm-card wsm-records">
            <div className="wsm-card__head">
              <span className="wsm-card__title">镜像数据 · {viewName}</span>
              <button type="button" className="wsm-btn wsm-btn--ghost" onClick={() => setViewId(null)}>收起</button>
            </div>
            <div className="wsm-search">
              <input
                className="wsm-input"
                value={recordQ}
                onChange={(e) => setRecordQ(e.target.value)}
                placeholder="在内容里搜（如项目名）"
              />
              <button type="button" className="wsm-btn wsm-btn--secondary" onClick={() => void searchRecords()}>搜索</button>
            </div>
            {recordLoading ? (
              <div className="wsm-loading"><Loading text="加载镜像…" /></div>
            ) : records.length === 0 ? (
              <div className="wsm-empty">还没有镜像数据，先点「立即同步」</div>
            ) : (
              <>
                <div className="wsm-card__meta">共 {recordTotal} 行，显示前 {records.length} 行</div>
                <div className="wsm-records__scroll">
                  <table className="wsm-table">
                    <tbody>
                      {records.map((r) => (
                        <tr key={r.record_id}>
                          <td className="wsm-table__id">{r.record_id}</td>
                          <td>{JSON.stringify(r.values)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
