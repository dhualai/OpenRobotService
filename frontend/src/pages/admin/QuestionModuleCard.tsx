import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchDashboardQuestions, type DashboardQuestions } from '@/api/dashboard';
import { parseBackendDate } from '@/shared/utils/time';

function beijingToday(): string {
  return new Intl.DateTimeFormat('sv-SE', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}

function displayTime(value: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const parsed = parseBackendDate(value);
  return parsed ? new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short', hour12: false,
  }).format(parsed) : '';
}

function Description({ title, text, emptyText }: { title: string; text: string; emptyText: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflow, setOverflow] = useState(false);
  const textRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const element = textRef.current;
    if (!element) return;
    const measure = () => setOverflow(element.scrollHeight > 3 * 22 + 1);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [text]);

  return (
    <div className="question-module__description">
      <h5>{title}</h5>
      <p ref={textRef} className={expanded ? 'question-module__text is-expanded' : 'question-module__text'}>
        {text || emptyText}
      </p>
      {overflow && (
        <button type="button" className="question-module__link" aria-label={`${expanded ? '收起' : '展开'}${title}`}
          aria-expanded={expanded} onClick={() => setExpanded((previous) => !previous)}>
          {expanded ? '收起' : '展开'}
        </button>
      )}
    </div>
  );
}

export default function QuestionModuleCard() {
  const [reportDate, setReportDate] = useState(beijingToday);
  const [data, setData] = useState<DashboardQuestions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [failedPage, setFailedPage] = useState(1);
  const requestId = useRef(0);

  const loadPage = useCallback(async (page: number) => {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError('');
    setFailedPage(page);
    if (page === 1) setData(null);
    try {
      const result = await fetchDashboardQuestions(reportDate, page);
      if (currentRequest !== requestId.current) return;
      setData((previous) => {
        if (page === 1 || previous?.report_date !== reportDate) return result;
        const items = new Map(previous.items.map((item) => [item.record_id, item]));
        result.items.forEach((item) => items.set(item.record_id, item));
        return { ...result, items: [...items.values()] };
      });
    } catch (failure) {
      if (currentRequest !== requestId.current) return;
      setError(failure instanceof Error ? failure.message : '问题列表加载失败，请稍后重试');
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }, [reportDate]);

  useEffect(() => {
    void loadPage(1);
    return () => { requestId.current += 1; };
  }, [loadPage]);

  const currentData = data?.report_date === reportDate ? data : null;
  return (
    <section className="mac-card mac-card--pad question-module" aria-labelledby="question-module-title">
      <h4 id="question-module-title" className="mac-source-block__title">问题模块</h4>
      <div className="question-module__filters">
        <label htmlFor="question-report-date">上报日期</label>
        <input id="question-report-date" type="date" className="mac-input" value={reportDate}
          onChange={(event) => { if (event.target.value) setReportDate(event.target.value); }} />
        <button type="button" className="question-module__link" onClick={() => setReportDate(beijingToday())}>今天</button>
        <button type="button" className="question-module__link" disabled={loading} onClick={() => void loadPage(1)}>刷新</button>
      </div>
      {currentData && <p className="question-module__meta">共 {currentData.total} 条问题 · 按北京时间筛选</p>}
      <div aria-busy={loading} aria-live="polite">
        {currentData?.items.map((item) => (
          <article key={`${reportDate}:${item.record_id}`} className="question-module__item">
            <div className="question-module__vehicle">车辆编号和车型：{item.vehicle_info || [item.vehicle_number, item.vehicle_model].filter(Boolean).join(' · ') || '未填写'}</div>
            <div className="question-module__meta">上报时间：{displayTime(item.reported_at)}</div>
            <Description title="问题描述" text={item.problem_description} emptyText="未填写" />
            <Description title="问题解决描述" text={item.solution_description} emptyText="暂未填写" />
          </article>
        ))}
        {!loading && !error && currentData?.total === 0 && <p className="question-module__state">该日期暂无上报问题</p>}
        {loading && <p className="question-module__state" role="status">{currentData ? '正在加载更多…' : '正在加载问题…'}</p>}
        {error && (
          <div className="question-module__state" role="alert">
            <p>{error}</p>
            <button type="button" className="question-module__link" onClick={() => void loadPage(failedPage)}>重试</button>
          </div>
        )}
      </div>
      {!loading && !error && currentData && currentData.page * currentData.page_size < currentData.total && (
        <div className="question-module__state">
          <button type="button" className="question-module__link" onClick={() => void loadPage(currentData.page + 1)}>加载更多</button>
        </div>
      )}
      {currentData?.last_sync_at && <p className="question-module__meta">数据最近同步时间：{displayTime(currentData.last_sync_at)}</p>}
    </section>
  );
}
