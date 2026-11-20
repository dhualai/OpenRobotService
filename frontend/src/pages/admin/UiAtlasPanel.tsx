/** 界面图鉴：标准截图 + AI 标难懂点 + 人工答题 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { createRequest, getToken } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

export interface AtlasRegion {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  question: string;
  answer?: string | null;
  status: 'pending' | 'answered' | 'skipped';
  /** 展开说明的左上角位置（归一化），可拖拽 */
  label_x?: number | null;
  label_y?: number | null;
}

export interface AtlasCard {
  id: number;
  product: string;
  iface_name: string;
  image_url: string;
  image_stored?: string;
  storage?: 'local' | 'data' | 'url';
  page_caption?: string | null;
  source: string;
  kb_path?: string | null;
  status: string;
  regions: AtlasRegion[];
}

const USP_IFACES = ['监控', '任务', '编排', '设备', '库位', '地图', '首页'];

const STATUS_LABEL: Record<string, string> = {
  draft: '草稿',
  pending_answers: '待答题',
  published: '已发布',
};

/** 带 Bearer 拉图（本地图鉴接口需要登录） */
function AtlasImage({ src, alt }: { src: string; alt: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    if (!src) {
      setBlobUrl(null);
      return;
    }
    if (src.startsWith('data:') || src.startsWith('blob:') || src.startsWith('http')) {
      setBlobUrl(src);
      return;
    }
    const run = async () => {
      try {
        const token = getToken();
        const res = await fetch(src.startsWith('/') ? src : `${API_CONFIG.ADMIN.BASE_URL}${src}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) throw new Error(`图加载失败 ${res.status}`);
        const blob = await res.blob();
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        revoked = url;
        setBlobUrl(url);
      } catch {
        if (!cancelled) setBlobUrl(null);
      }
    };
    void run();
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [src]);
  if (!blobUrl) {
    return <div className="dispatch-dev__empty-row">图片加载中…</div>;
  }
  return <img src={blobUrl} alt={alt} draggable={false} />;
}

export default function UiAtlasPanel() {
  const [cards, setCards] = useState<AtlasCard[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [saving, setSaving] = useState(false);
  const [product, setProduct] = useState('调度USP');
  const [iface, setIface] = useState('监控');
  const [activeRegionId, setActiveRegionId] = useState<string | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [labelsExpanded, setLabelsExpanded] = useState(false);
  const [openTagId, setOpenTagId] = useState<string | null>(null);
  const [vlmOutput, setVlmOutput] = useState<{
    at: string;
    kind: 'scan' | 'explain';
    page_caption?: string | null;
    lines: string[];
    meta?: string;
  } | null>(null);
  const [comparing, setComparing] = useState(false);
  const [compareResult, setCompareResult] = useState<{
    without_atlas: { ok: boolean; text: string; ms: number; label: string };
    with_atlas: { ok: boolean; text: string; ms: number; label: string };
    atlas_block: string;
  } | null>(null);
  const [dialogContext, setDialogContext] = useState('用户：监控页这个小黄标是什么意思？');
  const drawStart = useRef<{ x: number; y: number } | null>(null);
  const dragRef = useRef<{
    mode: 'move' | 'resize-se' | 'label';
    id: string;
    startX: number;
    startY: number;
    orig: AtlasRegion;
    moved?: boolean;
  } | null>(null);
  const imgWrapRef = useRef<HTMLDivElement | null>(null);
  const regionsLive = useRef<AtlasRegion[] | null>(null);

  const selected = cards.find((c) => c.id === selectedId) || null;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = unwrap<AtlasCard[]>(await request('/dispatch-dev/ui-atlas/cards', { skipCache: true }));
      setCards(Array.isArray(list) ? list : []);
      if (list?.length && selectedId == null) setSelectedId(list[0].id);
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '加载图鉴失败', theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshOne = async (id: number) => {
    const card = unwrap<AtlasCard>(await request(`/dispatch-dev/ui-atlas/cards/${id}`, { skipCache: true }));
    setCards((prev) => {
      const i = prev.findIndex((c) => c.id === id);
      if (i < 0) return [card, ...prev];
      const next = [...prev];
      next[i] = card;
      return next;
    });
    setSelectedId(id);
    const pending = (card.regions || []).find((r) => r.status === 'pending');
    setActiveRegionId(pending?.id || card.regions?.[0]?.id || null);
  };

  const onPickFile = async (file: File | null, autoScan = false) => {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      Toast({ message: '请选择图片文件', theme: 'error' });
      return;
    }
    if (file.size > 12 * 1024 * 1024) {
      Toast({ message: '图片请小于 12MB', theme: 'error' });
      return;
    }
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('product', product);
      fd.append('iface_name', iface);
      const card = unwrap<AtlasCard>(
        await request('/dispatch-dev/ui-atlas/cards/upload', {
          method: 'POST',
          body: fd,
          timeout: 60000,
        }),
      );
      setCards((prev) => [card, ...prev]);
      setSelectedId(card.id);
      setActiveRegionId(null);
      setVlmOutput(null);
      if (autoScan) {
        Toast({ message: '已上传，正在让 VLM 试读…', theme: 'success' });
        await scanCard(card, []);
      } else {
        Toast({
          message: '已存本地。要看 VLM 输出：点「AI 扫难懂点」，或用手动画框',
          theme: 'success',
        });
      }
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '建卡失败', theme: 'error' });
    }
  };

  const scanCard = async (card: AtlasCard, clientRegions: AtlasRegion[]) => {
    setScanning(true);
    try {
      const next = unwrap<AtlasCard & { _scan_meta?: { kept: number; added: number; answered: number; pending: number } }>(
        await request(`/dispatch-dev/ui-atlas/cards/${card.id}/scan`, {
          method: 'POST',
          timeout: 120000,
          body: JSON.stringify({ client_regions: clientRegions || [] }),
        }),
      );
      setCards((prev) => prev.map((c) => (c.id === next.id ? next : c)));
      const pending = (next.regions || []).find((r) => r.status === 'pending');
      setActiveRegionId(pending?.id || null);
      const meta = next._scan_meta;
      const lines = (next.regions || []).map((r, i) => {
        const tag = r.status === 'answered' && r.answer ? `答案：${r.answer}` : `提问：${r.question || '（无）'}`;
        return `#${i + 1} [${r.status}] ${tag}`;
      });
      setVlmOutput({
        at: new Date().toLocaleTimeString(),
        kind: 'scan',
        page_caption: next.page_caption,
        lines: lines.length ? lines : ['（VLM 未标出难懂点，regions 为空）'],
        meta: meta
          ? `保留 ${meta.kept} · 新增 ${meta.added} · 已标注 ${meta.answered} · 待答 ${meta.pending}`
          : undefined,
      });
      Toast({
        message: meta
          ? `VLM 试读完成：新增 ${meta.added}，当前共 ${(next.regions || []).length} 框（下方可看输出）`
          : `VLM 试读完成，共 ${(next.regions || []).length} 框`,
        theme: 'success',
      });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '扫图失败（请确认 AI 服务已启动）', theme: 'error' });
    } finally {
      setScanning(false);
    }
  };

  const scan = async () => {
    if (!selected) return;
    await scanCard(selected, selected.regions || []);
  };

  const onCompareSimilar = async (file: File | null) => {
    if (!file || !selected) {
      Toast({ message: '请先选中一张已标注的标准图鉴卡', theme: 'error' });
      return;
    }
    const answered = (selected.regions || []).filter((r) => r.status === 'answered' && r.answer).length;
    if (answered === 0) {
      Toast({ message: '当前卡还没有「已确认」标注，先对标准图确认几处答案再对照', theme: 'error' });
      return;
    }
    setComparing(true);
    setCompareResult(null);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('读图失败'));
        reader.readAsDataURL(file);
      });
      const data = unwrap<{
        without_atlas: { ok: boolean; text: string; ms: number; label: string };
        with_atlas: { ok: boolean; text: string; ms: number; label: string };
        atlas_block: string;
      }>(
        await request(`/dispatch-dev/ui-atlas/cards/${selected.id}/compare-chat-vlm`, {
          method: 'POST',
          timeout: 180000,
          body: JSON.stringify({
            image_data_url: dataUrl,
            dialog_context: dialogContext,
            image_name: file.name || 'similar_shot.png',
          }),
        }),
      );
      setCompareResult(data);
      Toast({ message: '对照完成：左侧=正式对话（无图鉴），右侧=带图鉴', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '对照失败', theme: 'error' });
    } finally {
      setComparing(false);
    }
  };

  const saveRegions = async (regions: AtlasRegion[], pageCaption?: string | null) => {
    if (!selected) return;
    setSaving(true);
    try {
      const card = unwrap<AtlasCard>(
        await request(`/dispatch-dev/ui-atlas/cards/${selected.id}`, {
          method: 'PUT',
          body: JSON.stringify({
            regions,
            page_caption: pageCaption === undefined ? selected.page_caption : pageCaption,
          }),
        }),
      );
      setCards((prev) => prev.map((c) => (c.id === card.id ? card : c)));
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '保存失败', theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const updateActive = (patch: Partial<AtlasRegion>) => {
    if (!selected || !activeRegionId) return;
    const regions = (selected.regions || []).map((r) =>
      r.id === activeRegionId ? { ...r, ...patch } : r,
    );
    setCards((prev) =>
      prev.map((c) => (c.id === selected.id ? { ...c, regions } : c)),
    );
  };

  const commitActiveAnswer = async (asSkipped = false) => {
    if (!selected || !activeRegionId) return;
    const regions = (selected.regions || []).map((r) => {
      if (r.id !== activeRegionId) return r;
      if (asSkipped) return { ...r, status: 'skipped' as const, answer: null };
      const answer = (r.answer || '').trim();
      return { ...r, answer, status: answer ? ('answered' as const) : r.status };
    });
    await saveRegions(regions);
    const next = regions.find((r) => r.status === 'pending' && r.id !== activeRegionId);
    setActiveRegionId(next?.id || activeRegionId);
    Toast({ message: asSkipped ? '已跳过' : '已保存', theme: 'success' });
  };

  const removeActive = async () => {
    if (!selected || !activeRegionId) return;
    const regions = (selected.regions || []).filter((r) => r.id !== activeRegionId);
    await saveRegions(regions);
    setActiveRegionId(regions[0]?.id || null);
  };

  const deleteCard = async (id: number) => {
    try {
      await request(`/dispatch-dev/ui-atlas/cards/${id}`, { method: 'DELETE' });
      setCards((prev) => prev.filter((c) => c.id !== id));
      if (selectedId === id) setSelectedId(null);
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '删除失败', theme: 'error' });
    }
  };

  const relPos = (clientX: number, clientY: number) => {
    const el = imgWrapRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (clientY - rect.top) / rect.height)),
    };
  };

  const patchRegionsLocal = (regions: AtlasRegion[]) => {
    if (!selected) return;
    regionsLive.current = regions;
    setCards((prev) =>
      prev.map((c) => (c.id === selected.id ? { ...c, regions } : c)),
    );
  };

  const onImgPointerDown = (e: React.PointerEvent) => {
    if (!selected) return;
    const p = relPos(e.clientX, e.clientY);
    if (!p) return;
    if (drawing) {
      drawStart.current = p;
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      return;
    }
  };

  const onBoxPointerDown = (
    e: React.PointerEvent,
    r: AtlasRegion,
    mode: 'move' | 'resize-se',
  ) => {
    if (drawing || !selected) return;
    e.stopPropagation();
    e.preventDefault();
    setActiveRegionId(r.id);
    const p = relPos(e.clientX, e.clientY);
    if (!p) return;
    dragRef.current = {
      mode,
      id: r.id,
      startX: p.x,
      startY: p.y,
      orig: { ...r },
      moved: false,
    };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const defaultLabelPos = (r: AtlasRegion) => {
    if (r.label_x != null && r.label_y != null) {
      return { x: r.label_x, y: r.label_y };
    }
    // 默认放在框右侧；右侧空间不够则放左侧；贴顶则下移到框下方，避免盖住控件
    const preferRight = r.x + r.w <= 0.7;
    const x = preferRight
      ? Math.min(0.74, r.x + r.w + 0.012)
      : Math.max(0.02, r.x - 0.24);
    let y = r.y;
    if (y < 0.03) y = Math.min(0.9, r.y + r.h + 0.012);
    return { x, y };
  };

  const onLabelPointerDown = (e: React.PointerEvent, r: AtlasRegion, open: boolean) => {
    e.stopPropagation();
    if (!open) {
      setActiveRegionId(r.id);
      setOpenTagId(r.id);
      return;
    }
    if (drawing || !selected) return;
    e.preventDefault();
    setActiveRegionId(r.id);
    const p = relPos(e.clientX, e.clientY);
    if (!p) return;
    const pos = defaultLabelPos(r);
    dragRef.current = {
      mode: 'label',
      id: r.id,
      startX: p.x,
      startY: p.y,
      orig: { ...r, label_x: pos.x, label_y: pos.y },
      moved: false,
    };
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const onImgPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || !selected) return;
    const p = relPos(e.clientX, e.clientY);
    if (!p) return;
    const dx = p.x - drag.startX;
    const dy = p.y - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) > 0.008) drag.moved = true;
    const o = drag.orig;
    let next: AtlasRegion;
    if (drag.mode === 'label') {
      const label_x = Math.min(0.92, Math.max(0, (o.label_x ?? 0) + dx));
      const label_y = Math.min(0.95, Math.max(0, (o.label_y ?? 0) + dy));
      next = { ...o, label_x, label_y };
    } else if (drag.mode === 'move') {
      const x = Math.min(1 - o.w, Math.max(0, o.x + dx));
      const y = Math.min(1 - o.h, Math.max(0, o.y + dy));
      next = { ...o, x, y };
    } else {
      const w = Math.min(1 - o.x, Math.max(0.02, o.w + dx));
      const h = Math.min(1 - o.y, Math.max(0.02, o.h + dy));
      next = { ...o, w, h };
    }
    const regions = (regionsLive.current || selected.regions || []).map((r) =>
      r.id === drag.id ? next : r,
    );
    patchRegionsLocal(regions);
  };

  const onImgPointerUp = async (e: React.PointerEvent) => {
    if (dragRef.current && selected) {
      const drag = dragRef.current;
      dragRef.current = null;
      const regions = regionsLive.current || selected.regions || [];
      regionsLive.current = null;
      if (drag.mode === 'label' && !drag.moved) {
        // 单击展开态标签：收起
        setOpenTagId(null);
        return;
      }
      await saveRegions(regions);
      return;
    }
    if (!drawing || !selected || !drawStart.current) return;
    const end = relPos(e.clientX, e.clientY);
    const start = drawStart.current;
    drawStart.current = null;
    if (!end) return;
    const x = Math.min(start.x, end.x);
    const y = Math.min(start.y, end.y);
    const w = Math.abs(end.x - start.x);
    const h = Math.abs(end.y - start.y);
    if (w < 0.02 || h < 0.02) return;
    const id = `m${Date.now().toString(36)}`;
    const region: AtlasRegion = {
      id,
      x,
      y,
      w,
      h,
      question: '（手动画框）这个区域是什么？',
      answer: null,
      status: 'pending',
    };
    const regions = [...(selected.regions || []), region];
    await saveRegions(regions);
    setActiveRegionId(id);
    setOpenTagId(id);
    setDrawing(false);
    setExplaining(true);
    try {
      const explained = unwrap<{ guess: string; confidence: string }>(
        await request(`/dispatch-dev/ui-atlas/cards/${selected.id}/explain`, {
          method: 'POST',
          timeout: 90000,
          body: JSON.stringify({ x, y, w, h }),
        }),
      );
      const guess = (explained?.guess || '').trim();
      const filled = regions.map((r) =>
        r.id === id
          ? {
              ...r,
              question: '（手动画框）请核对 AI 理解是否准确，不对请直接改答案后确认',
              answer: guess || null,
              status: 'pending' as const,
            }
          : r,
      );
      await saveRegions(filled);
      setVlmOutput({
        at: new Date().toLocaleTimeString(),
        kind: 'explain',
        lines: [
          guess
            ? `手动画框理解（confidence=${explained?.confidence || '?'}）：${guess}`
            : '手动画框：VLM 未返回 guess',
        ],
      });
      Toast({
        message: guess ? 'AI 已给出理解，请看下方「VLM 输出」或右侧答案' : 'AI 未给出猜测，请手动填写',
        theme: 'success',
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'AI 解释失败';
      Toast({
        message: /not\s*found/i.test(msg)
          ? '解释接口未加载：请重启后端 (backend/main.py) 后再试手动画框'
          : `${msg}（框已保存，可手动填写）`,
        theme: 'error',
      });
    } finally {
      setExplaining(false);
    }
  };

  const active = selected?.regions?.find((r) => r.id === activeRegionId) || null;
  const regionStats = (() => {
    const rs = selected?.regions || [];
    return {
      total: rs.length,
      answered: rs.filter((r) => r.status === 'answered').length,
      pending: rs.filter((r) => r.status === 'pending').length,
      skipped: rs.filter((r) => r.status === 'skipped').length,
    };
  })();

  const calloutLabel = (r: AtlasRegion) => {
    if (r.answer) return r.answer;
    if (r.status === 'skipped') return '（已跳过）';
    return r.question || '待标注';
  };

  const isTagOpen = (id: string) => labelsExpanded || openTagId === id;

  return (
    <section className="dispatch-dev__card ui-atlas">
      <div className="dispatch-dev__head">
        <span className="dispatch-dev__title">界面图鉴</span>
        <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void load()} disabled={loading}>
          刷新
        </button>
      </div>
      <p className="dispatch-dev__hint">
        真实对话看图在「用户上传图片」链路（与扫难懂点不是同一套 prompt）。
        要看差别：先选中已确认标注的标准卡 →「对话对照：上传相似图」→ 左右对比不带/带图鉴。
      </p>

      <div className="ui-atlas__toolbar">
        <label>
          产品
          <select value={product} onChange={(e) => setProduct(e.target.value)}>
            <option value="调度USP">调度USP</option>
            <option value="摇人吧服务号">摇人吧服务号</option>
          </select>
        </label>
        <label>
          界面
          <select value={iface} onChange={(e) => setIface(e.target.value)}>
            {USP_IFACES.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <label className="ui-atlas__file">
          上传标准图
          <input
            type="file"
            accept="image/*"
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              e.target.value = '';
              void onPickFile(f, false);
            }}
          />
        </label>
        <label className={`ui-atlas__file ui-atlas__file--trial${comparing ? ' is-busy' : ''}`}>
          {comparing ? '对照中…' : '对话对照：上传相似图'}
          <input
            type="file"
            accept="image/*"
            disabled={comparing || !selected}
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              e.target.value = '';
              void onCompareSimilar(f);
            }}
          />
        </label>
      </div>
      <label className="ui-atlas__dialog-ctx">
        模拟对话背景（会进 VLM，和正式上传一致）
        <input
          value={dialogContext}
          onChange={(e) => setDialogContext(e.target.value)}
          placeholder="用户：……"
        />
      </label>

      <div className="ui-atlas__layout">
        <aside className="ui-atlas__list">
          {loading ? (
            <div className="dispatch-dev__empty-row">加载中…</div>
          ) : cards.length === 0 ? (
            <div className="dispatch-dev__empty-row">还没有图鉴卡，先上传一张监控整页</div>
          ) : (
            <ul>
              {cards.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className={`ui-atlas__item${selectedId === c.id ? ' is-active' : ''}`}
                    onClick={() => void refreshOne(c.id)}
                  >
                    <strong>{c.product} · {c.iface_name}</strong>
                    <span>{STATUS_LABEL[c.status] || c.status} · {c.regions?.length || 0} 框</span>
                  </button>
                  <button type="button" className="ui-atlas__del" onClick={() => void deleteCard(c.id)} title="删除">×</button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="ui-atlas__main">
          {!selected ? (
            <div className="dispatch-dev__empty-row">选择或上传一张标准图</div>
          ) : (
            <>
              <div className="ui-atlas__actions">
                <button type="button" className="dispatch-dev__btn" disabled={scanning} onClick={() => void scan()}>
                  {scanning ? '扫图中…' : 'AI 扫难懂点'}
                </button>
                <button
                  type="button"
                  className={`dispatch-dev__btn dispatch-dev__btn--ghost${drawing ? ' is-on' : ''}`}
                  disabled={explaining}
                  onClick={() => setDrawing((v) => !v)}
                >
                  {explaining ? 'AI 解释中…' : drawing ? '画框中（拖拽）' : '手动画框'}
                </button>
                <button
                  type="button"
                  className="dispatch-dev__btn dispatch-dev__btn--ghost"
                  disabled={saving}
                  onClick={() => void saveRegions(selected.regions || [], selected.page_caption)}
                >
                  保存
                </button>
                <button
                  type="button"
                  className={`dispatch-dev__btn dispatch-dev__btn--ghost${labelsExpanded ? ' is-on' : ''}`}
                  onClick={() => {
                    setLabelsExpanded((v) => !v);
                    if (labelsExpanded) setOpenTagId(null);
                  }}
                >
                  {labelsExpanded ? '收起全部说明' : '展开全部说明'}
                </button>
              </div>
              {selected.page_caption ? (
                <p className="dispatch-dev__hint">整页：{selected.page_caption}</p>
              ) : null}
              {compareResult ? (
                <div className="ui-atlas__compare">
                  <div className="ui-atlas__vlm-out-head">
                    真实对话看图对照
                    <span>左：当前正式看图，不改 · 右：开发者试验（逐框对照，未采用前不影响正式上传）</span>
                  </div>
                  <div className="ui-atlas__compare-grid">
                    <div className="ui-atlas__compare-col">
                      <strong>{compareResult.without_atlas.label}</strong>
                      <span className="dispatch-dev__hint">{compareResult.without_atlas.ms} ms</span>
                      <pre>{compareResult.without_atlas.text || '（空）'}</pre>
                    </div>
                    <div className="ui-atlas__compare-col is-atlas">
                      <strong>{compareResult.with_atlas.label}</strong>
                      <span className="dispatch-dev__hint">{compareResult.with_atlas.ms} ms</span>
                      <pre>{compareResult.with_atlas.text || '（空）'}</pre>
                    </div>
                  </div>
                  <details className="ui-atlas__atlas-block">
                    <summary>这张标准卡上已确认的说明（画在裁剪图上，不进第一遍）</summary>
                    <pre>{compareResult.atlas_block}</pre>
                  </details>
                </div>
              ) : null}
              {vlmOutput ? (
                <div className="ui-atlas__vlm-out">
                  <div className="ui-atlas__vlm-out-head">
                    VLM 输出
                    <span>
                      {vlmOutput.kind === 'scan' ? '扫难懂点' : '手动画框'} · {vlmOutput.at}
                    </span>
                  </div>
                  {vlmOutput.page_caption ? (
                    <p><strong>整页理解：</strong>{vlmOutput.page_caption}</p>
                  ) : null}
                  {vlmOutput.meta ? <p className="dispatch-dev__hint">{vlmOutput.meta}</p> : null}
                  <ol>
                    {vlmOutput.lines.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ol>
                </div>
              ) : (
                <p className="dispatch-dev__hint">
                  还没有 VLM 输出。上传相似图并试读，或点「AI 扫难懂点」后会显示在这里。
                </p>
              )}
              <p className="dispatch-dev__hint ui-atlas__stats">
                已记框选 <strong>{regionStats.total}</strong>
                （<span className="ui-atlas__stat--ok">已标注 {regionStats.answered}</span>
                {' / '}
                <span className="ui-atlas__stat--pending">待答 {regionStats.pending}</span>
                {regionStats.skipped ? ` / 跳过 ${regionStats.skipped}` : ''}
                ）。绿=已确认，红=待答；点序号展开可拖说明。
                {selected.storage ? ` · 存储：${selected.storage === 'local' ? '本地文件' : selected.storage}` : ''}
              </p>
              <div
                ref={imgWrapRef}
                className={`ui-atlas__canvas${drawing ? ' is-drawing' : ''}`}
                onPointerDown={onImgPointerDown}
                onPointerMove={onImgPointerMove}
                onPointerUp={(e) => void onImgPointerUp(e)}
              >
                <AtlasImage
                  src={selected.image_url}
                  alt={`${selected.product}-${selected.iface_name}`}
                />
                <svg className="ui-atlas__callouts" viewBox="0 0 100 100" preserveAspectRatio="none">
                  {(selected.regions || []).map((r) => {
                    if (!isTagOpen(r.id)) return null;
                    const pos = defaultLabelPos(r);
                    const cx = (r.x + r.w / 2) * 100;
                    const cy = (r.y + r.h / 2) * 100;
                    const lx = pos.x * 100 + 2;
                    const ly = pos.y * 100 + 2;
                    const color = r.status === 'answered' ? '#2ba471' : r.status === 'skipped' ? '#888d8f' : '#e34d59';
                    return (
                      <g key={`line-${r.id}`}>
                        <line
                          x1={cx}
                          y1={cy}
                          x2={lx}
                          y2={ly}
                          stroke={color}
                          strokeWidth="0.2"
                          strokeOpacity="0.7"
                          vectorEffect="non-scaling-stroke"
                        />
                      </g>
                    );
                  })}
                </svg>
                {(selected.regions || []).map((r, i) => {
                  const open = isTagOpen(r.id);
                  const pos = defaultLabelPos(r);
                  return (
                    <button
                      key={`tag-${r.id}`}
                      type="button"
                      className={[
                        'ui-atlas__tag',
                        `status-${r.status}`,
                        open ? 'is-open' : 'is-collapsed',
                        activeRegionId === r.id ? 'is-active' : '',
                      ].filter(Boolean).join(' ')}
                      style={
                        open
                          ? { left: `${pos.x * 100}%`, top: `${pos.y * 100}%` }
                          : {
                              left: `${(r.x + r.w) * 100}%`,
                              top: `${r.y * 100}%`,
                            }
                      }
                      onPointerDown={(e) => onLabelPointerDown(e, r, open)}
                      onPointerMove={onImgPointerMove}
                      onPointerUp={(e) => void onImgPointerUp(e)}
                      title={open ? '拖动可移开说明；单击收起' : `打开说明 #${i + 1}`}
                    >
                      {open ? (
                        <>
                          <span className="ui-atlas__tag-idx">{i + 1}</span>
                          <span className="ui-atlas__tag-text">{calloutLabel(r)}</span>
                        </>
                      ) : (
                        i + 1
                      )}
                    </button>
                  );
                })}
                {(selected.regions || []).map((r) => (
                  <div
                    key={r.id}
                    role="button"
                    tabIndex={0}
                    className={`ui-atlas__box status-${r.status}${activeRegionId === r.id ? ' is-active' : ''}`}
                    style={{
                      left: `${r.x * 100}%`,
                      top: `${r.y * 100}%`,
                      width: `${r.w * 100}%`,
                      height: `${r.h * 100}%`,
                    }}
                    onPointerDown={(e) => onBoxPointerDown(e, r, 'move')}
                    onPointerMove={onImgPointerMove}
                    onPointerUp={(e) => void onImgPointerUp(e)}
                    onClick={(e) => {
                      e.stopPropagation();
                      setActiveRegionId(r.id);
                    }}
                    title={`${calloutLabel(r)}（拖动可改位置）`}
                  >
                    {activeRegionId === r.id ? (
                      <span
                        className="ui-atlas__resize"
                        onPointerDown={(e) => onBoxPointerDown(e, r, 'resize-se')}
                        title="拖动缩放"
                      />
                    ) : null}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        <aside className="ui-atlas__side">
          {!active ? (
            <div className="dispatch-dev__empty-row">点图上红框，或先扫图 / 手动画框</div>
          ) : (
            <>
              <div className="ui-atlas__side-head">难懂点</div>
              <label className="ui-atlas__field">
                AI 提问
                <textarea
                  rows={3}
                  value={active.question}
                  onChange={(e) => updateActive({ question: e.target.value })}
                />
              </label>
              <label className="ui-atlas__field">
                你的答案
                <textarea
                  rows={4}
                  placeholder="只写这个控件是什么，例如：车卡底部的百分比是该车当前电量。不要抄标准图上的具体数字。"
                  value={active.answer || ''}
                  onChange={(e) => updateActive({ answer: e.target.value })}
                />
              </label>
              <div className="dispatch-dev__review-btns">
                <button type="button" className="dispatch-dev__btn" disabled={saving} onClick={() => void commitActiveAnswer(false)}>
                  确认答案
                </button>
                <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" disabled={saving} onClick={() => void commitActiveAnswer(true)}>
                  误框 / 跳过
                </button>
                <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" disabled={saving} onClick={() => void removeActive()}>
                  删除框
                </button>
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
