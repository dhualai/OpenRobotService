// 日报周报分析 —— 对接 POST /api/ai/analysis/report/generate
// 数据来源：ai/agents/AiDataAnalysisPlatform/report_generator.py 实时采集 MySQL 中的
// 项目/风险/工单/任务数据并调用 LLM 生成结构化报告。
// 展示原则：
//   - 数值型指标（sections[].metrics）来自后端真实统计，按数值/分布字典生成卡片与图表；
//   - 章节正文（sections[].content、summary）为大模型生成的叙述文本，原样通过 MarkdownRenderer
//     渲染（含其中的示例编号、排行等），不在前端二次编造或裁剪。
//   - 趋势折线图（如工单/任务/项目数量的逐日走势）因接口仅返回整段周期的聚合值、无逐日序列，
//     暂不绘制，避免编造数据；分布类图表（状态/类型占比）有 metrics 字典支撑，予以保留。
import { useState, useEffect, useCallback } from 'react';
import { Tabs, TabPanel, Loading, Popup, Button, DateTimePicker } from 'tdesign-mobile-react';
import ReactECharts from 'echarts-for-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { generateReport, generateReportStream, readReportStream, type ReportPeriod, type ReportResult, type ReportSection } from '@/api/report';

const CHART_COLORS = ['#0052d9', '#2ba471', '#e37318', '#d54941', '#8e5fd9', '#00a3c4', '#999999'];

const METRIC_LABELS: Record<string, string> = {
  total: '总数', active: '活跃', completed: '已完成', on_hold: '暂停',
  new: '新增', resolved: '已解决', closed: '已关闭', overdue: '逾期',
  resolve_rate: '完成率', new_risks: '新增风险', closed_risks: '关闭风险',
  by_status: '状态分布', by_type: '类型分布', by_level: '等级分布',
};

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function formatDateTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function cardStyle(): React.CSSProperties {
  return { background: '#fff', borderRadius: 12, padding: 16, marginBottom: 12, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' };
}

function DistributionChart({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data).filter(([, v]) => typeof v === 'number' && v > 0);
  if (!entries.length) return null;
  const option = {
    tooltip: { trigger: 'item' as const },
    color: CHART_COLORS,
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      data: entries.map(([name, value]) => ({ name, value })),
      label: { fontSize: 11 },
    }],
  };
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{title}</div>
      <ReactECharts option={option} style={{ height: 200 }} notMerge />
    </div>
  );
}

function SectionBlock({ section, highlight }: { section: ReportSection; highlight?: boolean }) {
  const metrics = section.metrics || {};
  const numericMetrics = Object.entries(metrics).filter(([, v]) => typeof v === 'number');
  const distributions = Object.entries(metrics).filter(
    ([, v]) => v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length > 0,
  );

  return (
    <div
      style={{
        ...cardStyle(),
        ...(highlight
          ? { background: 'linear-gradient(135deg, #eff6ff, #ecfdf5)', border: '1px solid rgba(0,82,217,0.15)' }
          : {}),
      }}
    >
      <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 10 }}>{section.title}</div>

      {numericMetrics.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8, marginBottom: 8 }}>
          {numericMetrics.map(([key, value]) => (
            <div key={key} style={{ background: '#f5f7fa', borderRadius: 8, padding: '10px 8px', textAlign: 'center' }}>
              <div style={{ fontSize: 20, fontWeight: 700, color: '#0052d9' }}>
                {key === 'resolve_rate' ? `${value}%` : String(value)}
              </div>
              <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>{METRIC_LABELS[key] || key}</div>
            </div>
          ))}
        </div>
      )}

      {distributions.map(([key, value]) => (
        <DistributionChart key={key} title={METRIC_LABELS[key] || key} data={value as Record<string, number>} />
      ))}

      <div style={{ marginTop: numericMetrics.length || distributions.length ? 12 : 0 }}>
        <MarkdownRenderer content={section.content} compact />
      </div>
    </div>
  );
}

export default function DailySummaryAgent() {
  const [period, setPeriod] = useState<ReportPeriod>('daily');
  const [date, setDate] = useState<string>(todayStr());
  const [pickerVisible, setPickerVisible] = useState(false);
  const [report, setReport] = useState<ReportResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 流式状态
  const [useStream, setUseStream] = useState(true);
  const [streamText, setStreamText] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);

  const fetchReport = useCallback(async (p: ReportPeriod, d: string, stream: boolean) => {
    setLoading(true);
    setError(null);
    setReport(null);
    setStreamText('');

    if (stream) {
      // 流式模式：SSE 增量渲染
      setIsStreaming(true);
      try {
        const response = await generateReportStream({ period: p, date: d });
        await readReportStream(response, (text) => {
          setStreamText(text);
          setLoading(false); // 第一个 chunk 到达时关闭 loading
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setIsStreaming(false);
        setLoading(false);
      }
    } else {
      // 非流式模式：一次性返回结构化数据
      try {
        const data = await generateReport({ period: p, date: d });
        setReport(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    fetchReport(period, date, useStream);
  }, [period, date, useStream, fetchReport]);

  const handleRefresh = () => fetchReport(period, date, useStream);

  return (
    <div style={{ padding: 16 }}>
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

      {/* 流式/非流式切换 */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center', fontSize: 13, color: '#666' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={useStream}
            onChange={(e) => setUseStream(e.target.checked)}
            disabled={isStreaming}
          />
          流式输出
        </label>
        <span style={{ fontSize: 12, color: '#999' }}>
          {useStream ? '实时显示生成过程' : '等待完成后一次性展示'}
        </span>
      </div>

      {loading && <Loading text="AI 正在生成报告，请稍候..." />}

      {error && !loading && (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
          <p>报告生成失败：{error}</p>
          <Button theme="primary" onClick={handleRefresh} style={{ marginTop: 12 }}>重试</Button>
        </div>
      )}

      {/* ─── 流式模式：增量 Markdown 渲染 ─── */}
      {useStream && streamText && (
        <div style={cardStyle()}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
            <span style={{ fontSize: 15, fontWeight: 600 }}>
              {period === 'daily' ? '日报' : '周报'} · {date}
            </span>
            {isStreaming && (
              <span style={{ fontSize: 11, color: '#0052d9', animation: 'pulse 1.5s infinite' }}>● 生成中</span>
            )}
          </div>
          <MarkdownRenderer content={streamText} compact />
        </div>
      )}

      {/* ─── 非流式模式：结构化展示 ─── */}
      {!useStream && !loading && !error && report && (
        <>
          <div style={cardStyle()}>
            <Row label={period === 'daily' ? '报告日期' : '周报周期'} value={report.date_range} />
            <Row label={period === 'daily' ? '生成时间' : '数据更新时间'} value={formatDateTime(report.generated_at)} />
            {period === 'daily' && <Row label="统计周期" value="当日 00:00 - 23:59" />}
          </div>

          {report.sections
            .filter((s) => s.title !== '摘要')
            .map((s, i) => (
              <SectionBlock key={`${s.title}-${i}`} section={s} highlight={i === 0} />
            ))}
        </>
      )}

      {!loading && !error && !useStream && report && report.sections.length === 0 && (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无报告数据</div>
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

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 13 }}>
      <span style={{ color: '#999' }}>{label}</span>
      <span style={{ fontWeight: 500 }}>{value}</span>
    </div>
  );
}
