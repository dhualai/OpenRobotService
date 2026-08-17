// 日报周报分析 —— 对接 POST /api/ai/analysis/report/generate（流式 SSE）
// 数据来源：ai/agents/AiDataAnalysisPlatform/report_generator.py 实时采集 MySQL 中的
// 项目/风险/工单/任务数据并调用 LLM 生成报告文本。
// 样式参考 macaron reports 页：卡片内分段切换 + surface-card 项目选择 + 淡蓝日期条。
import { memo, useState, useEffect, useCallback, useRef } from 'react';
import { Loading, Popup, DateTimePicker } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { generateReportStream, readReportStream, type ReportPeriod } from '@/api/report';
import ProjectSelect from '@/shared/components/ProjectSelect';
import type { ProjectItem } from '@/api/projects';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
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

// 流式报告卡片（React.memo）：流式与完成后统一用 MarkdownRenderer 实时渲染，
// 从头到尾都是渲染后的样式，不会出现“先源码后渲染”的闪变
const ReportStreamCard = memo(function ReportStreamCard({
  text, streaming, period, date, projectName,
}: {
  text: string;
  streaming: boolean;
  period: ReportPeriod;
  date: string;
  projectName?: string | null;
}) {
  return (
    <div className="mac-card mac-card--pad">
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--mac-fg)' }}>
          {period === 'daily' ? '日报' : '周报'} · {date}
          {projectName ? ` · ${projectName}` : ''}
        </span>
        {streaming && <span className="mac-streaming">● 生成中</span>}
      </div>
      <MarkdownRenderer content={text} compact />
    </div>
  );
});

export default function DailySummaryAgent() {
  const [period, setPeriod] = useState<ReportPeriod>('daily');
  const [date, setDate] = useState<string>(todayStr());
  const [projectCode, setProjectCode] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [pickerVisible, setPickerVisible] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [streamText, setStreamText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
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

  const fetchReport = useCallback(async (p: ReportPeriod, d: string, force: boolean) => {
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
        setError(null);
        setIsStreaming(false);
        setLoading(false);
        return;
      }
    }

    setLoading(true);
    setError(null);
    setStreamText('');
    setIsStreaming(true);
    // 报告范围：选了项目 → project_code 优先（后端用单项目模板）；
    // 未选项目 → 传当前用户 username 作为 user_id，后端查该用户关联的
    // 全部项目与工单（用户范围模板）；拥有查看全量权限的用户不传，全局统计
    const { username, permissions } = useAuthStore.getState();
    const userId = projectCode || permissions.includes(PERMISSION_VIEW_ALL)
      ? undefined
      : username || undefined;
    try {
      const response = await generateReportStream(
        { period: p, date: d, project_code: projectCode ?? undefined, user_id: userId },
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
        setStreamText(fullText); // 流结束确保最终完整文本上屏
        saveReportCacheEntry(key, { generatedOnDay: todayStr(), streamText: fullText });
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
    fetchReport(period, date, false);
  }, [period, date, projectCode, fetchReport]);

  // 页面卸载时中断未完成的流，避免回调继续写已卸载组件的状态
  useEffect(() => () => { abortRef.current?.abort(); }, []);

  const handleRefresh = () => fetchReport(period, date, true);

  const handleProjectChange = (p: ProjectItem) => {
    setProjectCode(p.project_code);
    setProjectName(p.name);
  };
  const handleResetProject = () => {
    setProjectCode(null);
    setProjectName(null);
  };

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

      {/* 项目选择 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
        <div style={{ flex: 1 }} className="mac-projectselect">
          <ProjectSelect
            value={projectCode}
            onChange={handleProjectChange}
            placeholder="全部项目 · 点击选择"
            title="选择项目"
          />
        </div>
        {projectCode && (
          <button type="button" className="mac-btn mac-btn--ghost" onClick={handleResetProject}>
            全部项目
          </button>
        )}
      </div>

      {/* 日期条：点击选日期，右侧刷新按钮 */}
      <div className="mac-datebar" style={{ marginTop: 12 }} onClick={() => setPickerVisible(true)}>
        <span className="mac-datebar__icon"><MacCalendarDays size={16} /></span>
        <span className="mac-datebar__label">{date}</span>
        <button
          type="button"
          className="mac-datebar__refresh"
          disabled={isStreaming}
          onClick={(e) => { e.stopPropagation(); handleRefresh(); }}
        >
          <MacRefreshCw size={14} />
          {isStreaming ? '生成中...' : '刷新'}
        </button>
      </div>

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
          <ReportStreamCard text={streamText} streaming={isStreaming} period={period} date={date} projectName={projectName} />
        </div>
      )}

      <Popup visible={pickerVisible} onClose={() => setPickerVisible(false)} placement="bottom">
        <DateTimePicker
          mode="date"
          value={date}
          format="YYYY-MM-DD"
          title="选择日期"
          onConfirm={(v) => {
            setDate(String(v));
            setPickerVisible(false);
          }}
          onCancel={() => setPickerVisible(false)}
        />
      </Popup>
    </div>
  );
}
