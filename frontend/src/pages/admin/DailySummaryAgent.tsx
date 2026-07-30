// 日报周报分析 —— 对接 POST /api/ai/analysis/report/generate（流式 SSE）
// 数据来源：ai/agents/AiDataAnalysisPlatform/report_generator.py 实时采集 MySQL 中的
// 项目/风险/工单/任务数据并调用 LLM 生成报告文本。
import { memo, useState, useEffect, useCallback, useRef } from 'react';
import { Tabs, TabPanel, Loading, Popup, Button, DateTimePicker } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { generateReportStream, readReportStream, type ReportPeriod } from '@/api/report';

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// 生成结果本地缓存：每日首次生成后写入，当天内再次查看（切换视图/日期）直接读取，
// 不重复调用 LLM；仅点击「刷新」按钮才强制重新生成并覆盖缓存。
// key = period:date。
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

function reportCacheKey(p: ReportPeriod, d: string): string {
  return `${p}:${d}`;
}

function cardStyle(): React.CSSProperties {
  return { background: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' };
}

// 流式报告卡片（React.memo）：进行中用纯文本增量渲染，避免 Markdown 全量重解析卡顿；完成后转 Markdown 全量渲染
const ReportStreamCard = memo(function ReportStreamCard({
  text, streaming, period, date,
}: {
  text: string;
  streaming: boolean;
  period: ReportPeriod;
  date: string;
}) {
  return (
    <div style={cardStyle()}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>
          {period === 'daily' ? '日报' : '周报'} · {date}
        </span>
        {streaming && (
          <span style={{ fontSize: 11, color: '#0052d9', animation: 'pulse 1.5s infinite' }}>● 生成中</span>
        )}
      </div>
      {streaming ? (
        <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.7, fontSize: 14, color: '#333' }}>{text}</div>
      ) : (
        <MarkdownRenderer content={text} compact />
      )}
    </div>
  );
});

export default function DailySummaryAgent() {
  const [period, setPeriod] = useState<ReportPeriod>('daily');
  const [date, setDate] = useState<string>(todayStr());
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
    const key = reportCacheKey(p, d);

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
      const response = await generateReportStream({ period: p, date: d });
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
  }, []);

  useEffect(() => {
    fetchReport(period, date, false);
  }, [period, date, fetchReport]);

  const handleRefresh = () => fetchReport(period, date, true);

  return (
    <div ref={rootRef} style={{ padding: 16 }}>
      <div style={{ background: '#eef1f4', borderRadius: 999, padding: 4, display: 'flex', marginBottom: 16 }}>
        <Tabs value={period} onChange={(v) => setPeriod(v as ReportPeriod)} style={{ flex: 1 }}>
          <TabPanel value="daily" label="日报视图" />
          <TabPanel value="weekly" label="周报视图" />
        </Tabs>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <Button theme="light" block onClick={() => setPickerVisible(true)}>
          📅 {date}
        </Button>
        <Button theme="light" onClick={handleRefresh} disabled={isStreaming}>
          {isStreaming ? '⏳ 生成中...' : '🔄 刷新'}
        </Button>
      </div>

      {loading && <Loading text="AI 正在生成报告，请稍候..." />}

      {error && !loading && (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          <p>报告生成失败：{error}</p>
          <Button theme="primary" onClick={handleRefresh} style={{ marginTop: 12 }}>重试</Button>
        </div>
      )}

      {streamText && (
        <ReportStreamCard text={streamText} streaming={isStreaming} period={period} date={date} />
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
