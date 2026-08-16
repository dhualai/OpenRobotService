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

// 流式报告卡片（React.memo）：进行中用纯文本增量渲染，避免 Markdown 全量重解析卡顿；完成后转 Markdown 全量渲染
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
      {streaming ? (
        <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7, fontSize: 14, color: 'var(--mac-fg)' }}>{text}</div>
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
  const [pickerVisible, setPickerVisible] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [streamText, setStreamText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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
    const key = reportCacheKey(p, d, projectCode);

    if (!force) {
      const cached = loadReportCacheMap()[key];
      if (cached && cached.generatedOnDay === todayStr()) {
        setStreamText(cached.streamText);
        setError(null);
        setLoading(false);
        return;
      }
    }

    setLoading(true);
    setError(null);
    setStreamText('');
    setIsStreaming(true);
    try {
      const response = await generateReportStream({ period: p, date: d, project_code: projectCode ?? undefined });
      const fullText = await readReportStream(response, (text) => {
        setStreamText(text);
        setLoading(false); // 第一个 chunk 到达时关闭 loading
      });
      saveReportCacheEntry(key, { generatedOnDay: todayStr(), streamText: fullText });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsStreaming(false);
      setLoading(false);
    }
  }, [projectCode]);

  useEffect(() => {
    fetchReport(period, date, false);
  }, [period, date, projectCode, fetchReport]);

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
