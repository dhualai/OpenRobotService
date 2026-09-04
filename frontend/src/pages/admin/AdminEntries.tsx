// 后台管理 —— "其他"入口（从仪表盘「更多功能」进入）
// 仪表盘（/admin，Dashboard.tsx）是默认首页；本页仅承载不常用的管理员工具入口。
// 样式参考 macaron other 页：surface-card 行式入口 + 色调淡色图标圆角块。
// 顶部用户统计改为同页两个分组框：左侧分组顶部放时间筛选框，并展示与筛选框联动的
// 用户增减趋势 + 关注来源分布；右侧分组展示不受筛选框影响的当前用户构成 + 用户来源分布。
// 数据源后端 /api/wechat/user-summary-db 与 /api/wechat/batch-user-info-db（读 user_statistics/user_info 表，
// 由每日凌晨 1:00 与整点定时任务落库，不再实时调微信 API）；每 10 秒轮询刷新功能已停用（代码注释保留）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Navbar } from 'tdesign-mobile-react';
import type { ReactNode } from 'react';
import {
  MacUsers, MacTags, MacKeyRound, MacUserCog, MacShuffle, MacScrollText,
} from '@/shared/components/macaronIcons';
import ReactECharts from '@/shared/components/ReactECharts';
import { fetchBatchUserInfo, fetchUserSummary, USER_SOURCE_LABELS } from '@/api/wechat';
import type { UserSummaryItem, WechatUserInfo } from '@/api/wechat';

interface Entry { path: string; label: string; desc: string; icon: ReactNode; tone: string; }

/** yyyy-MM-dd 格式化（本地时区） */
function fmtDate(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

/** 默认时间范围：最近 5 天不含当天（begin=今天-5，end=昨天；统计数据 T+1 次日 01:00 落库，最早可查昨日） */
function getDefaultRange(): { begin: string; end: string } {
  const now = new Date();
  const begin = new Date(now);
  begin.setDate(begin.getDate() - 5);
  const end = new Date(now);
  end.setDate(end.getDate() - 1);
  return { begin: fmtDate(begin), end: fmtDate(end) };
}

// macaron 蓝阶配色（与 global.css --mac-blue-1..4 对齐），柱状图/饼图共用
const BAR_COLOR_NEW = '#3697c3';
const BAR_COLOR_CANCEL = '#93e0ff';
const PIE_COLORS = ['#227197', '#3697c3', '#51bfee', '#93e0ff', '#7fc6e8', '#5aa9cd', '#888d8f', '#c9d4d9', '#3d8ab0', '#c9e7f5'];
// 轮询间隔（user-summary 与 batch-user-info 两个接口）——轮询功能已停用，随下方 setInterval 一并注释
// const POLL_INTERVAL = 10_000;
// 环形图真实/虚拟用户配色
const DONUT_COLOR_REAL = '#3697c3';
const DONUT_COLOR_VIRTUAL = '#c9d4d9';

/** subscribe_scene 关注渠道编码 → 中文含义（微信官方 ADD_SCENE_* 定义，取自用户提供的接口文档） */
const SUBSCRIBE_SCENE_LABELS: Record<string, string> = {
  ADD_SCENE_SEARCH: '公众号搜索',
  ADD_SCENE_ACCOUNT_MIGRATION: '公众号迁移',
  ADD_SCENE_PROFILE_CARD: '名片分享',
  ADD_SCENE_QR_CODE: '扫描二维码',
  ADD_SCENE_PROFILE_LINK: '图文页内名称点击',
  ADD_SCENE_PROFILE_ITEM: '图文页右上角菜单',
  ADD_SCENE_PAID: '支付后关注',
  ADD_SCENE_WECHAT_ADVERTISEMENT: '微信广告',
  ADD_SCENE_REPRINT: '他人转载',
  ADD_SCENE_LIVESTREAM: '视频号直播',
  ADD_SCENE_CHANNELS: '视频号',
  ADD_SCENE_WXA: '小程序关注',
  ADD_SCENE_OTHERS: '其他',
};

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

  // ── 用户统计：时间筛选默认最近 5 天（不含当天；微信数据 T+1 延迟，最早可查昨日） ──
  const yesterdayDate = new Date();
  yesterdayDate.setDate(yesterdayDate.getDate() - 1);
  const yesterday = fmtDate(yesterdayDate);
  const [beginDate, setBeginDate] = useState(() => getDefaultRange().begin);
  const [endDate, setEndDate] = useState(() => getDefaultRange().end);
  const [list, setList] = useState<UserSummaryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  // 当前用户构成（batch-user-info）：total 显示在环形图中心，real/virtual 为两扇区
  const [userStats, setUserStats] = useState<{ total: number; real: number; virtual: number } | null>(null);
  const [userLoading, setUserLoading] = useState(true);
  const [userError, setUserError] = useState('');
  // 最新快照全部关注用户的 subscribe_scene 来源分布（与当前用户构成同源同刷新，不受筛选框影响）
  const [sceneStats, setSceneStats] = useState<{ total: number; list: { name: string; value: number }[] } | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // 时间筛选联动：数据来自本地表，不限跨度；仅保证 begin ≤ end，且当天及以后无数据
  // （T+1 次日凌晨落库），任一端越过昨天时统一收窄到昨天，另一端跟随对齐
  const handleBeginChange = (v: string) => {
    const clamped = v && v > yesterday ? yesterday : v;
    setBeginDate(clamped);
    if (!clamped) return;
    if (endDate && endDate < clamped) setEndDate(clamped);
  };

  const handleEndChange = (v: string) => {
    const clamped = v && v > yesterday ? yesterday : v;
    setEndDate(clamped);
    if (!clamped) return;
    if (beginDate && beginDate > clamped) setBeginDate(clamped);
  };

  // 恢复默认时间范围（最近 5 天不含当天）；日期变化触发下方 useEffect 自动重新查询
  const handleReset = () => {
    const { begin, end } = getDefaultRange();
    setBeginDate(begin);
    setEndDate(end);
  };

  // silent=true 为轮询刷新：不显示 loading，失败时保留旧数据避免界面闪烁
  const loadSummary = useCallback((silent: boolean) => {
    if (!silent) { setLoading(true); setError(''); }
    fetchUserSummary(beginDate, endDate)
      .then((r) => { if (mountedRef.current) { setList(r.list || []); setError(''); } })
      .catch((e: Error) => {
        if (!mountedRef.current || silent) return;
        setList([]);
        // 微信 T+1 延迟等业务错误直接透传后端文案（如 end_date 不能为今天）
        setError(e?.message || '加载用户统计数据失败');
      })
      .finally(() => { if (mountedRef.current && !silent) setLoading(false); });
  }, [beginDate, endDate]);

  const loadUsers = useCallback((silent: boolean) => {
    if (!silent) { setUserLoading(true); setUserError(''); }
    fetchBatchUserInfo()
      .then((r) => {
        if (!mountedRef.current) return;
        const items = r.user_info_list || [];
        const real = items.filter((u) => u.subscribe === 1).length;
        setUserStats({
          total: typeof r.total === 'number' ? r.total : items.length,
          real,
          virtual: items.length - real,
        });
        // 用户来源分布：仅统计 subscribe===1 的用户，按 subscribe_scene 归组（缺失归入其他）
        const sceneMap = new Map<string, number>();
        items.filter((u) => u.subscribe === 1).forEach((u) => {
          const scene = String((u as WechatUserInfo).subscribe_scene || 'ADD_SCENE_OTHERS');
          sceneMap.set(scene, (sceneMap.get(scene) || 0) + 1);
        });
        setSceneStats({
          total: sceneMap.size,
          list: [...sceneMap.entries()]
            .map(([s, v]) => ({ name: SUBSCRIBE_SCENE_LABELS[s] ?? `未知(${s})`, value: v }))
            .sort((a, b) => b.value - a.value),
        });
        setUserError('');
      })
      .catch((e: Error) => {
        if (!mountedRef.current || silent) return;
        setUserStats(null);
        setSceneStats(null);
        setUserError(e?.message || '加载当前用户数据失败');
      })
      .finally(() => { if (mountedRef.current && !silent) setUserLoading(false); });
  }, []);

  // 日期变更立即查询（每 10 秒轮询两个接口功能已停用，见下方注释代码）
  useEffect(() => {
    if (!beginDate || !endDate) return;
    if (beginDate > endDate) {
      setList([]);
      setError('开始时间不能晚于结束时间');
      return;
    }
    loadSummary(false);
    // const id = window.setInterval(() => { loadSummary(true); loadUsers(true); }, POLL_INTERVAL);
    // return () => window.clearInterval(id);
  }, [beginDate, endDate, loadSummary, loadUsers]);

  useEffect(() => { loadUsers(false); }, [loadUsers]);

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
      xAxis: { type: 'category', data: dates, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e8eaea' } }, axisLabel: { ...axisLabel, formatter: (v: string) => v.slice(5) } },
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

  // 环形图：真实/虚拟用户构成，中心数字显示用户总数
  const donutOption = useMemo(() => ({
    color: [DONUT_COLOR_REAL, DONUT_COLOR_VIRTUAL],
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { data: ['真实用户', '虚拟用户'], bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: '#888d8f', fontSize: 10 } },
    title: {
      text: String(userStats?.total ?? 0),
      subtext: '当前用户数',
      left: 'center',
      top: '30%',
      itemGap: 2,
      textStyle: { fontSize: 22, fontWeight: 600, color: '#303435' },
      subtextStyle: { fontSize: 10, color: '#888d8f' },
    },
    series: [{
      type: 'pie',
      radius: ['46%', '68%'],
      center: ['50%', '42%'],
      data: [
        { name: '真实用户', value: userStats?.real ?? 0 },
        { name: '虚拟用户', value: userStats?.virtual ?? 0 },
      ],
      label: { color: '#888d8f', fontSize: 10, formatter: '{b} {c}' },
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
    }],
  }), [userStats]);

  // 用户来源分布饼图：最新快照全部关注用户的 subscribe_scene 分布（与筛选框无关，随当前用户构成同源刷新）
  const scenePieOption = useMemo(() => ({
    color: PIE_COLORS,
    tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
    legend: { type: 'scroll', bottom: 0, itemWidth: 10, itemHeight: 10, textStyle: { color: '#888d8f', fontSize: 10 } },
    series: [{
      type: 'pie',
      radius: ['30%', '55%'],
      center: ['50%', '40%'],
      data: sceneStats?.list || [],
      label: { color: '#888d8f', fontSize: 10, formatter: '{b} {c}' },
      itemStyle: { borderColor: '#fff', borderWidth: 2 },
    }],
  }), [sceneStats]);

  return (
    <div className="admin-view">
      <Navbar title="其他" leftArrow onLeftClick={() => navigate('/admin')} fixed />
      <div className="admin-entries-stats">
        <section className="admin-entries-stats__card">
          <div className="admin-entries-stats__head">
            <span className="admin-entries-stats__title">用户统计</span>
          </div>
          <div className="admin-entries-stats__groups">
            <section className="admin-entries-stats__group">
              <div className="admin-entries-stats__group-head">
                <div className="admin-entries-stats__filter">
                  <input
                    type="date"
                    className="admin-entries-stats__date"
                    value={beginDate}
                    max={yesterday}
                    onChange={(e) => handleBeginChange(e.target.value)}
                  />
                  <span className="admin-entries-stats__sep">至</span>
                  <input
                    type="date"
                    className="admin-entries-stats__date"
                    value={endDate}
                    min={beginDate || undefined}
                    max={yesterday}
                    onChange={(e) => handleEndChange(e.target.value)}
                  />
                  <button type="button" className="admin-entries-stats__reset" onClick={handleReset}>重置</button>
                </div>
              </div>
              <div className="admin-entries-stats__charts">
                <div className="admin-entries-stats__chart">
                  <span className="admin-entries-stats__sub">用户增减趋势</span>
                  {loading ? (
                    <div className="admin-entries-stats__empty"><Loading text="加载中..." /></div>
                  ) : error ? (
                    <div className="admin-entries-stats__empty">{error}</div>
                  ) : !hasData ? (
                    <div className="admin-entries-stats__empty">该时间段暂无用户增减数据</div>
                  ) : (
                    <ReactECharts option={barOption} style={{ height: 240 }} notMerge />
                  )}
                </div>
                <div className="admin-entries-stats__chart">
                  <span className="admin-entries-stats__sub">关注来源分布</span>
                  {loading ? (
                    <div className="admin-entries-stats__empty"><Loading text="加载中..." /></div>
                  ) : error ? (
                    <div className="admin-entries-stats__empty">{error}</div>
                  ) : !hasData ? (
                    <div className="admin-entries-stats__empty">该时间段暂无用户增减数据</div>
                  ) : (
                    <ReactECharts option={pieOption} style={{ height: 240 }} notMerge />
                  )}
                </div>
              </div>
            </section>
            <section className="admin-entries-stats__group">
              <div className="admin-entries-stats__charts">
                <div className="admin-entries-stats__chart">
                  <span className="admin-entries-stats__sub">当前用户构成</span>
                  {userLoading ? (
                    <div className="admin-entries-stats__empty"><Loading text="加载中..." /></div>
                  ) : userError ? (
                    <div className="admin-entries-stats__empty">{userError}</div>
                  ) : (
                    <ReactECharts option={donutOption} style={{ height: 240 }} notMerge />
                  )}
                </div>
                <div className="admin-entries-stats__chart">
                  <span className="admin-entries-stats__sub">用户来源分布</span>
                  {userLoading ? (
                    <div className="admin-entries-stats__empty"><Loading text="加载中..." /></div>
                  ) : userError ? (
                    <div className="admin-entries-stats__empty">{userError}</div>
                  ) : !sceneStats || sceneStats.list.length === 0 ? (
                    <div className="admin-entries-stats__empty">暂无关注用户来源数据</div>
                  ) : (
                    <ReactECharts option={scenePieOption} style={{ height: 240 }} notMerge />
                  )}
                </div>
              </div>
            </section>
          </div>
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
