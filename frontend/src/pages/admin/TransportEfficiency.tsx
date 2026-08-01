// 搬运效率分析 —— 按日期查看某项目的搬运效率汇总指标 + AGV型号对比表
// 参考 DAS 项目指标页（ProjectMetricsList）：顶部信息卡片 + 10 个指标卡片 + 各组数据对比表格
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
  created_at?: string | null;
  updated_at?: string | null;
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
  created_at?: string | null;
}

interface EfficiencyResponse {
  summary: EfficiencySummary | null;
  robots: RobotEfficiency[];
}

// 仅数值型指标字段（排除时间字段），用于指标卡片与型号对比表的 key
type SummaryMetricKey = Exclude<keyof EfficiencySummary, 'created_at' | 'updated_at'>;
type RobotMetricKey = Exclude<keyof RobotEfficiency, 'robot_model' | 'created_at'>;

// 10 个指标卡片：中文标签 + 单位 + 主题色 + 图标
const SUMMARY_METRICS: Array<{ key: SummaryMetricKey; label: string; unit: string; color: string; icon: string; percent?: boolean }> = [
  { key: 'total_tasks', label: '总任务数', unit: '个', color: '#0052d9', icon: '📋' },
  { key: 'carry_task_count', label: '搬运任务数量', unit: '个', color: '#0891b2', icon: '🚚' },
  { key: 'effective_work_hours', label: '有效工作时长', unit: '小时', color: '#059669', icon: '⏱️' },
  { key: 'fault_hours', label: '机器人故障时长', unit: '小时', color: '#dc2626', icon: '⚠️' },
  { key: 'idle_hours', label: '空闲无任务时间', unit: '小时', color: '#d97706', icon: '🕐' },
  { key: 'avg_error_count', label: '平均错误次数', unit: '次', color: '#e11d48', icon: '❌' },
  { key: 'avg_fault_duration_minutes', label: '平均单次故障时间', unit: '分钟', color: '#dc2626', icon: '🔧' },
  { key: 'avg_carry_duration_minutes', label: '平均单次搬运任务时间', unit: '分钟', color: '#7c3aed', icon: '🎯' },
  { key: 'avg_manual_switch_count', label: '平均切手动次数', unit: '次', color: '#ea580c', icon: '✋' },
  { key: 'manual_intervention_rate', label: '人工干预率', unit: '百分比', color: '#db2777', icon: '📊', percent: true },
];

// 各组数据对比表：指标行（列 = AGV 型号/机器人组）
const ROBOT_ROWS: Array<{ key: RobotMetricKey; label: string }> = [
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

// 数值格式化：保留两位小数；percent 转换为百分比
const formatValue = (value: number | null | undefined, percent?: boolean): string => {
  if (value == null) return '-';
  const num = Number(value);
  if (percent) return `${Math.round(num * 100)}%`;
  const rounded = Number.isInteger(num) ? num : Math.round(num * 100) / 100;
  return String(rounded);
};

export default function TransportEfficiency() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

  const [date, setDate] = useState(todayStr());
  const [datePickerVisible, setDatePickerVisible] = useState(false);
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<EfficiencyResponse | null>(null);
  const [projectName, setProjectName] = useState('');

  const fetchProjectName = useCallback(async () => {
    if (!id) return;
    try {
      const project = await request<{ name: string }>(`/projects/${encodeURIComponent(id)}`);
      setProjectName(project.name || '');
    } catch {
      setProjectName('');
    }
  }, [id]);

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
  useEffect(() => { fetchProjectName(); }, [fetchProjectName]);

  const hasData = !!(data?.summary || (data?.robots && data.robots.length > 0));

  // 数据采集时间：取汇总记录最后更新时间（无汇总记录时退回到型号明细的创建时间）
  const collectionTime = data?.summary?.updated_at || data?.summary?.created_at || data?.robots?.[0]?.created_at || null;

  return (
    <div>
      <Navbar title="搬运效率分析" leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: 16, paddingTop: 64 }}>
        {/* 信息卡片：项目名称 / 指标标签 / 数据采集时间 / 数据时间范围 */}
        <div style={{ background: '#fff', borderRadius: 8, padding: '10px 16px', marginBottom: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <InfoRow label="项目名称" value={projectName || '-'} />
          <InfoRow label="指标标签" value="搬运效率" />
          <InfoRow label="数据采集时间" value={collectionTime || '-'} />
          <InfoRow label="数据时间范围" value={`${date} 00:00:00 ~ ${date} 23:59:59`} />
        </div>

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
                  <div key={m.key} style={{ background: '#fff', borderRadius: 8, padding: '12px 10px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div style={{ fontSize: 12, color: '#999' }}>{m.label}</div>
                      <div style={{
                        width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
                        background: `${m.color}1a`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13,
                      }}>
                        {m.icon}
                      </div>
                    </div>
                    <div style={{ fontSize: 18, fontWeight: 700, color: m.color, marginTop: 6 }}>
                      {formatValue(data.summary![m.key], m.percent)}
                    </div>
                    <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>{m.unit}</div>
                  </div>
                ))}
              </div>
            )}

            {data?.robots && data.robots.length > 0 && (
              <div style={{ background: '#fff', borderRadius: 8, padding: 14, boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
                <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>各组数据对比</div>
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
                              {formatValue(r[row.key])}
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

// 信息卡片中的一行：灰色标签 + 右侧数值
function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 0', fontSize: 13 }}>
      <span style={{ color: '#999', flexShrink: 0 }}>{label}</span>
      <span style={{ color: '#1a1a1a', fontWeight: 500, textAlign: 'right' }}>{value}</span>
    </div>
  );
}
