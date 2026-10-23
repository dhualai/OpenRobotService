// 公司工作日历管理 —— 工作时间（公司级，单段）+ 法定节假日/调休补班
// 用途：决定工单逾期/临期提醒按哪个时区、哪个时段推送（「按接收人工作时间推送」）。
// 规则：仅公司级；默认周一至周五 09:00-18:00；节假日手工维护；不支持午休分段与夜班。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Button, Input, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { MacCalendarDays } from '@/shared/components/macaronIcons';
import FilterableSelect from '@/shared/components/FilterableSelect';
import { getProfileOptions } from '@/api/profile';
import type { OrgOption } from '@/api/profile';
import {
  getWorkCalendar, updateWorkCalendar, listHolidays, createHoliday,
  batchUpsertHolidays, deleteHoliday,
  WEEKDAY_LABELS, parseWorkdays, formatWorkdays,
  type Holiday, type WorkCalendar,
} from '@/api/workCalendar';

const CURRENT_YEAR = new Date().getFullYear();

export default function WorkCalendarManage() {
  const [companies, setCompanies] = useState<OrgOption[]>([]);
  const [companyId, setCompanyId] = useState('');
  const [companyName, setCompanyName] = useState('');

  const [workStart, setWorkStart] = useState('09:00');
  const [workEnd, setWorkEnd] = useState('18:00');
  const [workdays, setWorkdays] = useState<number[]>([1, 2, 3, 4, 5]);

  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const [year, setYear] = useState<number>(CURRENT_YEAR);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // 新增节假日
  const [newDate, setNewDate] = useState('');
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState<'holiday' | 'workday_override'>('holiday');
  const [adding, setAdding] = useState(false);

  // 批量导入
  const [pendingDelete, setPendingDelete] = useState<Holiday | null>(null);

  // 批量导入
  const [batchOpen, setBatchOpen] = useState(false);
  const [batchText, setBatchText] = useState('');
  const [batchType, setBatchType] = useState<'holiday' | 'workday_override'>('holiday');
  const [importing, setImporting] = useState(false);

  const companyNames = useMemo(
    () => companies.map((c) => (c.status === 'pending' ? `${c.name}（审核中）` : c.name)),
    [companies],
  );

  useEffect(() => {
    getProfileOptions()
      .then((opts) => setCompanies(opts.companies || []))
      .catch(() => setCompanies([]));
  }, []);

  const loadCalendar = useCallback(async (cid: string) => {
    if (!cid) return;
    setLoading(true);
    try {
      const [cal, hl] = await Promise.all([
        getWorkCalendar(cid),
        listHolidays(cid, year).catch(() => [] as Holiday[]),
      ]);
      setWorkStart(cal.work_start || '09:00');
      setWorkEnd(cal.work_end || '18:00');
      setWorkdays(parseWorkdays(cal.workdays || '1,2,3,4,5'));
      setHolidays(hl || []);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [year]);

  useEffect(() => {
    void loadCalendar(companyId);
  }, [companyId, loadCalendar]);

  const handleCompanyChange = useCallback((displayName: string) => {
    const realName = displayName.replace(/（审核中）$/, '');
    const comp = companies.find((c) => c.name === realName);
    setCompanyName(displayName);
    setCompanyId(comp?.id || '');
  }, [companies]);

  const handleSave = useCallback(async () => {
    if (!companyId) {
      Toast({ message: '请先选择公司', theme: 'warning' });
      return;
    }
    if (!/^\d{2}:\d{2}$/.test(workStart) || !/^\d{2}:\d{2}$/.test(workEnd)) {
      Toast({ message: '时间需为 HH:MM 格式', theme: 'warning' });
      return;
    }
    if (workStart >= workEnd) {
      Toast({ message: '上班时间须早于下班时间（不支持跨午夜）', theme: 'warning' });
      return;
    }
    if (!workdays.length) {
      Toast({ message: '请至少选择一个工作日', theme: 'warning' });
      return;
    }
    setSaving(true);
    try {
      await updateWorkCalendar(companyId, {
        work_start: workStart,
        work_end: workEnd,
        workdays: formatWorkdays(workdays),
      });
      Toast({ message: '工作日历已保存', theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  }, [companyId, workStart, workEnd, workdays]);

  const handleAddHoliday = useCallback(async () => {
    if (!companyId || !newDate) {
      Toast({ message: '请选择公司并填写日期', theme: 'warning' });
      return;
    }
    setAdding(true);
    try {
      await createHoliday(companyId, { date: newDate, name: newName || undefined, type: newType });
      setNewDate('');
      setNewName('');
      Toast({ message: '已新增', theme: 'success' });
      await loadCalendar(companyId);
    } catch (err) {
      Toast({ message: `新增失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setAdding(false);
    }
  }, [companyId, newDate, newName, newType, loadCalendar]);

  const confirmDelete = useCallback(async () => {
    const target = pendingDelete;
    setPendingDelete(null);
    if (!target || !companyId) return;
    try {
      await deleteHoliday(companyId, target.id);
      Toast({ message: '已删除', theme: 'success' });
      await loadCalendar(companyId);
    } catch (err) {
      Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  }, [pendingDelete, companyId, loadCalendar]);

  const handleBatchImport = useCallback(async () => {
    if (!companyId) return;
    const items = batchText
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        // 支持 "2026-10-01 国庆节" 或 "2026-10-01"
        const [d, ...rest] = line.split(/\s+/);
        return { date: d, name: rest.join(' ') || undefined, type: batchType };
      })
      .filter((it) => /^\d{4}-\d{2}-\d{2}$/.test(it.date));
    if (!items.length) {
      Toast({ message: '未解析到有效日期（每行一个 yyyy-MM-dd）', theme: 'warning' });
      return;
    }
    setImporting(true);
    try {
      const res = await batchUpsertHolidays(companyId, items);
      Toast({ message: `导入完成：新增 ${res.created}、更新 ${res.updated}`, theme: 'success' });
      setBatchText('');
      setBatchOpen(false);
      await loadCalendar(companyId);
    } catch (err) {
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setImporting(false);
    }
  }, [companyId, batchText, batchType, loadCalendar]);

  const toggleWeekday = useCallback((day: number) => {
    setWorkdays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day],
    );
  }, []);

  return (
    <div style={{ padding: 16, maxWidth: 880, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <MacCalendarDays size={20} />
        <span style={{ fontSize: 16, fontWeight: 600 }}>公司工作日历</span>
      </div>
      <div style={{ fontSize: 12, color: '#999', marginBottom: 16 }}>
        决定工单逾期/临期提醒按哪个时区、哪个时段推送。仅公司级；节假日需管理员手工维护。
      </div>

      <div style={{ background: '#fff', borderRadius: 12, padding: 16, marginBottom: 16 }}>
        <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>选择公司</div>
        <FilterableSelect
          value={companyName}
          onChange={handleCompanyChange}
          options={companyNames}
          placeholder="请选择公司"
          title="选择公司"
          searchPlaceholder="搜索公司…"
        />
      </div>

      {loading && <Loading text="加载中…" />}

      {companyId && !loading && (
        <>
          <div style={{ background: '#fff', borderRadius: 12, padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>工作时间</div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>上班时间</div>
                <Input value={workStart} onChange={(v) => setWorkStart(String(v))} placeholder="09:00" />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>下班时间</div>
                <Input value={workEnd} onChange={(v) => setWorkEnd(String(v))} placeholder="18:00" />
              </div>
            </div>

            <div style={{ fontSize: 12, color: '#888', marginBottom: 6 }}>工作日</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
              {WEEKDAY_LABELS.map(({ value, label }) => {
                const active = workdays.includes(value);
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => toggleWeekday(value)}
                    style={{
                      padding: '6px 12px',
                      borderRadius: 16,
                      border: '1px solid #ddd',
                      background: active ? '#0052d9' : '#fff',
                      color: active ? '#fff' : '#333',
                      fontSize: 13,
                      cursor: 'pointer',
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>

            <Button theme="primary" block loading={saving} onClick={handleSave}>
              保存工作时间
            </Button>
          </div>

          <div style={{ background: '#fff', borderRadius: 12, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>节假日 / 调休</span>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {/* 年份可切换：年底需提前导入次年节假日，否则跨年后的提醒会漏判假期 */}
                <Button
                  theme="primary"
                  variant="outline"
                  size="small"
                  onClick={() => setYear((y) => y - 1)}
                >
                  上一年
                </Button>
                <span style={{ fontSize: 13, fontWeight: 600, minWidth: 44, textAlign: 'center' }}>{year}</span>
                <Button
                  theme="primary"
                  variant="outline"
                  size="small"
                  onClick={() => setYear((y) => y + 1)}
                >
                  下一年
                </Button>
                <Button theme="primary" variant="outline" size="small" onClick={() => setBatchOpen(true)}>
                  批量导入
                </Button>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
              <div style={{ flex: '0 0 140px' }}>
                <Input value={newDate} onChange={(v) => setNewDate(String(v))} placeholder="2026-10-01" />
              </div>
              <div style={{ flex: 1 }}>
                <Input value={newName} onChange={(v) => setNewName(String(v))} placeholder="名称，如 国庆节" />
              </div>
              <Button
                theme="primary"
                variant="outline"
                size="small"
                onClick={() => setNewType(newType === 'holiday' ? 'workday_override' : 'holiday')}
                style={{ flexShrink: 0 }}
              >
                {newType === 'holiday' ? '放假' : '补班'}
              </Button>
              <Button theme="primary" size="small" loading={adding} onClick={handleAddHoliday} style={{ flexShrink: 0 }}>
                新增
              </Button>
            </div>

            {holidays.length === 0 ? (
              <div style={{ fontSize: 13, color: '#999', padding: '12px 0' }}>暂无记录</div>
            ) : (
              <div>
                {holidays.map((h) => (
                  <div
                    key={h.id}
                    style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '10px 0', borderBottom: '1px solid #f0f0f0',
                    }}
                  >
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span style={{ fontSize: 13 }}>{h.date}</span>
                      <span style={{ fontSize: 12, color: '#888' }}>{h.name || '—'}</span>
                      <span
                        style={{
                          fontSize: 11, padding: '1px 6px', borderRadius: 8,
                          background: h.type === 'workday_override' ? '#e8f3ff' : '#ffece8',
                          color: h.type === 'workday_override' ? '#0052d9' : '#d54941',
                        }}
                      >
                        {h.type === 'workday_override' ? '调休补班' : '放假'}
                      </span>
                    </div>
                    <Button theme="danger" variant="text" size="small" onClick={() => setPendingDelete(h)}>
                      删除
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      <Dialog
        visible={!!pendingDelete}
        title="删除节假日"
        content={pendingDelete ? `确认删除 ${pendingDelete.date}？` : ''}
        confirmBtn="确认删除"
        cancelBtn="取消"
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      <Dialog
        visible={batchOpen}
        title="批量导入节假日"
        content={
          <div>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
              每行一条，格式：yyyy-MM-dd 名称（名称可省略）
            </div>
            <textarea
              value={batchText}
              onChange={(e) => setBatchText(e.target.value)}
              rows={8}
              placeholder={'2026-10-01 国庆节\n2026-10-02\n2026-10-10 国庆调休补班'}
              style={{ width: '100%', border: '1px solid #ddd', borderRadius: 6, padding: 8, fontSize: 13 }}
            />
            <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
              <Button
                theme="primary"
                variant="outline"
                size="small"
                onClick={() => setBatchType(batchType === 'holiday' ? 'workday_override' : 'holiday')}
              >
                {batchType === 'holiday' ? '标记为放假' : '标记为补班'}
              </Button>
              <span style={{ fontSize: 12, color: '#888' }}>当前：{batchType === 'holiday' ? '放假' : '调休补班'}</span>
            </div>
          </div>
        }
        confirmBtn={{ content: '导入', loading: importing }}
        cancelBtn="取消"
        onConfirm={handleBatchImport}
        onCancel={() => setBatchOpen(false)}
      />
    </div>
  );
}
