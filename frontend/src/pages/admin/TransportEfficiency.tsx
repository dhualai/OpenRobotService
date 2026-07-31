// 搬运效率分析 —— 按日期查看某项目的搬运效率汇总指标 + AGV型号对比表
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Loading, Toast, Popup, DateTimePicker } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface EfficiencySummary {
  total_tasks: number | null;
  carry_task_count: number | null;
  effective_work_hours: number | null;
  fault_hours: number | null;
  idle_hours: number | null;
  avg_error_count: number | null;
  avg_fault_duration_minutes: number | null;
  avg_carry_duration_minutes: number | null;
  avg_manual_switch_count: number | null;
  manual_intervention_rate: number | null;
}

interface RobotEfficiency {
  robot_model: string;
  carry_task_total: number | null;
  effective_work_hours: number | null;
  effective_efficiency: number | null;
  fault_hours: number | null;
  idle_hours: number | null;
  avg_fault_duration_minutes: number | null;
  avg_carry_duration_minutes: number | null;
}

interface EfficiencyResponse {
  summary: EfficiencySummary | null;
  robots: RobotEfficiency[];
}

const SUMMARY_METRICS: Array<{ key: keyof EfficiencySummary; label: string; unit?: string; percent?: boolean }> = [
  { key: 'total_tasks', label: '总任务数' },
  { key: 'carry_task_count', label: '搬运任务数量' },
  { key: 'effective_work_hours', label: '有效工作时长', unit: 'h' },
  { key: 'fault_hours', label: '机器人故障时长', unit: 'h' },
  { key: 'idle_hours', label: '空闲无任务时间', unit: 'h' },
  { key: 'avg_error_count', label: '平均错误次数' },
  { key: 'avg_fault_duration_minutes', label: '平均单次故障时间', unit: '分钟' },
  { key: 'avg_carry_duration_minutes', label: '平均单次搬运任务时间', unit: '分钟' },
  { key: 'avg_manual_switch_count', label: '平均切手动次数' },
  { key: 'manual_intervention_rate', label: '人工干预率', percent: true },
];

const ROBOT_ROWS: Array<{ key: keyof RobotEfficiency; label: string }> = [
  { key: 'carry_task_total', label: '搬运任务总数(个)' },
  { key: 'effective_work_hours', label: '有效工作时长(h)' },
  { key: 'effective_efficiency', label: '有效搬运效率(小时/个)' },
  { key: 'fault_hours', label: '机器人故障时间(小时)' },
  { key: 'idle_hours', label: '无工作时间(小时)' },
  { key: 'avg_fault_duration_minutes', label: '平均单次故障(分钟)' },
  { key: 'avg_carry_duration_minutes', label: '平均单次搬运时间(分钟)' },
];

const todayStr = (): string => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const formatValue = (value: number | null | undefined, unit?: string, percent?: boolean): string => {
  if (value == null) return '-';
  if (percent) return `${Math.round(value * 100)}%`;
  return unit ? `${value}${unit}` : String(value);
};

export default function TransportEfficiency() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [date, setDate] = useState(todayStr());
  const [datePickerVisible, setDatePickerVisible] = useState(false);
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<EfficiencyResponse | null>(null);

  const fetchData = useCallback(async () => {
    if (!id || !date) return;
    setLoading(true);
    try {
      const result = await request<EfficiencyResponse>(
        `/transport-efficiency/${encodeURIComponent(id)}?date=${encodeURIComponent(date)}`,
      );
      setData(result);
    } catch (err) {
      Toast({ message: `加载搬运效率数据失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [id, date]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const hasData = !!(data?.summary || (data?.robots && data.robots.length > 0));

  return (
    <div>
      <Navbar title="搬运效率分析" leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: 16, paddingTop: 64 }}>
        <div
          onClick={() => setDatePickerVisible(true)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            background: '#fff', borderRadius: 8, padding: '12px 14px', marginBottom: 16,
            boxShadow: '0 1px 3px rgba(0,0,0,0.06)', cursor: 'pointer',
          }}
        >
          <div style={{ fontWeight: 500 }}>{date}</div>
          <span style={{ color: '#999' }}>›</span>
        </div>

        <Popup visible={datePickerVisible} onClose={() => setDatePickerVisible(false)} placement="bottom">
          <DateTimePicker
            mode="date"
            title="选择日期"
            format="YYYY-MM-DD"
            value={date || undefined}
            onConfirm={(v) => { setDate(String(v)); setDatePickerVisible(false); }}
            onCancel={() => setDatePickerVisible(false)}
          />
        </Popup>

        {loading ? (
          <Loading text="加载中..." />
        ) : !hasData ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>该日期暂无搬运效率数据</div>
        ) : (
          <>
            {data?.summary && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, marginBottom: 16 }}>
                {SUMMARY_METRICS.map((m) => (
                  <div key={m.key} style={{ background: '#fff', borderRadius: 8, padding: '12px 10px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)', textAlign: 'center' }}>
                    <div style={{ fontSize: 18, fontWeight: 700, color: '#0052d9' }}>
                      {formatValue(data.summary![m.key], m.unit, m.percent)}
                    </div>
                    <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>{m.label}</div>
                  </div>
                ))}
              </div>
            )}

            {data?.robots && data.robots.length > 0 && (
              <div style={{ background: '#fff', borderRadius: 8, padding: 14, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>AGV型号对比</div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: 'left', padding: '6px 10px', borderBottom: '1px solid #eee', color: '#999', whiteSpace: 'nowrap' }}>指标</th>
                        {data.robots.map((r) => (
                          <th key={r.robot_model} style={{ textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid #eee', whiteSpace: 'nowrap' }}>
                            {r.robot_model}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {ROBOT_ROWS.map((row) => (
                        <tr key={row.key}>
                          <td style={{ padding: '6px 10px', borderBottom: '1px solid #f5f5f5', color: '#666', whiteSpace: 'nowrap' }}>{row.label}</td>
                          {data.robots.map((r) => (
                            <td key={r.robot_model} style={{ textAlign: 'right', padding: '6px 10px', borderBottom: '1px solid #f5f5f5', whiteSpace: 'nowrap' }}>
                              {r[row.key] ?? '-'}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
