// 责任模块树管理 —— 产品→界面→功能 三层树维护
// 背景：以「产品→界面→功能」树为派单主数据，工程师在此认领负责的功能。
// 功能节点可编辑 keywords（L3 子串）/ anchor（L2 语义）/ engineers（负责工程师）。
// 保存：PUT /admin/module-tree 整体覆盖 DB + 导出 config.yaml + 通知 AI 热更新。
import { useState, useEffect, useCallback, useMemo } from 'react';
import { Toast, Loading, Dialog, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import {
  MacPlus, MacX, MacSearch, MacCheck,
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

export default function ModuleTreeManage() {
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // 主数据
  const [products, setProducts] = useState<string[]>([]);
  const [active, setActive] = useState<string>('');
  const [trees, setTrees] = useState<TreeMap>({});
  const [candidates, setCandidates] = useState<Engineer[]>([]);

  // 新关键词/锚输入缓存
  const [kwInputs, setKwInputs] = useState<Record<string, string>>({});
  const [newIfaceName, setNewIfaceName] = useState('');
  const [newFuncName, setNewFuncName] = useState<Record<string, string>>({});
  const [newProdName, setNewProdName] = useState('');

  // 工程师选择弹层状态
  const [pickEng, setPickEng] = useState<{ ifaceIdx: number; funcIdx: number } | null>(null);
  const [engSearch, setEngSearch] = useState('');

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
        setActive((prev) => (prev && prodData.includes(prev) ? prev : prodData[0]));
      } else {
        setActive('');
      }
    } catch (e) {
      Toast({ message: '加载失败', theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => { load(); }, [load]);

  const activeTree = trees[active] || EMPTY_TREE;

  const setActiveTree = (updater: (t: { interfaces: InterfaceNode[] }) => { interfaces: InterfaceNode[] }) => {
    if (!active) return;
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

  // ── 功能 CRUD ──
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
    setProducts((prev) => [...prev, name]);
    setTrees((prev) => ({ ...prev, [name]: { interfaces: [] } }));
    setActive(name);
    setNewProdName('');
  };

  // ── 保存 ──
  const handleSave = async () => {
    if (!active) { Toast({ message: '无产品可保存', theme: 'warning' }); return; }
    setSaving(true);
    try {
      const res = await request<{ code: number; message: string; ai_reload?: string }>('/module-tree/', {
        method: 'PUT',
        body: JSON.stringify(trees),
      });
      Toast({ message: res?.message || '保存成功', theme: 'success' });
    } catch (e) {
      Toast({ message: '保存失败', theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const filteredCands = candidates.filter((c) =>
    !engSearch || c.name.toLowerCase().includes(engSearch.toLowerCase()) || (c.department || '').includes(engSearch));

  const renderEngChips = (ifaceIdx: number, funcIdx: number, fn: FuncNode) => (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '4px 8px' }}>
      {fn.engineers.map((eid, k) => (
        <span key={k} className="mac-kwchip">
          {engName(eid, candidates)}
          <span
            className="mac-kwchip__remove"
            onClick={() => updateFuncField(ifaceIdx, funcIdx, { engineers: fn.engineers.filter((_, kdx) => kdx !== k) })}
          >×</span>
        </span>
      ))}
      <span className="mac-chip mac-chip--tag-blue" onClick={() => setPickEng({ ifaceIdx, funcIdx })}>
        指定负责工程师 +
      </span>
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
    <div className="mac-page" style={{ padding: 12, paddingBottom: 80 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>责任模块树</h2>
        <button className="mac-btn mac-btn--primary" onClick={handleSave} disabled={saving}>
          {saving ? '保存中…' : '保存并生效'}
        </button>
      </div>

      {/* 产品选择器 */}
      <div className="mac-search" style={{ marginBottom: 12 }}>
        <MacSearch size={16} />
        <input
          placeholder="新增产品名（如 调度USP / 摇人吧服务号）"
          value={newProdName}
          onChange={(e) => setNewProdName(e.target.value)}
        />
        <button type="button" className="mac-btn mac-btn--primary" onClick={addProduct}>新增</button>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
        {products.map((p) => (
          <button
            key={p}
            className={`mac-chip ${p === active ? 'mac-chip--tag-blue' : 'mac-chip--soft'}`}
            onClick={() => setActive(p)}
          >{p}</button>
        ))}
      </div>

      {!active ? (
        <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>暂无产品，请先新增或刷新</div>
      ) : (
        <>
          {/* 新增界面 */}
          <div className="mac-search" style={{ marginBottom: 12 }}>
            <MacPlus size={16} />
            <input
              placeholder="新增界面（如 任务管理 / 地图 / 仿真）"
              value={newIfaceName}
              onChange={(e) => setNewIfaceName(e.target.value)}
            />
            <button className="mac-btn mac-btn--primary" onClick={addInterface}>添加</button>
          </div>

          {/* 界面列表 */}
          {activeTree.interfaces.map((iface, i) => (
            <div key={i} className="mac-module-card" style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <input
                  className="mac-input"
                  style={{ fontWeight: 700, fontSize: 16 }}
                  value={iface.name}
                  onChange={(e) => renameInterface(i, e.target.value)}
                />
                <button type="button" className="mac-btn mac-btn--ghost" onClick={() => removeInterface(i)}>
                  <MacX size={14} /> 删除
                </button>
              </div>

              {/* 新增功能 */}
              <div className="mac-search" style={{ margin: '8px 0' }}>
                <MacPlus size={16} />
                <input
                  placeholder="新增功能（如 任务下发）"
                  value={newFuncName[`${i}`] || ''}
                  onChange={(e) => setNewFuncName((prev) => ({ ...prev, [`${i}`]: e.target.value }))}
                />
                <button className="mac-btn mac-btn--primary" onClick={() => addFunc(i)}>添加</button>
              </div>

              {/* 功能列表 */}
              {iface.functions.map((fn, j) => (
                <div key={j} className="mac-sheet" style={{ marginBottom: 10, padding: 10, background: 'rgba(0,0,0,0.02)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <input
                      className="mac-input"
                      style={{ fontWeight: 600 }}
                      value={fn.name}
                      onChange={(e) => renameFunc(i, j, e.target.value)}
                    />
                    <button type="button" className="mac-btn mac-btn--ghost" onClick={() => removeFunc(i, j)}>
                      <MacX size={14} /> 删除
                    </button>
                  </div>

                  {/* keywords */}
                  <div style={{ marginTop: 8, fontSize: 13, color: '#666' }}>关键词（L3 子串匹配）：</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '4px 0' }}>
                    {fn.keywords.map((k, kdx) => (
                      <span key={kdx} className="mac-kwchip">
                        {k}
                        <span className="mac-kwchip__remove" onClick={() => removeKeyword(i, j, kdx)}>×</span>
                      </span>
                    ))}
                  </div>
                  <div className="mac-search" style={{ margin: '4px 0' }}>
                    <MacPlus size={16} />
                    <input
                      placeholder="添加关键词"
                      value={kwInputs[`${i}-${j}`] || ''}
                      onChange={(e) => setKwInputs((prev) => ({ ...prev, [`${i}-${j}`]: e.target.value }))}
                    />
                    <button className="mac-btn mac-btn--primary" onClick={() => addKeyword(i, j)}>加</button>
                  </div>

                  {/* anchor */}
                  <div style={{ marginTop: 8, fontSize: 13, color: '#666' }}>语义锚（L2）：</div>
                  <input
                    className="mac-input"
                    placeholder="一句语义描述，逗号分隔"
                    value={fn.anchor || ''}
                    onChange={(e) => updateFuncField(i, j, { anchor: e.target.value })}
                  />

                  {/* engineers */}
                  <div style={{ marginTop: 8, fontSize: 13, color: '#666' }}>负责工程师：</div>
                  {renderEngChips(i, j, fn)}
                </div>
              ))}
            </div>
          ))}
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
            {filteredCands.map((c) => {
              const ifaceIdx = pickEng?.ifaceIdx ?? 0;
              const funcIdx = pickEng?.funcIdx ?? 0;
              const fn = trees[active]?.interfaces?.[ifaceIdx]?.functions?.[funcIdx];
              const selected = fn?.engineers?.includes(c.id) || false;
              return (
                <div
                  key={c.id}
                  className="mac-user-card"
                  style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 10, cursor: 'pointer' }}
                  onClick={() => {
                    const cur = fn?.engineers || [];
                    const next = selected ? cur.filter((x) => x !== c.id) : [...cur, c.id];
                    updateFuncField(ifaceIdx, funcIdx, { engineers: next });
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600 }}>{c.name}<span style={{ color: '#999', fontWeight: 400 }}> ({c.username})</span></div>
                    <div style={{ fontSize: 12, color: '#888' }}>{c.department || '无部门'}</div>
                  </div>
                  {selected && <MacCheck size={16} />}
                </div>
              );
            })}
          </div>
        </div>
      </Popup>
    </div>
  );
}
