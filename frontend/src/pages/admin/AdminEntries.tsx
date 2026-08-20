// 后台管理 —— "其他"入口（从仪表盘「更多功能」进入）
// 仪表盘（/admin，Dashboard.tsx）是默认首页；本页仅承载不常用的管理员工具入口。
// 样式参考 macaron other 页：surface-card 行式入口 + 色调淡色图标圆角块。
// 顶部公众号用户统计：柱状图（同日期新增/取消用户合计）+ 饼图（时间段内来源分布），
// 数据源后端 /api/wechat/user-summary（X-API-Key 鉴权）。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Navbar } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';
import {
  MacUsers, MacTags, MacKeyRound, MacUserCog, MacShuffle, MacScrollText,
} from '@/shared/components/macaronIcons';
import ReactECharts from '@/shared/components/ReactECharts';
import { fetchUserSummary, USER_SOURCE_LABELS } from '@/api/wechat';
import type { UserSummaryItem } from '@/api/wechat';

interface Entry { path: string; label: string; desc: string; icon: ReactNode; tone: string; }

/** yyyy-MM-dd 格式化（本地时区） */
function fmtDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

// macaron 蓝阶配色（与 global.css --mac-blue-1..4 对齐），柱状图/饼图共用
const BAR_COLOR_NEW = '#3697c3';
const BAR_COLOR_CANCEL = '#93e0ff';
const PIE_COLORS = ['#227197', '#3697c3', '#51bfee', '#93e0ff', '#7fc6e8', '#5aa9cd', '#888d8f', '#c9d4d9', '#3d8ab0', '#c9e7f5'];

const adminEntries: Entry[] = [
  { path: '/admin/users', label: '用户管理', desc: '用户账号CRUD、派单画像', icon: <MacUsers />, tone: 'blue-1' },
  { path: '/admin/module-tree', label: '责任模块树', desc: '产品→界面→功能维护、工程师认领', icon: <MacTags />, tone: 'blue-2' },
  { path: '/admin/roles', label: '角色管理', desc: '角色定义、权限绑定', icon: <MacTags />, tone: 'blue-2' },
  { path: '/admin/permissions', label: '权限管理', desc: '权限项定义、分配', icon: <MacKeyRound />, tone: 'blue-3' },
  { path: '/admin/assign-role', label: '分配角色', desc: '为用户在项目中分配角色', icon: <MacUserCog />, tone: 'blue-2' },
  { path: '/admin/user-setup', label: '设置用户', desc: '迁移用户数据、合并账号', icon: <MacShuffle />, tone: 'blue-3' },
  { path: '/admin/operation-logs', label: '操作记录', desc: '操作日志审计与追溯', icon: <MacScrollText />, tone: 'blue-4' },
];

export default function AdminEntries() {
  const navigate = useNavigate();

  // ── 用户统计：时间筛选默认昨天（微信数据 T+1 延迟，最早可查昨日） ──
  const yesterdayDate = new Date();
  yesterdayDate.setDate(yesterdayDate.getDate() - 1);
  const yesterday = fmtDate(yesterdayDate);
  const [beginDate, setBeginDate] = useState(yesterday);
  const [endDate, setEndDate] = useState(yesterday);
  const [list, setList] = useState<UserSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!beginDate || !endDate) return;
    if (beginDate > endDate) {
      setList([]);
      setError('开始时间不能晚于结束时间');
      return;
    }
    let alive = true;
    setLoading(true);
    setError('');
    fetchUserSummary(beginDate, endDate)
      .then((r) => { if (alive) setList(r.list || []); })
      .catch((e: Error) => {
        if (!alive) return;
        setList([]);
        // 微信 T+1 延迟等业务错误直接透传后端文案（如 end_date 不能为今天）
        setError(e?.message || '加载用户统计数据失败');
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [beginDate, endDate]);

  // ── 聚合 + 图表配置 ──
  const { barOption, pieOption, hasData } = useMemo(() => {
    // 柱状图：同一日期下各来源 new_user / cancel_user 求和
    const dates = [...new Set(list.map((i) => i.ref_date))].sort();
    const sumBy = (d: string, key: 'new_user' | 'cancel_user') =>
      list.filter((i) => i.ref_date === d).reduce((s, i) => s + (i[key] || 0), 0);
    // 饼图：时间段内相同 user_source 的新增用户合计
    const sourceMap = new Map<number, number>();
    list.forEach((i) => sourceMap.set(i.user_source, (sourceMap.get(i.user_source) || 0) + (i.new_user || 0)));
    const pieData = [...sourceMap.entries()]
      .map(([s, v]) => ({ name: USER_SOURCE_LABELS[s] ?? `未知(${s})`, value: v }))
      .filter((d) => d.value > 0)
      .sort((a, b) => b.value - a.value);

    const axisLabel = { color: '#888d8f', fontSize: 10 };
    const barOption = {
      color: [BAR_COLOR_NEW, BAR_COLOR_CANCEL],
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['新增用户', '取消用户'], top: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: '#888d8f', fontSize: 11 } },
      grid: { left: 8, right: 8, top: 32, bottom: 0, containLabel: true },
      xAxis: { type: 'category', data: dates, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e8eaea' } }, axisLabel },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#f1f4f4' } }, axisLabel },
      series: [
        { name: '新增用户', type: 'bar', data: dates.map((d) => sumBy(d, 'new_user')), barMaxWidth: 24, itemStyle: { color: BAR_COLOR_NEW, borderRadius: [4, 4, 0, 0] } },
        { name: '取消用户', type: 'bar', data: dates.map((d) => sumBy(d, 'cancel_user')), barMaxWidth: 24, itemStyle: { color: BAR_COLOR_CANCEL, borderRadius: [4, 4, 0, 0] } },
      ],
    };
    const pieOption = {
      color: PIE_COLORS,
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: '#888d8f', fontSize: 10 } },
      series: [{
        type: 'pie',
        radius: ['38%', '62%'],
        center: ['50%', '42%'],
        data: pieData,
        label: { color: '#888d8f', fontSize: 10, formatter: '{b} {c}' },
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
      }],
    };
    return { barOption, pieOption, hasData: list.length > 0 };
  }, [list]);

  return (
    <div className="admin-view">
      <Navbar title="其他" leftArrow onLeftClick={() => navigate('/admin')} fixed />
      <div className="admin-entries-stats">
        <section className="admin-entries-stats__card">
          <div className="admin-entries-stats__head">
            <span className="admin-entries-stats__title">用户统计</span>
            <div className="admin-entries-stats__filter">
              <input
                type="date"
                className="admin-entries-stats__date"
                value={beginDate}
                max={endDate || undefined}
                onChange={(e) => setBeginDate(e.target.value)}
              />
              <span className="admin-entries-stats__sep">至</span>
              <input
                type="date"
                className="admin-entries-stats__date"
                value={endDate}
                min={beginDate || undefined}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
          </div>
          {loading ? (
            <div className="admin-entries-stats__empty"><Loading text="加载中..." /></div>
          ) : error ? (
            <div className="admin-entries-stats__empty">{error}</div>
          ) : !hasData ? (
            <div className="admin-entries-stats__empty">该时间段暂无用户增减数据</div>
          ) : (
            <div className="admin-entries-stats__charts">
              <div className="admin-entries-stats__chart">
                <span className="admin-entries-stats__sub">用户增减趋势</span>
                <ReactECharts option={barOption} style={{ height: 240 }} notMerge />
              </div>
              <div className="admin-entries-stats__chart">
                <span className="admin-entries-stats__sub">关注来源分布</span>
                <ReactECharts option={pieOption} style={{ height: 240 }} notMerge />
              </div>
            </div>
          )}
        </section>
      </div>
      <div className="admin-entries-grid">
        {adminEntries.map((e) => (
          <button
            key={e.path}
            type="button"
            className="admin-entries-card"
            data-tone={e.tone}
            onClick={() => navigate(e.path)}
          >
            <span className="admin-entries-card__icon">{e.icon}</span>
            <span className="admin-entries-card__body">
              <span className="admin-entries-card__label">{e.label}</span>
              <span className="admin-entries-card__desc">{e.desc}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
