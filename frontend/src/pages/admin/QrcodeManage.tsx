// 二维码管理 —— 列表、筛选、批量创建、状态流转、图片预览
// 入口：后台管理 → 其他（AdminEntries）→ 二维码管理
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Navbar, Popup, Toast } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';
import {
  fetchQrcodes, fetchAllQrcodes, fetchQrcodeStats, createQrcode, batchCreateQrcodes,
  generateQrcodeTicket, batchGenerateTickets, updateQrcode,
  qrcodeTransition, deleteQrcode, QRCODE_STATUS_LABELS,
  type QrcodeItem, type QrcodeStats, type QrcodeType, type QrcodeStatus, type QrcodeDateFilters,
} from '@/api/qrcode';
import { MacChevronRight, MacPlus, MacDownload, MacCheck, MacTrash2, MacRefreshCw, MacTags } from '@/shared/components/macaronIcons';
import QrcodeDownloadPopup from './components/QrcodeDownloadPopup';

type FilterStatus = '' | QrcodeStatus;
type FilterType = '' | QrcodeType;

const PAGE_SIZE = 20;
const TICKET_IMAGE_BASE = 'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=';

const STATUS_ORDER: QrcodeStatus[] = ['init', 'entering', 'published', 'deprecated'];
const TYPE_LABEL: Record<QrcodeType, string> = { temporary: '临时', permanent: '永久' };

/** 状态流转：允许谁从哪来 */
const TRANSITIONS: Record<QrcodeStatus, Array<{ action: string; target: QrcodeStatus; label: string; needTicket?: boolean }>> = {
  init:       [{ action: 'generate',  target: 'entering',   label: '生成 ticket' }],
  entering:   [
               { action: 'publish',    target: 'published',  label: '发布' },
               { action: 'regenerate', target: 'entering',   label: '重新生成', needTicket: true },
              ],
  published:  [{ action: 'deprecate', target: 'deprecated', label: '弃用' }],
  deprecated: [],
};

export default function QrcodeManage() {
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<QrcodeItem[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<QrcodeStats | null>(null);
  const [skip, setSkip] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const loadSequence = useRef(0);

  const [filterStatus, setFilterStatus] = useState<FilterStatus>('');
  const [filterType, setFilterType] = useState<FilterType>('');
  const [keyword, setKeyword] = useState('');
  const [showDateFilter, setShowDateFilter] = useState(false);
  const [dateField, setDateField] = useState<NonNullable<QrcodeDateFilters['date_field']>>('created_at');
  const [dateStart, setDateStart] = useState('');
  const [dateEnd, setDateEnd] = useState('');
  const [dateFilters, setDateFilters] = useState<QrcodeDateFilters>({});
  const filters = useMemo(() => ({
    status: filterStatus || undefined,
    qrcode_type: filterType || undefined,
    keyword: keyword.trim() || undefined,
    ...dateFilters,
  }), [filterStatus, filterType, keyword, dateFilters]);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [popup, setPopup] = useState<null | 'batch-create' | 'batch-generate' | 'batch-download' | { qr: QrcodeItem }>(null);

  // ── 加载 ──
  const loadList = useCallback(async () => {
    const sequence = ++loadSequence.current;
    setLoading(true);
    try {
      const [listRes, statsRes] = await Promise.all([
        showAll ? fetchAllQrcodes(filters, () => sequence === loadSequence.current)
          : fetchQrcodes({ ...filters, skip, limit: PAGE_SIZE }),
        fetchQrcodeStats(),
      ]);
      if (sequence !== loadSequence.current) return;
      setItems(listRes.items);
      setTotal(listRes.total);
      setStats(statsRes);
    } catch (e: any) {
      if (sequence !== loadSequence.current) return;
      setItems([]);
      setTotal(0);
      Toast({ message: e?.message || '加载失败' });
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }, [filters, skip, showAll]);

  useEffect(() => {
    loadList();
    return () => { loadSequence.current += 1; };
  }, [loadList]);

  const showToast = (msg: string) => {
    Toast({ message: msg });
  };

  // ── 操作 ──
  const handleGenerate = async (id: number) => {
    try {
      setLoading(true);
      const q = await generateQrcodeTicket(id);
      setItems((prev) => prev.map((x) => (x.id === q.id ? q : x)));
      showToast('ticket 已生成');
    } catch (e: any) { showToast(e?.message || '生成失败'); }
    finally { setLoading(false); }
  };

  const handleTransition = async (id: number, action: 'confirm' | 'publish' | 'deprecate') => {
    try {
      setLoading(true);
      const q = await qrcodeTransition(id, action);
      setItems((prev) => prev.map((x) => (x.id === q.id ? q : x)));
      // 结果按后端返回的状态回显：标签本身就带「已」，不要再拼一个
      showToast(QRCODE_STATUS_LABELS[q.status as QrcodeStatus]?.label || `已${action}`);
    } catch (e: any) { showToast(e?.message || '操作失败'); }
    finally { setLoading(false); }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('确认删除？仅 init / deprecated 状态可删除')) return;
    try {
      setLoading(true);
      await deleteQrcode(id);
      setItems((prev) => prev.filter((x) => x.id !== id));
      showToast('已删除');
    } catch (e: any) { showToast(e?.message || '删除失败'); }
    finally { setLoading(false); }
  };

  const handleToggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const allVisibleSelected = items.length > 0 && items.every((item) => selected.has(item.id));
  const toggleSelectAll = () => {
    setSelected((previous) => {
      const next = new Set(previous);
      items.forEach((item) => {
        if (allVisibleSelected) next.delete(item.id);
        else next.add(item.id);
      });
      return next;
    });
  };

  const applyDateFilter = () => {
    if (dateStart && dateEnd && dateStart > dateEnd) {
      showToast('开始日期不能晚于结束日期');
      return;
    }
    setDateFilters(dateStart || dateEnd ? {
      date_field: dateField, date_start: dateStart || undefined, date_end: dateEnd || undefined,
    } : {});
    setSkip(0);
  };

  const clearDateFilter = () => {
    setDateStart('');
    setDateEnd('');
    setDateField('created_at');
    setDateFilters({});
    setSkip(0);
  };

  const selectedInitIds = useMemo(
    () => items.filter((x) => selected.has(x.id) && (x.status === 'init' || x.status === 'entering')).map((x) => x.id),
    [items, selected],
  );

  // ── 批量创建弹窗 ──
  const BatchCreatePopup = () => {
    const [count, setCount] = useState<number>(10);
    const [namePrefix, setNamePrefix] = useState('');
    const [qtype, setQtype] = useState<QrcodeType>('permanent');
    const [redirectUrl, setRedirectUrl] = useState('');
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState<null | { batchId: string; created: number }>(null);

    const submit = async () => {
      if (!count || count < 1 || count > 500) { showToast('数量 1~500'); return; }
      setBusy(true);
      try {
        const res = await batchCreateQrcodes({
          count, name_prefix: namePrefix,
          qrcode_type: qtype, redirect_url: redirectUrl || undefined,
        });
        setResult({ batchId: res.batch_id, created: res.created_count });
      } catch (e: any) { showToast(e?.message || '创建失败'); }
      finally { setBusy(false); }
    };

    return (
      <Popup visible={popup === 'batch-create'} onVisibleChange={() => setPopup(null)} placement="bottom">
        <div className="qr-popup">
          <h3>批量创建二维码（仅落库）</h3>
          <p className="qr-popup-hint">创建后为 init 状态，批量生成 ticket 后即可扫码；扫码进入录入信息页登记项目信息</p>

          <label className="qr-field">创建数量</label>
          <input
            className="qr-input"
            type="number"
            min={1}
            max={500}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
          />

          <div className="qr-row">
            <div className="qr-field-group">
              <label className="qr-field">名称前缀</label>
              <input className="qr-input" value={namePrefix} onChange={(e) => setNamePrefix(e.target.value)} placeholder="如 test → test-1, test-2…" />
            </div>
            <div className="qr-field-group">
              <label className="qr-field">类型</label>
              <select className="qr-input" value={qtype} onChange={(e) => setQtype(e.target.value as QrcodeType)}>
                <option value="permanent">永久（上限 10 万）</option>
                <option value="temporary">临时（30 天）</option>
              </select>
            </div>
          </div>

          {/* 留空 = 存 NULL，跳转由扫码链路算：DB 有记录且没配 redirect_url →
              /app/admin/info-entry/{id}（见 wechat.py::_send_scan_redirect_card）。
              前端不预填域名，也不写死地址：带 id 的 path 才扛得住微信 OAuth 回跳丢 query */}
          <label className="qr-field" style={{ marginTop: 12 }}>扫码跳转 URL（选填，留空则默认跳转到录入信息页面）</label>
          <input className="qr-input" value={redirectUrl} onChange={(e) => setRedirectUrl(e.target.value)} placeholder="留空默认：录入信息页" />

          {result && (
            <div className="qr-batch-result">
              ✅ 创建 {result.created} 条
              <div className="qr-batch-id">批次：{result.batchId}</div>
            </div>
          )}

          <div className="qr-popup-actions">
            <button className="qr-btn qr-btn--ghost" onClick={() => setPopup(null)}>关闭</button>
            <button className="qr-btn qr-btn--primary" onClick={submit} disabled={busy}>{busy ? '创建中…' : '批量创建'}</button>
          </div>
        </div>
      </Popup>
    );
  };

  // ── 批量生成弹窗 ──
  const BatchGeneratePopup = () => {
    const [useSelected, setUseSelected] = useState(true);
    const [batchIdInput, setBatchIdInput] = useState('');
    const [busy, setBusy] = useState(false);
    const [progress, setProgress] = useState<null | { total: number; done: number; ok: number; fail: number }>(null);

    const submit = async () => {
      setBusy(true);
      setProgress(null);
      try {
        const res = await batchGenerateTickets({
          qid_list: useSelected ? selectedInitIds : undefined,
          batch_id: !useSelected ? batchIdInput : undefined,
          only_init: true,
        });
        setProgress({ total: res.total, done: res.success.length + res.failed.length, ok: res.success.length, fail: res.failed.length });
        await loadList();
      } catch (e: any) { showToast(e?.message || '批量生成失败'); }
      finally { setBusy(false); }
    };

    return (
      <Popup visible={popup === 'batch-generate'} onVisibleChange={() => setPopup(null)} placement="bottom">
        <div className="qr-popup">
          <h3>批量生成 ticket</h3>
          <p className="qr-popup-hint">循环调微信接口，每次间隔 0.5 秒避免限流；永久码检查 10 万上限</p>

          <label className="qr-field">
            <input type="checkbox" checked={useSelected} onChange={(e) => setUseSelected(e.target.checked)} />
            仅处理已选 {selectedInitIds.length} 条（init / entering 状态）
          </label>
          <label className="qr-field">
            <input type="checkbox" checked={!useSelected} onChange={(e) => setUseSelected(!e.target.checked)} />
            按批次 ID 筛选
          </label>
          {!useSelected && (
            <input className="qr-input" value={batchIdInput} onChange={(e) => setBatchIdInput(e.target.value)} placeholder="batch_20260929_103015_abc123" />
          )}

          {progress && (
            <div className="qr-batch-result">
              ✅ 成功 {progress.ok} / 共 {progress.total}，❌ 失败 {progress.fail}
            </div>
          )}

          <div className="qr-popup-actions">
            <button className="qr-btn qr-btn--ghost" onClick={() => setPopup(null)}>关闭</button>
            <button className="qr-btn qr-btn--primary" onClick={submit} disabled={busy || (!useSelected && !batchIdInput)}>
              {busy ? '生成中…' : '批量生成'}
            </button>
          </div>
        </div>
      </Popup>
    );
  };

  // ── 图片预览 ──
  const ImagePreview = () => {
    if (!popup || typeof popup !== 'object' || !('qr' in popup)) return null;
    const qr = popup.qr;
    return (
      <Popup visible onVisibleChange={() => setPopup(null)} placement="center" destroyOnClose>
        <div className="qr-preview">
          <h4>{qr.name}</h4>
          <div className="qr-preview-scene">scene_str = <code>{qr.scene_str}</code></div>
          {/* 录入信息行：项目名就是码记录名（见上方 h4），这里列出登记的项目字段 */}
          {qr.project_code && <div className="qr-preview-scene">项目编号：{qr.project_code}</div>}
          {qr.project_location && <div className="qr-preview-scene">项目地点：{qr.project_location}</div>}
          {qr.customer_name && <div className="qr-preview-scene">客户名称：{qr.customer_name}</div>}
          {qr.vehicle_model && <div className="qr-preview-scene">车型：{qr.vehicle_model}</div>}
          {qr.ticket ? (
            <img
              src={`${TICKET_IMAGE_BASE}${encodeURIComponent(qr.ticket)}`}
              alt={qr.scene_str}
              className="qr-preview-img"
            />
          ) : (
            <div className="qr-preview-empty">未生成 ticket</div>
          )}
          {/* 录入信息行直接回录入页修改（项目id=行 id 只读展示，其余字段可改） */}
          {qr.project_code && (
            <button
              className="qr-btn qr-btn--secondary"
              onClick={() => navigate(`/admin/info-entry/${qr.id}`)}
            >
              编辑信息
            </button>
          )}
          <button className="qr-btn qr-btn--ghost" onClick={() => setPopup(null)}>关闭</button>
        </div>
      </Popup>
    );
  };

  // ── 状态标签 ──
  const StatusChip = ({ status }: { status: QrcodeStatus }) => {
    const s = QRCODE_STATUS_LABELS[status];
    if (!s) return null;
    return (
      <span className="qr-status-chip" style={{ background: `${s.color}22`, color: s.color, borderColor: s.color }}>
        {s.label}
      </span>
    );
  };

  // ── 渲染 ──
  return (
    <div className="qr-manage">
      <Navbar title="二维码管理" leftArrow onLeftClick={() => navigate('/admin/entries')} fixed />

      {/* 统计卡片 */}
      <div className="qr-stats">
        {STATUS_ORDER.map((st) => (
          <div key={st} className="qr-stat-card" style={{ borderTopColor: QRCODE_STATUS_LABELS[st].color }}>
            <span className="qr-stat-val">{stats?.status?.[st] ?? 0}</span>
            <span className="qr-stat-label">{QRCODE_STATUS_LABELS[st].label}</span>
          </div>
        ))}
        <div className="qr-stat-card qr-stat-card--quota">
          <span className="qr-stat-val">{stats?.permanent_quota_remaining?.toLocaleString() ?? 0}</span>
          <span className="qr-stat-label">永久码剩余配额</span>
        </div>
      </div>

      {/* 筛选栏 */}
      <div className="qr-filter">
        <select className="qr-filter-select" value={filterStatus} onChange={(e) => { setFilterStatus(e.target.value as FilterStatus); setSkip(0); }}>
          <option value="">全部状态</option>
          {STATUS_ORDER.map((s) => <option key={s} value={s}>{QRCODE_STATUS_LABELS[s].label}</option>)}
        </select>
        <select className="qr-filter-select" value={filterType} onChange={(e) => { setFilterType(e.target.value as FilterType); setSkip(0); }}>
          <option value="">全部类型</option>
          <option value="permanent">永久</option>
          <option value="temporary">临时</option>
        </select>
        <input
          className="qr-filter-input"
          placeholder="搜索名称 / ID"
          value={keyword}
          onChange={(e) => { setKeyword(e.target.value); setSkip(0); }}
        />
      </div>

      <div className="qr-toolbar">
        <button className="qr-btn qr-btn--ghost" aria-expanded={showDateFilter}
          onClick={() => setShowDateFilter((visible) => !visible)}>
          日期筛选{dateFilters.date_start || dateFilters.date_end ? '（已启用）' : ''}
        </button>
        <button className="qr-btn qr-btn--ghost" disabled={loading} onClick={() => { setShowAll((value) => !value); setSkip(0); }}>
          {showAll ? '恢复分页（每页 20 条）' : `一页显示全部（${total} 条）`}
        </button>
      </div>
      {showDateFilter && (
        <div className="qr-date-filter">
          <label className="qr-field" htmlFor="qr-date-field">日期类型（北京时间）</label>
          <select id="qr-date-field" className="qr-input" value={dateField}
            onChange={(event) => setDateField(event.target.value as typeof dateField)}>
            <option value="created_at">创建日期</option>
            <option value="ticket_created_at">ticket 生成日期</option>
          </select>
          <div className="qr-row">
            <div className="qr-field-group">
              <label className="qr-field" htmlFor="qr-date-start">开始日期（包含）</label>
              <input id="qr-date-start" className="qr-input" type="date" value={dateStart}
                onChange={(event) => setDateStart(event.target.value)} />
            </div>
            <div className="qr-field-group">
              <label className="qr-field" htmlFor="qr-date-end">结束日期（包含）</label>
              <input id="qr-date-end" className="qr-input" type="date" value={dateEnd}
                onChange={(event) => setDateEnd(event.target.value)} />
            </div>
          </div>
          <div className="qr-popup-actions">
            <button className="qr-btn qr-btn--ghost" onClick={clearDateFilter}>清除日期</button>
            <button className="qr-btn qr-btn--primary" onClick={applyDateFilter}>按日期筛选</button>
          </div>
        </div>
      )}

      {/* 批量操作 */}
      <div className="qr-toolbar">
        <button className="qr-btn qr-btn--primary" onClick={() => setPopup('batch-create')}>
          <MacPlus /> 批量创建
        </button>
        <button className="qr-btn qr-btn--secondary" disabled={selected.size === 0} onClick={() => setPopup('batch-generate')}>
          <MacRefreshCw /> 批量生成 ({selected.size})
        </button>
        <button className="qr-btn qr-btn--secondary" disabled={loading} onClick={() => setPopup('batch-download')}>
          <MacDownload /> 批量下载
        </button>
        <button className="qr-btn qr-btn--ghost" onClick={loadList}>刷新</button>
      </div>

      <div className="qr-selection-bar">
        <button className="qr-btn qr-btn--ghost" disabled={loading || items.length === 0} onClick={toggleSelectAll}>
          {allVisibleSelected ? (showAll ? '取消全选' : '取消本页全选') : (showAll ? '一键全选全部' : '一键全选本页')}
        </button>
        <span>显示 {items.length} / {total} 条 · 已选 {selected.size} 条</span>
        {selected.size > 0 && <button className="qr-action-btn" onClick={() => setSelected(new Set())}>清空已选</button>}
      </div>

      {/* 列表 */}
      {loading ? (
        <Loading text={showAll ? '正在加载全部二维码…' : '加载中…'} />
      ) : items.length === 0 ? (
        <div className="qr-empty">暂无二维码记录</div>
      ) : (
        <div className="qr-list">
          {items.map((q) => {
            const trans = TRANSITIONS[q.status as QrcodeStatus] || [];
            const checked = selected.has(q.id);
            return (
              <div key={q.id} className="qr-item">
                <div className="qr-item-top">
                  <input type="checkbox" aria-label={`选择二维码 ${q.id}`} checked={checked} onChange={() => handleToggleSelect(q.id)} />
                  <div className="qr-item-main" onClick={() => setPopup({ qr: q })}>
                    <div className="qr-item-scene">
                      <MacTags size={14} />
                      <code>{q.scene_str}</code>
                      <StatusChip status={q.status as QrcodeStatus} />
                      <span className="qr-item-type">{TYPE_LABEL[q.type as QrcodeType]}</span>
                    </div>
                    <div className="qr-item-name">{q.name || '—'}</div>
                    {q.batch_id && <div className="qr-item-batch">批次: {q.batch_id.slice(-8)}</div>}
                  </div>
                  <span className="qr-item-chev"><MacChevronRight size={16} /></span>
                </div>
                {q.ticket && <div className="qr-item-ticket">ticket 已生成 · {q.expire_seconds ? `${Math.round(q.expire_seconds / 86400)}天有效` : '永久'}</div>}
                <div className="qr-item-actions">
                  {trans.map((t) => {
                    const disabled = t.needTicket && !q.ticket;
                    return (
                      <button
                        key={t.action}
                        className="qr-action-btn"
                        disabled={disabled}
                        onClick={() => {
                          if (t.action === 'generate' || t.action === 'regenerate') handleGenerate(q.id);
                          else handleTransition(q.id, t.action as 'confirm' | 'publish' | 'deprecate');
                        }}
                      >
                        {t.action === 'generate' || t.action === 'regenerate' ? <MacRefreshCw /> :
                         t.action === 'confirm' ? <MacCheck /> :
                         t.action === 'publish' ? <MacDownload /> : null}
                        {t.label}
                      </button>
                    );
                  })}
                  {(q.status === 'init' || q.status === 'deprecated') && (
                    <button className="qr-action-btn qr-action-btn--danger" onClick={() => handleDelete(q.id)}>
                      <MacTrash2 size={14} /> 删除
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* 分页 */}
      {!showAll && total > PAGE_SIZE && (
        <div className="qr-pager">
          <button className="qr-btn qr-btn--ghost" disabled={skip === 0} onClick={() => setSkip((s) => Math.max(0, s - PAGE_SIZE))}>上一页</button>
          <span>{skip + 1}–{Math.min(skip + PAGE_SIZE, total)} / {total}</span>
          <button className="qr-btn qr-btn--ghost" disabled={skip + PAGE_SIZE >= total} onClick={() => setSkip((s) => s + PAGE_SIZE)}>下一页</button>
        </div>
      )}

      <BatchCreatePopup />
      <BatchGeneratePopup />
      {popup === 'batch-download' && (
        <QrcodeDownloadPopup
          selectedIds={[...selected]}
          pageIds={items.map((item) => item.id)}
          filteredTotal={total}
          filters={filters}
          onClose={() => setPopup(null)}
        />
      )}
      <ImagePreview />
    </div>
  );
}
