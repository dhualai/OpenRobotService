// 责任模块树管理 —— 产品→界面→功能 三层树维护
// 背景：以「产品→界面→功能」树为派单主数据，工程师在此认领负责的功能。
// 功能节点可维护：关键词（识别相关问题）/ 功能描述 / 负责工程师。
// 保存：PUT /admin/module-tree 整体覆盖 DB + 导出 config.yaml + 通知 AI 热更新。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Toast, Loading, Dialog, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import MindmapView from '@/pages/admin/MindmapView';
import {
  MacPlus, MacX, MacSearch,
} from '@/shared/components/macaronIcons';

interface Engineer {
  id: string;
  username: string;
  name: string;
  department?: string;
  duty_text?: string;
}

interface FuncNode {
  key: string;
  name: string;
  keywords: string[];
  anchor?: string;
  engineers: string[];
}
interface InterfaceNode {
  key: string;
  name: string;
  description?: string;
  functions: FuncNode[];
}
type TreeMap = Record<string, { interfaces: InterfaceNode[] }>;

const EMPTY_TREE = { interfaces: [] as InterfaceNode[] };

const engName = (id: string, cands: Engineer[]) => {
  const found = cands.find((c) => c.id === id);
  return found ? found.name : id.slice(0, 8);
};

// ── key 说明 ──
// 界面/功能的 key（标识）由「后端保存时统一生成」：取中文名前两字拼音 + 短哈希，
// 保证格式统一、唯一、随中文名自动更新。前端无需自行生成，新增/改名时以中文名作临时 key，
// 保存后由后端重算覆盖。AI 派单归因不依赖 key（用 name/keywords/anchor/界面名）。

export default function ModuleTreeManage() {
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // ── 从「用户详情→责任模块」跳转的聚焦（先于 load 定义，避免 TDZ 报错）──
  const [searchParams] = useSearchParams();
  const focusUserId = searchParams.get('user') || '';
  const [highlightFns, setHighlightFns] = useState<Record<string, boolean>>({});

  // 主数据
  const [products, setProducts] = useState<string[]>([]);
  const [active, setActive] = useState<string>('');
  const [trees, setTrees] = useState<TreeMap>({});
  const [candidates, setCandidates] = useState<Engineer[]>([]);

  // 视图模式：single=单个产品编辑；overview=全产品总览（只读、全部展开）
  const [viewMode, setViewMode] = useState<'single' | 'overview'>('single');

  // 新关键词/锚输入缓存
  const [kwInputs, setKwInputs] = useState<Record<string, string>>({});
  const [newIfaceName, setNewIfaceName] = useState('');
  const [newFuncName, setNewFuncName] = useState<Record<string, string>>({});
  const [newProdName, setNewProdName] = useState('');

  // 工程师选择弹层状态
  const [pickEng, setPickEng] = useState<{ ifaceIdx: number; funcIdx: number } | null>(null);
  const [engSearch, setEngSearch] = useState('');

  // 树状折叠状态：界面默认展开；功能详情默认折叠（只显示功能名+负责人）
  const [collapsedIfaces, setCollapsedIfaces] = useState<Record<number, boolean>>({});
  const [expandedFns, setExpandedFns] = useState<Record<string, boolean>>({});

  // 切换产品时：界面全部展开，功能详情全部折叠
  useEffect(() => {
    setCollapsedIfaces({});
    setExpandedFns({});
  }, [active]);

  // 从用户详情聚焦：展开并高亮该用户负责的功能
  useEffect(() => {
    if (!focusUserId) { setHighlightFns({}); return; }
    const hl: Record<string, boolean> = {};
    const ifaceList = trees[active]?.interfaces || [];
    for (let i = 0; i < ifaceList.length; i++) {
      const funcs = ifaceList[i].functions || [];
      for (let j = 0; j < funcs.length; j++) {
        if ((funcs[j].engineers || []).map(String).includes(String(focusUserId))) {
          const key = `${i}-${j}`;
          hl[key] = true;
          setExpandedFns((prev) => ({ ...prev, [key]: true }));
        }
      }
    }
    setHighlightFns(hl);
  }, [focusUserId, active, trees]);

  // 功能负责人标签：无负责人显示「待分配」
  const fnOwnerLabel = (fn: FuncNode) => {
    if (!fn.engineers || fn.engineers.length === 0) return '待分配';
    return fn.engineers.map((id) => engName(id, candidates)).join('、');
  };

  // ── 编辑权限 ──
  const storeUserId = useAuthStore((s) => s.userId);
  const [perm, setPerm] = useState<{ user_id: string; is_privileged: boolean } | null>(null);
  useEffect(() => {
    request<{ user_id: string; is_privileged: boolean }>('/module-tree/permission')
      .then((p) => setPerm(p))
      .catch(() => setPerm(null));
  }, [request]);

  // 判断当前用户是否能直接编辑某功能：白名单/本人负责/待分配 → 可编辑
  const canEditFn = (fn: FuncNode) => {
    if (perm?.is_privileged) return true;
    const engs = (fn.engineers || []).map(String);
    if (engs.length === 0) return true; // 待分配：任意登录用户可编辑
    const myId = String(perm?.user_id || storeUserId || '');
    return myId !== '' && engs.includes(myId);
  };

  // 指定负责工程师（仅管理员/特殊权限可改负责人分配；普通用户改不了他人模块）
  const canAssignEng = !!perm?.is_privileged;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [treeData, prodData, candData] = await Promise.all([
        request<TreeMap>('/module-tree/'),
        request<string[]>('/module-tree/products'),
        request<Engineer[]>('/module-tree/candidates'),
      ]);
      setTrees(treeData || {});
      setProducts(prodData || []);
      setCandidates(candData || []);
      if ((prodData?.length ?? 0) > 0) {
        // 有聚焦用户时，优先进入其负责的产品
        if (focusUserId) {
          let focusProduct = '';
          for (const [prod, body] of Object.entries(treeData || {})) {
            for (const iface of (body as any)?.interfaces || []) {
              for (const fn of iface.functions || []) {
                if ((fn.engineers || []).map(String).includes(String(focusUserId))) {
                  focusProduct = prod;
                  break;
                }
              }
              if (focusProduct) break;
            }
            if (focusProduct) break;
          }
          if (focusProduct) { setActive(focusProduct); return; }
        }
        setActive((prev) => (prev && prodData.includes(prev) ? prev : prodData[0]));
      } else {
        setActive('');
      }
    } catch (e) {
      Toast({ message: '加载失败', theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [request, focusUserId]);

  useEffect(() => { load(); }, [load]);

  const activeTree = trees[active] || EMPTY_TREE;

  // ── 增量保存用状态 ──
  // dirtyProducts：本次被修改过、待保存的产品集合；removedProducts：本次待删除的产品
  const [dirtyProducts, setDirtyProducts] = useState<Set<string>>(new Set());
  const [removedProducts, setRemovedProducts] = useState<string[]>([]);
  const markDirty = (name: string) => {
    if (!name) return;
    setDirtyProducts((prev) => {
      const next = new Set(prev);
      next.add(name);
      return next;
    });
  };

  const setActiveTree = (updater: (t: { interfaces: InterfaceNode[] }) => { interfaces: InterfaceNode[] }) => {
    if (!active) return;
    markDirty(active);
    setTrees((prev) => {
      const cur = prev[active] || { interfaces: [] };
      const next = updater(cur);
      return { ...prev, [active]: next };
    });
  };

  // ── 界面 CRUD ──
  const addInterface = () => {
    const name = newIfaceName.trim();
    if (!name) { Toast({ message: '请输入界面名称', theme: 'warning' }); return; }
    setActiveTree((t) => ({
      interfaces: [...t.interfaces, { key: name, name, functions: [] }],
    }));
    setNewIfaceName('');
  };
  const removeInterface = (i: number) => {
    setActiveTree((t) => ({ interfaces: t.interfaces.filter((_, idx) => idx !== i) }));
  };
  const renameInterface = (i: number, name: string) => {
    setActiveTree((t) => ({
      interfaces: t.interfaces.map((iface, idx) => (idx === i ? { ...iface, name, key: name } : iface)),
    }));
  };

  // ── 功能 CRUD（key 用中文名作临时值，保存时由后端统一重算为拼音+哈希）──
  const addFunc = (i: number) => {
    const name = (newFuncName[`${i}`] || '').trim();
    if (!name) { Toast({ message: '请输入功能名称', theme: 'warning' }); return; }
    setActiveTree((t) => ({
      interfaces: t.interfaces.map((iface, idx) =>
        idx === i ? { ...iface, functions: [...iface.functions, { key: name, name, keywords: [], engineers: [] }] } : iface),
    }));
    setNewFuncName((prev) => ({ ...prev, [`${i}`]: '' }));
  };
  const removeFunc = (i: number, j: number) => {
    setActiveTree((t) => ({
      interfaces: t.interfaces.map((iface, idx) =>
        idx === i ? { ...iface, functions: iface.functions.filter((_, jdx) => jdx !== j) } : iface),
    }));
  };
  const renameFunc = (i: number, j: number, name: string) => {
    setActiveTree((t) => ({
      interfaces: t.interfaces.map((iface, idx) =>
        idx === i
          ? { ...iface, functions: iface.functions.map((fn, jdx) => (jdx === j ? { ...fn, name, key: name } : fn)) }
          : iface),
    }));
  };
  const updateFuncField = (i: number, j: number, patch: Partial<FuncNode>) => {
    setActiveTree((t) => ({
      interfaces: t.interfaces.map((iface, idx) =>
        idx === i
          ? { ...iface, functions: iface.functions.map((fn, jdx) => (jdx === j ? { ...fn, ...patch } : fn)) }
          : iface),
    }));
  };

  // 关键词
  const addKeyword = (i: number, j: number) => {
    const key = `${i}-${j}`;
    const val = (kwInputs[key] || '').trim();
    if (!val) return;
    updateFuncField(i, j, { keywords: [...(trees[active]?.interfaces?.[i]?.functions?.[j]?.keywords || []), val] });
    setKwInputs((prev) => ({ ...prev, [key]: '' }));
  };
  const removeKeyword = (i: number, j: number, k: number) => {
    const fn = trees[active]?.interfaces?.[i]?.functions?.[j];
    if (!fn) return;
    updateFuncField(i, j, { keywords: fn.keywords.filter((_, kdx) => kdx !== k) });
  };

  // ── 产品 CRUD ──
  const addProduct = () => {
    const name = newProdName.trim();
    if (!name) { Toast({ message: '请输入产品名', theme: 'warning' }); return; }
    if (products.includes(name)) { Toast({ message: '产品已存在', theme: 'warning' }); return; }
    // 若该产品曾标记删除又重新添加，则取消删除标记
    setRemovedProducts((prev) => prev.filter((p) => p !== name));
    markDirty(name);
    setProducts((prev) => [...prev, name]);
    setTrees((prev) => ({ ...prev, [name]: { interfaces: [] } }));
    setActive(name);
    setNewProdName('');
  };
  // 删除产品：仅特权用户可用；确认后加入待删除清单并从本地移除，保存(增量 PUT)时从 DB 删掉该产品
  const removeProduct = (name: string) => {
    if (!perm?.is_privileged) { Toast({ message: '无权限删除产品', theme: 'warning' }); return; }
    const ifaceCount = trees[name]?.interfaces?.length || 0;
    const tip = ifaceCount > 0
      ? `确定删除产品「${name}」吗？它包含 ${ifaceCount} 个界面，删除后需点击「保存并生效」才会真正落库。`
      : `确定删除产品「${name}」吗？删除后需点击「保存并生效」才会真正落库。`;
    if (!window.confirm(tip)) return;
    setRemovedProducts((prev) => (prev.includes(name) ? prev : [...prev, name]));
    setDirtyProducts((prev) => { const n = new Set(prev); n.delete(name); return n; });
    setProducts((prev) => prev.filter((p) => p !== name));
    setTrees((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    setActive((cur) => (cur === name ? '' : cur));
    Toast({ message: '已标记删除，保存后生效', theme: 'success' });
  };

  // ── 保存（手动增量：只提交改动过的产品 + 待删除产品，避免覆盖他人改动的其它产品）──
  const handleSave = async () => {
    // 待保存产品 = dirty 且未被标记删除
    const dirtyNames = [...dirtyProducts].filter((p) => !removedProducts.includes(p));
    if (dirtyNames.length === 0 && removedProducts.length === 0) {
      Toast({ message: '没有需要保存的修改', theme: 'warning' });
      return;
    }
    setSaving(true);
    try {
      const products: Record<string, unknown> = {};
      for (const name of dirtyNames) {
        products[name] = trees[name] || { interfaces: [] };
      }
      const res = await request<{ code: number; message: string; ai_reload?: string; rejected?: string }>('/module-tree/', {
        method: 'PUT',
        body: JSON.stringify({ products, removed: removedProducts }),
      });
      // 保存成功：清空增量标记
      setDirtyProducts(new Set());
      setRemovedProducts([]);
      Toast({ message: res?.message || '保存成功', theme: 'success' });
    } catch (e) {
      Toast({ message: '保存失败', theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  // Ctrl+S 快捷保存：始终引用最新 handleSave，避免闭包过期
  const handleSaveRef = useRef(handleSave);
  useEffect(() => { handleSaveRef.current = handleSave; }, [handleSave]);
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        handleSaveRef.current();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const filteredCands = candidates.filter((c) =>
    !engSearch || c.name.toLowerCase().includes(engSearch.toLowerCase()) || (c.department || '').includes(engSearch));

  // 候选分组：所有候选按「有部门 → 归部门分组；无部门 → 其他账号」，不按 username 过滤。
  const withDept = filteredCands.filter((c) => (c.department || '').trim());
  const noDeptCands = filteredCands.filter((c) => !(c.department || '').trim());
  const deptGroups: { dept: string; list: Engineer[] }[] = [];
  for (const c of withDept) {
    const dept = (c.department || '').trim();
    let g = deptGroups.find((x) => x.dept === dept);
    if (!g) { g = { dept, list: [] }; deptGroups.push(g); }
    g.list.push(c);
  }
  deptGroups.sort((a, b) => a.dept.localeCompare(b.dept, 'zh-Hans-CN'));
  const [showWechatGroup, setShowWechatGroup] = useState(false);

  const renderEngChips = (ifaceIdx: number, funcIdx: number, fn: FuncNode, canEdit: boolean, canAssign: boolean) => (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '4px 8px' }}>
      {/* 已认领：展示负责工程师 */}
      {fn.engineers.map((eid, k) => (
        <span key={k} className="mac-kwchip">
          {engName(eid, candidates)}
          {canEdit && canAssign && (
            <span
              className="mac-kwchip__remove"
              onClick={() => updateFuncField(ifaceIdx, funcIdx, { engineers: fn.engineers.filter((_, kdx) => kdx !== k) })}
            >×</span>
          )}
        </span>
      ))}
      {/* 分配入口：仅待分配（无人认领）时显示；已认领后不再显示「指定负责工程师」 */}
      {canEdit && canAssign && (!fn.engineers || fn.engineers.length === 0) && (
        <span className="mac-chip mac-chip--tag-blue" onClick={() => setPickEng({ ifaceIdx, funcIdx })}>
          指定负责工程师 +
        </span>
      )}
      {!canEdit && fn.engineers && fn.engineers.length === 0 && (
        <span className="mac-chip mac-chip--tag-muted" style={{ fontSize: 11 }}>
          待分配
        </span>
      )}
    </div>
  );

  if (loading) {
    return (
      <div className="mac-page" style={{ display: 'flex', justifyContent: 'center', paddingTop: 60 }}>
        <Loading text="加载中..." />
      </div>
    );
  }

  return (
    <div className="mac-page" style={{ padding: 12, paddingBottom: 60 }}>
      {/* 顶部固定标题栏：滚动时不随页面移动，保存按钮始终可见 */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 50,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        margin: '-3px -12px 12px', padding: '10px 12px',
        background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(8px)',
        borderBottom: '1px solid var(--mac-border)',
      }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>责任模块树</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className={`mac-chip ${viewMode === 'overview' ? 'mac-chip--tag-blue' : 'mac-chip--soft'}`}
            onClick={() => setViewMode(viewMode === 'overview' ? 'single' : 'overview')}
          >
            {viewMode === 'overview' ? '退出总览' : '🗺 总览'}
          </button>
          <button className="mac-btn mac-btn--primary" onClick={() => handleSave()} disabled={saving}>
            {saving ? '保存中…' : '保存并生效'}
          </button>
        </div>
      </div>

      {/* ───────────── 单产品总览（左右对称延伸，全部功能一眼可见） ───────────── */}
      {viewMode === 'overview' && (
        active ? (
          <MindmapView productName={active} interfaces={trees[active]?.interfaces || []} candidates={candidates} />
        ) : (
          <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>请先选择产品</div>
        )
      )}

      {/* 单产品编辑视图：产品选择器仅编辑模式显示 */}
      {viewMode === 'single' && (
      <>
      {/* 产品选择器 */}
      <div className="mac-tree-add" style={{ marginBottom: 12 }}>
        <span className="mac-tree-add__icon"><MacPlus size={16} /></span>
        <input
          placeholder="新增产品名（如 调度USP / 摇人吧服务号）"
          value={newProdName}
          onChange={(e) => setNewProdName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { addProduct(); } }}
        />
        <button type="button" className="mac-tree-add__btn" onClick={addProduct}>新增</button>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
        {products.map((p) => (
          <span
            key={p}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
          >
            <button
              type="button"
              className={`mac-chip ${p === active ? 'mac-chip--tag-blue' : 'mac-chip--soft'}`}
              onClick={() => setActive(p)}
            >{p}</button>
            {perm?.is_privileged && (
              <button
                type="button"
                className="mac-btn mac-btn--ghost mac-iface-row__del"
                style={{ padding: 0, lineHeight: 1, fontSize: 13 }}
                title={`删除产品「${p}」`}
                onClick={(e) => { e.stopPropagation(); removeProduct(p); }}
              ><MacX size={13} /></button>
            )}
          </span>
        ))}
      </div>

      {!active ? (
        <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>暂无产品，请先新增或刷新</div>
      ) : (
        <>
          {/* 新增界面 */}
          <div className="mac-tree-add" style={{ marginBottom: 10 }}>
            <span className="mac-tree-add__icon"><MacPlus size={16} /></span>
            <input
              placeholder="新增界面名称，回车确定"
              value={newIfaceName}
              onChange={(e) => setNewIfaceName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { addInterface(); } }}
            />
            <button type="button" className="mac-tree-add__btn" onClick={addInterface}>添加</button>
          </div>

          {/* 界面树：每个界面为可折叠节点，功能缩进嵌套 */}
          {activeTree.interfaces.map((iface, i) => {
            const ifaceCollapsed = !!collapsedIfaces[i];
            return (
              <div key={i} style={{ marginBottom: 10 }}>
                {/* 界面行 */}
                <div
                  className="mac-iface-row"
                  onClick={() => setCollapsedIfaces((prev) => ({ ...prev, [i]: !prev[i] }))}
                >
                  <span className={`mac-tree-chevron mac-iface-row__icon ${ifaceCollapsed ? '' : 'mac-tree-chevron--open'}`}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>
                  </span>
                  <svg className="mac-iface-row__icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z"/></svg>
                  <input
                    className="mac-iface-row__name"
                    value={iface.name}
                    onChange={(e) => renameInterface(i, e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                  />
                  <span className="mac-iface-row__badge">{iface.functions.length} 功能</span>
                  <button type="button" className="mac-btn mac-btn--ghost mac-iface-row__del" onClick={(e) => { e.stopPropagation(); removeInterface(i); }}>
                    <MacX size={14} />
                  </button>
                </div>

                {/* 界面展开：功能树（缩进） */}
                {!ifaceCollapsed && (
                  <div className="mac-fn-tree">
                    {iface.functions.map((fn, j) => {
                      const fnKey = `${i}-${j}`;
                      const fnExpanded = !!expandedFns[fnKey];
                      const canEdit = canEditFn(fn);
                      const ownerNames = (fn.engineers || []).map((id) => engName(id, candidates)).join('、');
                      const isFocus = !!highlightFns[fnKey];
                      return (
                        <div key={j} className={`mac-fn-node${isFocus ? ' mac-fn-node--hl' : ''}`}>
                          <div className="mac-fn-node__head" onClick={() => setExpandedFns((prev) => ({ ...prev, [fnKey]: !prev[fnKey] }))}>
                            <span className="mac-fn-node__dot" />
                            <input
                              className={`mac-fn-node__name${canEdit ? '' : ' mac-fn-node__name--locked'}`}
                              value={fn.name}
                              disabled={!canEdit}
                              onChange={(e) => renameFunc(i, j, e.target.value)}
                              onClick={(e) => e.stopPropagation()}
                              title={canEdit ? '' : `由 ${ownerNames} 负责，需其同意后才能修改`}
                            />
                            {!canEdit && (
                              <span className="mac-fn-lock" title={`由 ${ownerNames} 负责`}>
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                              </span>
                            )}
                            <span className={`mac-fn-owner ${fn.engineers && fn.engineers.length > 0 ? 'mac-fn-owner--assigned' : ''}`}>
                              {fn.engineers && fn.engineers.length > 0 && (
                                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                              )}
                              {fnOwnerLabel(fn)}
                            </span>
                            {canEdit && (
                              <button type="button" className="mac-btn mac-btn--ghost mac-fn-node__del" onClick={(e) => { e.stopPropagation(); removeFunc(i, j); }}>
                                <MacX size={13} />
                              </button>
                            )}
                          </div>

                          {fnExpanded && (
                            <div className="mac-fn-node__body">
                              {/* keywords */}
                              <div className="mac-field-label">关键词</div>
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                {fn.keywords.map((k, kdx) => (
                                  <span key={kdx} className="mac-kwchip">
                                    {k}
                                    {canEdit && <span className="mac-kwchip__remove" onClick={() => removeKeyword(i, j, kdx)}>×</span>}
                                  </span>
                                ))}
                              </div>
                              {/* 添加关键词 */}
                              {canEdit && (
                                <div className="mac-tree-add mac-tree-add--sm" style={{ marginTop: 6 }}>
                                  <span className="mac-tree-add__icon"><MacPlus size={13} /></span>
                                  <input
                                    placeholder="添加关键词，回车"
                                    value={kwInputs[`${i}-${j}`] || ''}
                                    onChange={(e) => setKwInputs((prev) => ({ ...prev, [`${i}-${j}`]: e.target.value }))}
                                    onKeyDown={(e) => { if (e.key === 'Enter') { addKeyword(i, j); } }}
                                  />
                                  <button type="button" className="mac-tree-add__btn" onClick={() => addKeyword(i, j)}>+</button>
                                </div>
                              )}

                              {/* anchor */}
                              <div className="mac-field-label">功能描述</div>
                              <input
                                className="mac-input"
                                placeholder="一句语义描述，逗号分隔"
                                value={fn.anchor || ''}
                                disabled={!canEdit}
                                onChange={(e) => updateFuncField(i, j, { anchor: e.target.value })}
                              />

                              {/* engineers */}
                              <div className="mac-field-label">负责工程师</div>
                              {renderEngChips(i, j, fn, canEdit, canAssignEng)}
                            </div>
                          )}
                        </div>
                      );
                    })}

                    {/* 新增功能 */}
                    <div className="mac-tree-add">
                      <span className="mac-tree-add__icon"><MacPlus size={14} /></span>
                      <input
                        placeholder="新增功能名称，回车确定"
                        value={newFuncName[`${i}`] || ''}
                        onChange={(e) => setNewFuncName((prev) => ({ ...prev, [`${i}`]: e.target.value }))}
                        onKeyDown={(e) => { if (e.key === 'Enter') { addFunc(i); } }}
                      />
                      <button type="button" className="mac-tree-add__btn" onClick={() => addFunc(i)}>添加</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </>
      )}
      </>
      )}

      {/* 工程师选择弹层 */}
      <Popup
        visible={pickEng !== null}
        placement="bottom"
        showOverlay
        onVisibleChange={(v) => { if (!v) setPickEng(null); }}
      >
        <div className="mac-sheet" style={{ padding: 16, maxHeight: '60vh', overflowY: 'auto' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
            <h3 style={{ margin: 0 }}>选择负责工程师</h3>
            <button type="button" className="mac-btn mac-btn--ghost" onClick={() => setPickEng(null)}>
              <MacX size={14} /> 关闭
            </button>
          </div>
          <div className="mac-search" style={{ marginBottom: 10 }}>
            <MacSearch size={16} />
            <input placeholder="搜索姓名/部门" value={engSearch} onChange={(e) => setEngSearch(e.target.value)} />
          </div>
          <div>
            {filteredCands.length === 0 && <div style={{ color: '#999', padding: 12 }}>无匹配工程师</div>}

            {/* 真工程师：按部门分组 */}
            {deptGroups.map((g) => (
              <div key={g.dept}>
                <div className="mac-eng-group__label">
                  {g.dept}
                  <span className="count">{g.list.length} 人</span>
                </div>
                {g.list.map((c) => {
                  const ifaceIdx = pickEng?.ifaceIdx ?? 0;
                  const funcIdx = pickEng?.funcIdx ?? 0;
                  const fn = trees[active]?.interfaces?.[ifaceIdx]?.functions?.[funcIdx];
                  const selected = fn?.engineers?.includes(c.id) || false;
                  return (
                    <div
                      key={c.id}
                      className={`mac-eng-row${selected ? ' mac-eng-row--sel' : ''}`}
                      onClick={() => {
                        const cur = fn?.engineers || [];
                        const next = selected ? cur.filter((x) => x !== c.id) : [...cur, c.id];
                        updateFuncField(ifaceIdx, funcIdx, { engineers: next });
                        if (!selected) setPickEng(null); // 添加后自动收回弹层
                      }}
                    >
                      <span className="mac-eng-row__avatar">{c.name ? c.name.trim().charAt(0) : '?'}</span>
                      <div className="mac-eng-row__main">
                        <div className="mac-eng-row__name">{c.name}</div>
                        <div className="mac-eng-row__sub">{c.username}{c.department ? ` · ${c.department}` : ''}</div>
                      </div>
                      <span className="mac-eng-row__check">
                        {selected && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5"/></svg>}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))}

            {/* 无部门账号：折叠组 */}
            {noDeptCands.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div className="mac-eng-group__label" style={{ cursor: 'pointer' }} onClick={() => setShowWechatGroup(!showWechatGroup)}>
                  <span className={`mac-tree-chevron ${showWechatGroup ? 'mac-tree-chevron--open' : ''}`} style={{ color: 'var(--mac-muted-fg)' }}>
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m9 18 6-6-6-6"/></svg>
                  </span>
                  其他账号
                  <span className="count">{noDeptCands.length} 人</span>
                </div>
                {showWechatGroup && noDeptCands.map((c) => {
                  const ifaceIdx = pickEng?.ifaceIdx ?? 0;
                  const funcIdx = pickEng?.funcIdx ?? 0;
                  const fn = trees[active]?.interfaces?.[ifaceIdx]?.functions?.[funcIdx];
                  const selected = fn?.engineers?.includes(c.id) || false;
                  return (
                    <div
                      key={c.id}
                      className={`mac-eng-row${selected ? ' mac-eng-row--sel' : ''}`}
                      onClick={() => {
                        const cur = fn?.engineers || [];
                        const next = selected ? cur.filter((x) => x !== c.id) : [...cur, c.id];
                        updateFuncField(ifaceIdx, funcIdx, { engineers: next });
                        if (!selected) setPickEng(null); // 添加后自动收回弹层
                      }}
                    >
                      <span className="mac-eng-row__avatar" style={{ background: 'var(--mac-muted-fg)' }}>{c.name ? c.name.trim().charAt(0) : '?'}</span>
                      <div className="mac-eng-row__main">
                        <div className="mac-eng-row__name">{c.name}</div>
                        <div className="mac-eng-row__sub">{c.username}</div>
                      </div>
                      <span className="mac-eng-row__check">
                        {selected && <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6 9 17l-5-5"/></svg>}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </Popup>
    </div>
  );
}
