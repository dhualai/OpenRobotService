// 日报周报分析 —— 对接 POST /api/ai/analysis/report/generate（流式 SSE）
// 数据来源：ai/agents/AiDataAnalysisPlatform/report_generator.py 实时采集 MySQL 中的
// 项目/风险/工单/任务数据并调用 LLM 生成报告文本。
// 样式参考 macaron reports 页：卡片内分段切换 + surface-card 项目选择 + 淡蓝日期条。
import { memo, useState, useEffect, useCallback, useRef, type RefObject, type ClipboardEvent as ReactClipboardEvent } from 'react';
import { Loading, Toast } from 'tdesign-mobile-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import DOMPurify from 'dompurify';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import SafeHtml from '@/shared/components/SafeHtml';
import { applyTableAlign, exportReportWord, exportReportWordFromHtml, markdownToHtml } from '@/shared/utils/reportExport';
import { generateReportStream, readReportStream, type ReportPeriod } from '@/api/report';
import ProjectSelect from '@/shared/components/ProjectSelect';
import { getMyProjects, type ProjectItem } from '@/api/projects';
import { MacCalendarDays, MacRefreshCw } from '@/shared/components/macaronIcons';

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// 生成结果本地缓存：每日首次生成后写入，当天内再次查看（切换视图/日期/项目）直接读取，
// 不重复调用 LLM；仅点击「刷新」按钮才强制重新生成并覆盖缓存。
// key = projectCode(或 __all__):period:date。
const REPORT_CACHE_KEY = 'admin_daily_summary_report_cache_v1';

interface CachedReportEntry {
  generatedOnDay: string;
  streamText: string;
  /** 用户在渲染后格式上编辑的富文本 HTML（保存后预览/导出以此为准） */
  editedHtml?: string;
}

function loadReportCacheMap(): Record<string, CachedReportEntry> {
  try {
    const raw = localStorage.getItem(REPORT_CACHE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveReportCacheEntry(key: string, entry: CachedReportEntry): void {
  try {
    const map = loadReportCacheMap();
    map[key] = entry;
    localStorage.setItem(REPORT_CACHE_KEY, JSON.stringify(map));
  } catch { /* 本地存储不可用（如隐私模式）不影响本次展示 */ }
}

function reportCacheKey(p: ReportPeriod, d: string, code: string | null): string {
  return `${code || '__all__'}:${p}:${d}`;
}

// 富文本编辑工具栏：基于 document.execCommand（浏览器原生支持，零新依赖），
// 在渲染后的排版上直接改字、加粗、切标题/列表。
const RICH_TOOL_ITEMS: Array<{ cmd: string; label: string; value?: string }> = [
  { cmd: 'bold', label: 'B' },
  { cmd: 'italic', label: 'I' },
  { cmd: 'formatBlock', label: '标题', value: 'H2' },
  { cmd: 'formatBlock', label: '正文', value: 'P' },
  { cmd: 'insertUnorderedList', label: '• 列表' },
  { cmd: 'insertOrderedList', label: '1. 列表' },
];

function execRichCmd(cmd: string, value?: string): void {
  document.execCommand(cmd, false, value);
}

// 流式报告卡片（React.memo）：流式与完成后统一用 MarkdownRenderer 实时渲染，
// 从头到尾都是渲染后的样式，不会出现“先源码后渲染”的闪变。
// 生成完成后提供「编辑」（富文本所见即所得编辑）与「导出」（Word）。
const ReportStreamCard = memo(function ReportStreamCard({
  text, streaming, period, date, projectName,
  editedHtml, editing, initialHtml, exporting, editorRef,
  onStartEdit, onSaveEdit, onCancelEdit, onExport,
}: {
  text: string;
  streaming: boolean;
  period: ReportPeriod;
  date: string;
  projectName?: string | null;
  editedHtml: string | null;
  editing: boolean;
  initialHtml: string;
  exporting: boolean;
  editorRef: RefObject<HTMLDivElement | null>;
  onStartEdit: () => void;
  onSaveEdit: () => void;
  onCancelEdit: () => void;
  onExport: () => void;
}) {
  // 进入编辑时把渲染后的 HTML 灌入 contenteditable：仅在内容变化时设置一次，
  // 避免 React 重渲染覆盖用户正在编辑的光标位置（编辑器本身非受控）
  useEffect(() => {
    if (editing && editorRef.current && initialHtml !== editorRef.current.innerHTML) {
      editorRef.current.innerHTML = initialHtml;
    }
  }, [editing, initialHtml, editorRef]);

  // 表格列宽对齐：列数相同的表格统一各列宽度，视觉上列线对齐。
  // 流式期间表格每帧变化，对齐无意义且会抖动，等流结束/内容定稿/编辑切换后再对齐。
  // 编辑模式下对编辑容器（editorRef）对齐，保存后的 HTML 自带列宽，预览与导出保持一致。
  const bodyRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (streaming) return;
    const raf = requestAnimationFrame(() => {
      applyTableAlign(editing ? editorRef.current : bodyRef.current);
    });
    return () => cancelAnimationFrame(raf);
  }, [text, editedHtml, editing, streaming, editorRef]);

  // 粘贴净化：只插入纯文本，防止从 Word/网页粘贴带入样式垃圾破坏排版
  const handleRichPaste = (e: ReactClipboardEvent<HTMLDivElement>) => {
    e.preventDefault();
    const text = e.clipboardData.getData('text/plain');
    if (text) document.execCommand('insertText', false, text);
  };

  return (
    <div ref={bodyRef} className="mac-card mac-card--pad mac-report">
      {/* 头部行：编辑模式下 sticky 固定在顶部，保存/取消按钮随行置顶 */}
      <div className={`mac-report-head${editing ? ' is-editing' : ''}`}>
        <div className="mac-report-head__row">
          <span className="mac-report-head__title">
            {period === 'daily' ? '日报' : '周报'} · {date}
            {projectName ? ` · ${projectName}` : ''}
          </span>
          {streaming && <span className="mac-streaming">● 生成中</span>}
          {!streaming && !editing && (
            <span className="mac-report-actions">
              <button type="button" className="mac-report-action-btn" onClick={onStartEdit}>
                编辑
              </button>
              <button
                type="button"
                className="mac-report-action-btn"
                onClick={onExport}
                disabled={exporting}
              >
                {exporting ? '导出中…' : '导出 Word'}
              </button>
            </span>
          )}
          {!streaming && editing && (
            <span className="mac-report-actions">
              <button type="button" className="mac-report-action-btn" onClick={onCancelEdit}>
                取消
              </button>
              <button
                type="button"
                className="mac-report-action-btn mac-report-action-btn--primary"
                onClick={onSaveEdit}
              >
                保存
              </button>
            </span>
          )}
        </div>
        {editing && (
          <div className="mac-report-toolbar">
            {RICH_TOOL_ITEMS.map((t) => (
              <button
                key={`${t.cmd}-${t.value || ''}`}
                type="button"
                className={`mac-report-toolbar__btn${t.cmd === 'bold' ? ' is-bold' : ''}${t.cmd === 'italic' ? ' is-italic' : ''}`}
                // preventDefault 保留编辑区光标/选区，点击工具栏不丢失焦点
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => execRichCmd(t.cmd, t.value)}
              >
                {t.label}
              </button>
            ))}
          </div>
        )}
      </div>
      {editing ? (
        /* 富文本所见即所得编辑：渲染后的排版上直接编辑（markdown-body 排版 = 预览一致） */
        <div
          ref={editorRef}
          className="mac-report-richtext markdown-body md-compact"
          contentEditable
          suppressContentEditableWarning
          onPaste={handleRichPaste}
          data-placeholder="点击编辑报告内容…"
        />
      ) : editedHtml ? (
        /* 编辑保存后的内容：以富文本 HTML 渲染（SafeHtml 内置 DOMPurify 清洗） */
        <SafeHtml html={editedHtml} className="markdown-body md-compact" />
      ) : (
        <MarkdownRenderer content={text} compact />
      )}
    </div>
  );
});

export default function DailySummaryAgent() {
  const [period, setPeriod] = useState<ReportPeriod>('daily');
  const [date, setDate] = useState<string>(todayStr());
  const [projectCode, setProjectCode] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  // 用户关联项目：日报/周报只允许选择当前用户关联的项目（user_project_roles）
  const [myProjects, setMyProjects] = useState<ProjectItem[]>([]);
  const [myProjectsLoading, setMyProjectsLoading] = useState(true);
  const [myProjectsError, setMyProjectsError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [streamText, setStreamText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  // 编辑模式：在渲染后的排版上直接编辑（富文本所见即所得）。
  // editedHtml = 用户保存的富文本 HTML（预览/导出以此为准，刷新重新生成后清空）；
  // editInitialHtml = 进入编辑时灌入 contenteditable 的初始 HTML（markdown 渲染或上次编辑结果）
  const [editing, setEditing] = useState(false);
  const [editInitialHtml, setEditInitialHtml] = useState('');
  const [editedHtml, setEditedHtml] = useState<string | null>(null);
  const richEditorRef = useRef<HTMLDivElement | null>(null);
  // 导出：默认导出 Word（重型依赖 react-dom/server 等在导出模块内部按需动态加载，不阻塞首屏）
  const [exporting, setExporting] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  // 请求代际守卫：新请求发起时 abort 旧流并使旧请求的回调全部失效，
  // 避免生成中切换日报/周报/日期/项目或点刷新时，新旧两条 SSE 流交替写
  // streamText/loading/isStreaming 导致页面一闪一闪。
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  // 流式渲染节流：chunk 到达很快时按 120ms 合并刷新一次，
  // 避免每个 chunk 都触发 Markdown 全量重解析导致卡顿
  const flushTimerRef = useRef<number | null>(null);
  const pendingTextRef = useRef('');

  // 进入页面时滚动到最上端（AdminLayout 的滚动容器是外层 overflow:auto 的 div，非 window）
  useEffect(() => {
    let el: HTMLElement | null = rootRef.current;
    while (el) {
      const style = window.getComputedStyle(el);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
        el.scrollTop = 0;
        break;
      }
      el = el.parentElement;
    }
    window.scrollTo(0, 0);
  }, []);

  // 拉取当前用户关联的项目列表（只允许选这些项目）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await getMyProjects();
        if (cancelled) return;
        setMyProjects(list);
        setMyProjectsError(false);
      } catch {
        if (cancelled) return;
        setMyProjectsError(true);
      } finally {
        if (!cancelled) setMyProjectsLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const fetchReport = useCallback(async (p: ReportPeriod, d: string, force: boolean) => {
    // 每次取报告（含点刷新重新生成）都退出编辑模式，避免编辑框旧内容与新报告串扰
    setEditing(false);
    // 项目必选：未选择项目时不发起请求，仅展示引导提示
    if (!projectCode) {
      setLoading(false);
      setIsStreaming(false);
      setError(null);
      setStreamText('');
      return;
    }
    // 中断上一次未完成的流，并为本次请求分配新的代际号
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const reqId = ++requestSeqRef.current;
    const isCurrent = () => requestSeqRef.current === reqId && !controller.signal.aborted;

    const key = reportCacheKey(p, d, projectCode);

    if (!force) {
      const cached = loadReportCacheMap()[key];
      if (cached && cached.generatedOnDay === todayStr()) {
        setStreamText(cached.streamText);
        // 恢复用户上次编辑保存的富文本内容（若存在）
        setEditedHtml(cached.editedHtml || null);
        setError(null);
        setIsStreaming(false);
        setLoading(false);
        return;
      }
    }

    setLoading(true);
    setError(null);
    setStreamText('');
    setEditedHtml(null); // 重新生成以 AI 输出为准，清空上次编辑结果
    setIsStreaming(true);
    // 报告范围：项目必选，后端使用单项目模板，仅统计所选项目的数据
    try {
      const response = await generateReportStream(
        { period: p, date: d, project_code: projectCode },
        controller.signal,
      );
      const fullText = await readReportStream(response, (text) => {
        if (!isCurrent()) return; // 旧流回调一律忽略，不再触碰共享状态
        // 节流刷新：120ms 内的多个 chunk 合并为一次 Markdown 渲染（flush 时取最新文本）
        pendingTextRef.current = text;
        if (flushTimerRef.current == null) {
          flushTimerRef.current = window.setTimeout(() => {
            flushTimerRef.current = null;
            if (isCurrent()) {
              setStreamText(pendingTextRef.current);
              setLoading(false); // 第一个 chunk 到达时关闭 loading
            }
          }, 120);
        }
      });
      if (flushTimerRef.current != null) {
        window.clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      if (isCurrent()) {
        // 流正常结束但无任何正文（如 LLM 输出预算被思考过程耗尽）：
        // 给出明确提示而不是留下空白页面，同时避免把空结果写进缓存
        if (!fullText.trim()) {
          setError('报告生成内容为空（生成过程可能被截断），请点击「刷新」重试');
        } else {
          setStreamText(fullText); // 流结束确保最终完整文本上屏
          saveReportCacheEntry(key, { generatedOnDay: todayStr(), streamText: fullText });
        }
      }
    } catch (err) {
      if (!isCurrent()) return; // 被新请求中断/取代，静默忽略（含 AbortError）
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (flushTimerRef.current != null) {
        window.clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      if (isCurrent()) {
        setIsStreaming(false);
        setLoading(false);
      }
    }
  }, [projectCode]);

  useEffect(() => {
    // 切换视图/日期/项目时退出编辑模式，避免编辑框内容串到另一份报告上
    setEditing(false);
    fetchReport(period, date, false);
  }, [period, date, projectCode, fetchReport]);

  // 页面卸载时中断未完成的流，避免回调继续写已卸载组件的状态
  useEffect(() => () => { abortRef.current?.abort(); }, []);

  const handleRefresh = () => fetchReport(period, date, true);

  const handleProjectChange = (p: ProjectItem) => {
    setProjectCode(p.project_code);
    setProjectName(p.name);
  };

  // ---- 编辑（富文本所见即所得） ----
  const handleStartEdit = useCallback(async () => {
    // 已编辑过：直接用上次保存的富文本继续编辑
    if (editedHtml) {
      setEditInitialHtml(editedHtml);
      setEditing(true);
      return;
    }
    // 未编辑过：把 AI 生成的 markdown 渲染为 HTML 后灌入编辑器
    try {
      setEditInitialHtml(await markdownToHtml(streamText));
      setEditing(true);
    } catch (e) {
      console.error('[report-edit]', e);
      Toast({ message: '进入编辑失败，请重试', theme: 'error' });
    }
  }, [editedHtml, streamText]);

  const handleCancelEdit = () => {
    setEditing(false);
    setEditInitialHtml('');
  };

  const handleSaveEdit = () => {
    const raw = richEditorRef.current?.innerHTML || '';
    // 清洗编辑产物（去掉可能的样式垃圾/危险节点），空内容直接提示
    const clean = DOMPurify.sanitize(raw);
    if (!clean || !clean.replace(/<[^>]*>/g, '').trim()) {
      Toast({ message: '报告内容不能为空', theme: 'error' });
      return;
    }
    setEditedHtml(clean);
    // 编辑结果回写本地缓存：当天内切换视图/日期再切回时仍展示编辑后的内容；
    // 点击「刷新」重新生成会覆盖编辑结果（以 AI 重新生成为准）
    if (projectCode) {
      saveReportCacheEntry(reportCacheKey(period, date, projectCode), {
        generatedOnDay: todayStr(),
        streamText,
        editedHtml: clean,
      });
    }
    setEditing(false);
    Toast({ message: '已保存，预览与导出将以编辑后内容为准', theme: 'success' });
  };

  // ---- 导出 Word ----
  const handleExport = useCallback(async () => {
    if (exporting || !streamText || !projectCode) return;
    setExporting(true);
    try {
      const label = period === 'daily' ? '日报' : '周报';
      const projLabel = projectName || projectCode;
      const title = `${label} · ${date} · ${projLabel}`;
      const meta = `统计周期 ${date} ｜ 项目 ${projLabel}`;
      const base = `${label}_${date}_${projLabel}`;
      await (editedHtml
        ? exportReportWordFromHtml(editedHtml, title, meta, base)
        : exportReportWord(streamText, title, meta, base));
      Toast({ message: `${label}已导出为 Word`, theme: 'success' });
    } catch (e) {
      console.error('[report-export]', e);
      Toast({ message: '导出失败，请重试', theme: 'error' });
    } finally {
      setExporting(false);
    }
  }, [exporting, streamText, editedHtml, projectCode, projectName, period, date]);

  return (
    <div ref={rootRef} className="mac-page">
      {/* 视图切换（日报 / 周报） */}
      <div className="mac-card">
        <div className="mac-seg">
          <button
            type="button"
            className={`mac-seg__btn ${period === 'daily' ? 'is-active' : ''}`}
            onClick={() => setPeriod('daily')}
          >
            日报视图
          </button>
          <button
            type="button"
            className={`mac-seg__btn ${period === 'weekly' ? 'is-active' : ''}`}
            onClick={() => setPeriod('weekly')}
          >
            周报视图
          </button>
        </div>
      </div>

      {/* 项目选择（必选） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
        <div style={{ flex: 1 }} className="mac-projectselect">
            <ProjectSelect
              value={projectCode}
              onChange={handleProjectChange}
              placeholder="请选择项目（必选）"
              title="选择项目"
              projects={myProjects}
            />
        </div>
      </div>

      {/* 日期条：内嵌 antd DatePicker（桌面下拉日历/移动端点选，双端可用），右侧刷新按钮 */}
      <div className="mac-datebar" style={{ marginTop: 12 }}>
        <span className="mac-datebar__icon"><MacCalendarDays size={16} /></span>
        <DatePicker
          className="mac-datebar__picker"
          value={dayjs(date)}
          format="YYYY-MM-DD"
          allowClear={false}
          placeholder="选择日期"
          onChange={(d) => { if (d) setDate(d.format('YYYY-MM-DD')); }}
          getPopupContainer={(trigger) => trigger.parentElement || document.body}
          styles={{ popup: { root: { zIndex: 12000 } } }}
        />
        <button
          type="button"
          className="mac-datebar__refresh"
          disabled={isStreaming || !projectCode}
          onClick={handleRefresh}
        >
          <MacRefreshCw size={14} />
          {isStreaming ? '生成中...' : '刷新'}
        </button>
      </div>

      {myProjectsError && (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          <p>获取用户关联项目失败，请稍后重试</p>
        </div>
      )}

      {!myProjectsError && !myProjectsLoading && myProjects.length === 0 && (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          <p>您暂无关联项目，无法生成日报/周报，请联系管理员绑定项目</p>
        </div>
      )}

      {!myProjectsError && !myProjectsLoading && !projectCode && (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          <p>请先选择项目，系统将自动生成该项目的工作日报/周报</p>
        </div>
      )}

      {loading && <Loading text="AI 正在生成报告，请稍候..." />}

      {error && !loading && (
        <div className="mac-empty" style={{ padding: '40px 0' }}>
          <p>报告生成失败：{error}</p>
          <button type="button" className="mac-btn mac-btn--primary" style={{ marginTop: 12 }} onClick={handleRefresh}>
            重试
          </button>
        </div>
      )}

      {streamText && (
        <div style={{ marginTop: 12 }}>
          <ReportStreamCard
            text={streamText}
            streaming={isStreaming}
            period={period}
            date={date}
            projectName={projectName}
            editedHtml={editedHtml}
            editing={editing}
            initialHtml={editInitialHtml}
            exporting={exporting}
            editorRef={richEditorRef}
            onStartEdit={handleStartEdit}
            onSaveEdit={handleSaveEdit}
            onCancelEdit={handleCancelEdit}
            onExport={handleExport}
          />
        </div>
      )}
    </div>
  );
}
