// 责任模块树管理 —— 产品→界面→功能 三层树维护
// 背景：以「产品→界面→功能」树为派单主数据，工程师在此认领负责的功能。
// 功能节点可维护：关键词（识别相关问题）/ 功能描述 / 负责工程师。
// 保存：每行（每个功能）即时 PUT / DELETE /admin/module-tree/node，按行 id 定位，
// 多人改不同行天然互不覆盖（并发安全核心）；写库后后端自动导出 config.yaml + 通知 AI 热更新。
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Toast, Loading, Popup, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { buildWsUrl } from '@/api/ws';
import { useAuthStore } from '@/stores/auth';
import MindmapView from '@/pages/admin/MindmapView';
import DepartmentProfileManager from '@/pages/admin/DepartmentProfileManager';
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
  id?: number;           // 行主键。新增未落库时为 undefined，PUT 后用返回的 id 绑定
  key?: string;          // 后端聚合时生成的展示 key（MindmapView 需用，编辑流程不用）
  name: string;
  keywords: string[];
  anchor?: string;
  engineers: string[];
  iface_order: number;
  func_order: number;
  iface_name?: string;   // 冗余：便于重排/重命名界面时按原值定位
}
interface InterfaceNode {
  key?: string;
  name: string;
  functions: FuncNode[];
}
type TreeMap = Record<string, { interfaces: InterfaceNode[] }>;
type PersistRes = { code: number; id?: number; message?: string };

const EMPTY_TREE = { interfaces: [] as InterfaceNode[] };

const engName = (id: string, cands: Engineer[]) => {
  const found = cands.find((c) => c.id === id);
  return found ? found.name : id.slice(0, 8);
};

// ── 行级合并：以 remote（他人最新）为权威，保留本地「有未保存差异的行」 ──
// 按界面名分组对齐；功能行按行 id 对齐：
//   - remote 有、local 有且内容一致  → 用 remote
//   - remote 有、local 无           → 新增（远端加的）
//   - remote 有、local 有但内容不同  → 保留 local（本地有未保存编辑，交给冲突弹窗）
//   - remote 无、local 有 id        → 删除（远端删的）
//   - local 无 id（本地新插入未落库）→ 保留
function sameFn(a: FuncNode, b: FuncNode): boolean {
  const pick = (f: FuncNode) => JSON.stringify([
    f.name, f.keywords || [], f.anchor || '', f.engineers || [], f.iface_order, f.func_order,
  ]);
  return pick(a) === pick(b);
}
function mergeProductTree(
  local: { interfaces: InterfaceNode[] },
  remote: { interfaces: InterfaceNode[] },
): InterfaceNode[] {
  const lmap = new Map((local?.interfaces || []).map((it) => [it?.name || '', it]));
  const out: InterfaceNode[] = [];
  for (const rit of remote?.interfaces || []) {
    const name = rit?.name || '';
    const lin = lmap.get(name);
    const fns: FuncNode[] = [];
    for (const rf of rit.functions || []) {
      const lf = lin?.functions?.find((f) => f.id != null && rf.id != null && f.id === rf.id);
      fns.push(lf && !sameFn(lf, rf) ? { ...lf } : { ...rf });
    }
    // 本地新插入未落库的行（无 id）保留
    for (const lf of lin?.functions || []) {
      if (lf.id == null && !fns.some((f) => sameFn(f, lf))) fns.push({ ...lf });
    }
    out.push({ key: rit.key, name, functions: fns });
  }
  return out;
}

// ── key 说明 ──
// 界面/功能的 key（标识）由「后端聚合时统一生成」：取中文名前两字拼音 + 短哈希。
// 编辑流程不依赖 key（用「行 id + 界面名 + 功能名」定位与保存），仅保留给总览图 MindmapView 展示用。

export default function ModuleTreeManage() {
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);
  const [loading, setLoading] = useState(true);

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
  // 顶层标签：tree=责任模块树；dept=部门职责画像管理（AI 派单部门分类用，与责任树互不影响）
  const [tab, setTab] = useState<'tree' | 'dept'>('tree');

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
      treesRef.current = treeData || {};
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

  // ── 行级持久化工具（每行即时保存）──
  // treesRef：为 persistence 读取当前树提供稳定引用（函数式 setTrees 之外）。
  const treesRef = useRef(trees);
  useEffect(() => { treesRef.current = trees; }, [trees]);

  // mutateTree：以 treesRef 为同步真源，更新当前产品树并同步到 React state。
  // 所有树变更（含 applyFn/增删界面功能/加载）都必须走它，保证后续按 treesRef 读到的即最新。
  const mutateTree = (updater: (t: { interfaces: InterfaceNode[] }) => { interfaces: InterfaceNode[] }) => {
    if (!active) return;
    const cur = treesRef.current[active] || { interfaces: [] };
    const nextAll = { ...treesRef.current, [active]: updater(cur) };
    treesRef.current = nextAll;
    setTrees(nextAll);
  };

  // applyFn：更新本地树中某个功能行（乐观 UI）；bindId：落库后把返回的行 id 绑定到本地行。
  const applyFn = (ifaceIdx: number, funcIdx: number, updater: (fn: FuncNode) => FuncNode) => {
    mutateTree((tree) => ({
      interfaces: tree.interfaces.map((iface, i) =>
        i !== ifaceIdx ? iface : { ...iface, functions: iface.functions.map((fn, j) => (j !== funcIdx ? fn : updater(fn))) }),
    }));
  };
  const bindId = (ifaceIdx: number, funcIdx: number, id: number) => {
    applyFn(ifaceIdx, funcIdx, (fn) => (fn.id === id ? fn : { ...fn, id }));
  };
  const currentFn = (ifaceIdx: number, funcIdx: number): FuncNode | null =>
    treesRef.current[active]?.interfaces?.[ifaceIdx]?.functions?.[funcIdx] ?? null;

  // 文本输入防抖定时器（按 产品/界面idx/功能idx 维度），避免每次按键都打接口
  const persistTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const clearTimer = (key: string) => {
    const t = persistTimers.current[key];
    if (t) { clearTimeout(t); delete persistTimers.current[key]; }
  };

  // ── 实时协同：订阅他人对责任树的修改（WS 广播 → 行级合并 → 冲突弹窗）──
  const myUsername = useAuthStore((s) => s.username);
  const activeRef = useRef(active);
  const wsRef = useRef<WebSocket | null>(null);
  const wsReconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => { activeRef.current = active; }, [active]);

  // 拉取某产品最新树并与本地行级合并（远端为权威，保留本地未保存行）
  const applyRemoteProduct = useCallback(async (product: string) => {
    try {
      const fresh = await request<TreeMap>('/module-tree/');
      const remote = fresh?.[product];
      if (!remote) return;
      const local = treesRef.current[product] || { interfaces: [] };
      const merged = mergeProductTree(local, remote);
      const nextAll = { ...treesRef.current, [product]: { interfaces: merged } };
      treesRef.current = nextAll;
      setTrees(nextAll);
    } catch {
      /* 拉取失败忽略，下次广播再试 */
    }
  }, [request]);

  const confirmRefresh = useCallback((product: string, by: string) => {
    Dialog.confirm?.({
      title: '责任树已被他人修改',
      content: `${by} 刚修改了「${product}」。你本地有未保存的编辑，是否刷新为最新？`,
      confirmBtn: '刷新',
      cancelBtn: '保留我的',
      onConfirm: () => { void applyRemoteProduct(product); },
    });
  }, [applyRemoteProduct]);

  useEffect(() => {
    let closedByUser = false;

    const handleMessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data) as { type?: string; product?: string; by?: string };
        if (msg.type !== 'module_tree.updated') return;
        if (msg.by && msg.by === myUsername) return; // 自己的改动本地已乐观更新
        const product = String(msg.product || '');
        if (!product) return;
        // 正在编辑该产品且本地有未落库编辑（防抖定时器在跑）→ 冲突弹窗；否则静默合并
        const editing = product === activeRef.current
          && Object.keys(persistTimers.current).some((k) => k.startsWith(`${product}::`));
        if (editing) {
          confirmRefresh(product, String(msg.by || '对方'));
        } else {
          void applyRemoteProduct(product);
        }
      } catch { /* 忽略非法帧 */ }
    };

    const connect = (attempt: number) => {
      if (closedByUser) return;
      const ws = new WebSocket(buildWsUrl('/api/admin/module-tree/ws'));
      wsRef.current = ws;
      ws.onmessage = handleMessage;
      ws.onclose = () => {
        if (closedByUser) return;
        // 指数退避重连（3s、6s、12s…上限 60s）
        const delay = Math.min(3000 * (attempt + 1), 60000);
        wsReconnectRef.current = setTimeout(() => connect(attempt + 1), delay);
      };
    };
    connect(0);
    return () => {
      closedByUser = true;
      if (wsReconnectRef.current) clearTimeout(wsReconnectRef.current);
      wsRef.current?.close();
    };
  }, [myUsername, applyRemoteProduct, confirmRefresh]);

  // 立即落库一个功能行（update：必须有 id；add/rename 等新建走 persistRowObject）
  const persistNow = async (ifaceIdx: number, funcIdx: number) => {
    const iface = treesRef.current[active]?.interfaces?.[ifaceIdx];
    const fn = iface?.functions?.[funcIdx];
    if (!active || !iface || !fn) return;
    if (fn.id === undefined) return; // 尚未落库绑定的新行：跳过（防止重复插入）
    try {
      await request<PersistRes>('/module-tree/node', {
        method: 'PUT',
        body: JSON.stringify({
          id: fn.id, product: active, iface_name: iface.name,
          iface_order: fn.iface_order, func_name: fn.name, func_order: fn.func_order,
          keywords: fn.keywords || [], anchor: fn.anchor || '', engineers: fn.engineers || [],
        }),
      });
    } catch (e) {
      Toast({ message: '保存失败，请重试', theme: 'error' });
    }
  };

  // 防抖落库（文本输入用）
  const persistDebounced = (ifaceIdx: number, funcIdx: number, delay = 600) => {
    if (!active) return;
    const key = `${active}::${ifaceIdx}::${funcIdx}`;
    clearTimer(key);
    persistTimers.current[key] = setTimeout(() => {
      delete persistTimers.current[key];
      void persistNow(ifaceIdx, funcIdx);
    }, delay);
  };

  // 新建功能行落库（PUT 无 id → insert），成功后把返回 id 绑定到本地行
  const persistRowObject = async (ifaceIdx: number, funcIdx: number, fn: FuncNode, ifaceName: string) => {
    try {
      const res = await request<PersistRes>('/module-tree/node', {
        method: 'PUT',
        body: JSON.stringify({
          id: fn.id, product: active, iface_name: ifaceName,
          iface_order: fn.iface_order, func_name: fn.name, func_order: fn.func_order,
          keywords: fn.keywords || [], anchor: fn.anchor || '', engineers: fn.engineers || [],
        }),
      });
      if (fn.id === undefined && res?.id) bindId(ifaceIdx, funcIdx, res.id);
    } catch (e) {
      Toast({ message: '保存失败，请重试', theme: 'error' });
    }
  };

  // 批量删除若干功能行
  const deleteNodeIds = async (ids: number[]) => {
    if (!ids.length) return;
    try {
      await request<{ code: number }>('/module-tree/node', {
        method: 'DELETE',
        body: JSON.stringify({ ids }),
      });
    } catch (e) {
      Toast({ message: '删除失败', theme: 'error' });
    }
  };

  // ── 界面 CRUD ──
  // 界面是行模型的「分组视图」，由功能行的 iface_name 聚合而来，本身不单独存行；
  // 因此界面必须至少含一个功能才能持久化。新增界面只在本地建空分组，加入功能后才真正落库。
  const addInterface = () => {
    const name = newIfaceName.trim();
    if (!name) { Toast({ message: '请输入界面名称', theme: 'warning' }); return; }
    mutateTree((t) => ({ interfaces: [...t.interfaces, { name, functions: [] }] }));
    setNewIfaceName('');
  };
  const removeInterface = (i: number) => {
    // 界面由功能行聚合而来 → 删除界面 = 批量删除其下所有功能行
    const ids = (treesRef.current[active]?.interfaces?.[i]?.functions || [])
      .map((f) => f.id).filter((x): x is number => typeof x === 'number');
    void deleteNodeIds(ids);
    mutateTree((t) => ({ interfaces: t.interfaces.filter((_, idx) => idx !== i) }));
  };
  const renameInterface = (i: number, name: string) => {
    // 界面改名 = 把该界面下所有功能行的 iface_name 批量改为新名
    const funcs = treesRef.current[active]?.interfaces?.[i]?.functions || [];
    mutateTree((t) => ({ interfaces: t.interfaces.map((iface, idx) => (idx === i ? { ...iface, name } : iface)) }));
    funcs.forEach((fn) => {
      if (fn.id === undefined) return;
      void (async () => {
        try {
          await request<PersistRes>('/module-tree/node', {
            method: 'PUT',
            body: JSON.stringify({
              id: fn.id, product: active, iface_name: name,
              iface_order: fn.iface_order, func_name: fn.name, func_order: fn.func_order,
              keywords: fn.keywords || [], anchor: fn.anchor || '', engineers: fn.engineers || [],
            }),
          });
        } catch (e) { Toast({ message: '界面改名保存失败', theme: 'error' }); }
      })();
    });
  };

  // ── 功能 CRUD（新增/编辑即时落库）──
  const addFunc = (i: number) => {
    const name = (newFuncName[`${i}`] || '').trim();
    if (!name) { Toast({ message: '请输入功能名称', theme: 'warning' }); return; }
    const iface = treesRef.current[active]?.interfaces?.[i];
    if (!iface) return;
    const funcIdx = iface.functions.length;
    const newFn: FuncNode = { name, keywords: [], anchor: '', engineers: [], iface_order: i, func_order: funcIdx, iface_name: iface.name };
    mutateTree((t) => ({ interfaces: t.interfaces.map((it, idx) => (idx === i ? { ...it, functions: [...it.functions, newFn] } : it)) }));
    setNewFuncName((prev) => ({ ...prev, [`${i}`]: '' }));
    void persistRowObject(i, funcIdx, newFn, iface.name); // 立即新建并绑定 id
  };
  const removeFunc = (i: number, j: number) => {
    const fn = currentFn(i, j);
    if (!fn) return;
    clearTimer(`${active}::${i}::${j}`);
    if (typeof fn.id === 'number') void deleteNodeIds([fn.id]);
    mutateTree((t) => ({ interfaces: t.interfaces.map((it, idx) => (idx === i ? { ...it, functions: it.functions.filter((_, jdx) => jdx !== j) } : it)) }));
  };
  const renameFunc = (i: number, j: number, name: string) => {
    applyFn(i, j, (fn) => ({ ...fn, name }));
    persistDebounced(i, j);
  };
  const updateFuncField = (i: number, j: number, patch: Partial<FuncNode>) => {
    applyFn(i, j, (fn) => ({ ...fn, ...patch }));
    persistDebounced(i, j);
  };
  const saveFuncNow = (i: number, j: number, patch: Partial<FuncNode>) => {
    applyFn(i, j, (fn) => ({ ...fn, ...patch }));
    void persistNow(i, j);
  };

  // 关键词
  const addKeyword = (i: number, j: number) => {
    const key = `${i}-${j}`;
    const val = (kwInputs[key] || '').trim();
    if (!val) return;
    const fn = currentFn(i, j);
    if (!fn) return;
    applyFn(i, j, (cf) => ({ ...cf, keywords: [...(cf.keywords || []), val] }));
    setKwInputs((prev) => ({ ...prev, [key]: '' }));
    void persistNow(i, j);
  };
  const removeKeyword = (i: number, j: number, k: number) => {
    const fn = currentFn(i, j);
    if (!fn) return;
    applyFn(i, j, (cf) => ({ ...cf, keywords: (cf.keywords || []).filter((_, kdx) => kdx !== k) }));
    void persistNow(i, j);
  };

  // ── 产品 CRUD ──
  const addProduct = () => {
    const name = newProdName.trim();
    if (!name) { Toast({ message: '请输入产品名', theme: 'warning' }); return; }
    if (products.includes(name)) { Toast({ message: '产品已存在', theme: 'warning' }); return; }
    // 产品由功能行聚合而来：先在本地建空组，加入第一个功能后才真正出现在行模型
    setProducts((prev) => [...prev, name]);
    setTrees((prev) => ({ ...prev, [name]: { interfaces: [] } }));
    setActive(name);
    setNewProdName('');
  };
  // 删除产品：仅特权用户可用；确认后立即删除该产品下所有功能行并移除本地
  const removeProduct = (name: string) => {
    if (!perm?.is_privileged) { Toast({ message: '无权限删除产品', theme: 'warning' }); return; }
    const ifaces = treesRef.current[name]?.interfaces || [];
    const fnCount = ifaces.reduce((n, it) => n + (it.functions?.length || 0), 0);
    const tip = fnCount > 0
      ? `确定删除产品「${name}」吗？它包含 ${fnCount} 个功能节点，删除将立即生效。`
      : `确定删除产品「${name}」吗？删除将立即生效。`;
    if (!window.confirm(tip)) return;
    const ids: number[] = [];
    for (const it of ifaces) for (const f of it.functions || []) if (typeof f.id === 'number') ids.push(f.id);
    void deleteNodeIds(ids);
    setProducts((prev) => prev.filter((p) => p !== name));
    setTrees((prev) => { const next = { ...prev }; delete next[name]; return next; });
    setActive((cur) => (cur === name ? '' : cur));
    Toast({ message: '已删除', theme: 'success' });
  };

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
              onClick={() => saveFuncNow(ifaceIdx, funcIdx, { engineers: fn.engineers.filter((_, kdx) => kdx !== k) })}
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
      {/* 顶部固定标题栏：滚动时不随页面移动 */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 50,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        margin: '-3px -12px 12px', padding: '10px 12px',
        background: 'rgba(255,255,255,0.95)', backdropFilter: 'blur(8px)',
        borderBottom: '1px solid var(--mac-border)',
      }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>{tab === 'dept' ? '部门职责' : '责任模块树'}</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            type="button"
            className={`mac-chip ${tab === 'tree' ? 'mac-chip--tag-blue' : 'mac-chip--soft'}`}
            onClick={() => setTab('tree')}
          >责任树</button>
          <button
            type="button"
            className={`mac-chip ${tab === 'dept' ? 'mac-chip--tag-blue' : 'mac-chip--soft'}`}
            onClick={() => setTab('dept')}
          >部门职责</button>
          {tab === 'tree' && (
            <button
              type="button"
              className={`mac-chip ${viewMode === 'overview' ? 'mac-chip--tag-blue' : 'mac-chip--soft'}`}
              onClick={() => setViewMode(viewMode === 'overview' ? 'single' : 'overview')}
            >
              {viewMode === 'overview' ? '退出总览' : '🗺 总览'}
            </button>
          )}
        </div>
      </div>

      {/* ───────────── 部门职责画像管理（AI 派单部门分类用，与责任树互不影响） ───────────── */}
      {tab === 'dept' && (
        <DepartmentProfileManager request={request} />
      )}

      {/* ───────────── 单产品总览（左右对称延伸，全部功能一眼可见） ───────────── */}
      {tab === 'tree' && viewMode === 'overview' && (
        active ? (
          <MindmapView productName={active} interfaces={trees[active]?.interfaces || []} candidates={candidates} />
        ) : (
          <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>请先选择产品</div>
        )
      )}

      {/* 单产品编辑视图：产品选择器仅编辑模式显示 */}
      {viewMode === 'single' && tab === 'tree' && (
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
                        saveFuncNow(ifaceIdx, funcIdx, { engineers: next });
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
                        saveFuncNow(ifaceIdx, funcIdx, { engineers: next });
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
