// 中心放射式思维导图 —— 责任模块树可视化（产品居中，界面外环，功能随扇形分布）
// 纯 SVG 画布 + 自适应缩放/平移，一次性展示该产品下所有界面与功能。
import { useMemo, useRef, useState } from 'react';

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
interface Engineer {
  id: string;
  name: string;
}

interface Props {
  productName: string;
  interfaces: InterfaceNode[];
  candidates: Engineer[];
}

// 马卡龙色系（与设计系统一致的柔和色）
const IFACE_COLORS = [
  '#4f9be8', '#7b8fe0', '#a78fd9', '#d48fce',
  '#f1a8b8', '#f5b08c', '#e8c76a', '#a7c86a',
  '#6fc7a7', '#5fc7cf', '#6aaee8', '#8aa8e0',
];
const CENTER_COLOR = '#4f9be8';

const engName = (id: string, cands: Engineer[]) => {
  const f = cands.find((c) => c.id === id);
  return f ? f.name : id.slice(0, 6);
};

export default function MindmapView({ productName, interfaces, candidates }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);

  // ── 布局：计算所有节点坐标（极坐标 → 笛卡尔） ──
  const layout = useMemo(() => {
    const N = interfaces.length;
    if (N === 0) return { center: { x: 0, y: 0 }, rings: [] as any[], lines: [] as any[] };
    const center = { x: 0, y: 0 };
    // 界面环半径与功能外环半径
    const ifaceR = Math.max(240, N * 42 + 120);
    const fnR = ifaceR + Math.max(180, 46 * 4.5);
    const rings: any[] = [];
    const lines: any[] = []; // {x1,y1,x2,y2,color}

    interfaces.forEach((iface, i) => {
      // 界面的固定角度（均布，留出起始 90° 向上）
      const baseAngle = (i / N) * Math.PI * 2 - Math.PI / 2;
      const ix = center.x + ifaceR * Math.cos(baseAngle);
      const iy = center.y + ifaceR * Math.sin(baseAngle);
      lines.push({
        x1: center.x, y1: center.y, x2: ix, y2: iy,
        color: IFACE_COLORS[i % IFACE_COLORS.length],
      });

      const funcs = iface.functions || [];
      const F = Math.max(funcs.length, 1);
      // 该界面扇形覆盖的角度范围（相邻界面的中点）
      const halfSpan = (Math.PI * 2 / N) * 0.42;
      const fnCenterAngle = baseAngle;
      const fns: any[] = [];
      funcs.forEach((fn, j) => {
        // 功能在界面角度两侧等分扇形内偏移
        const span = F > 1 ? halfSpan * 2 * (j / (F - 1) - 0.5) : 0;
        const a = fnCenterAngle + span;
        const fx = center.x + fnR * Math.cos(a);
        const fy = center.y + fnR * Math.sin(a);
        fns.push({ fn, x: fx, y: fy, color: IFACE_COLORS[i % IFACE_COLORS.length] });
        lines.push({ x1: ix, y1: iy, x2: fx, y2: fy, color: IFACE_COLORS[i % IFACE_COLORS.length] });
      });
      rings.push({ iface, x: ix, y: iy, fns, color: IFACE_COLORS[i % IFACE_COLORS.length] });
    });

    return { center, rings, lines };
  }, [interfaces]);

  // 布局边界，用于自适应 viewBox
  const bounds = useMemo(() => {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    layout.lines.forEach((l: any) => {
      minX = Math.min(minX, l.x1, l.x2); maxX = Math.max(maxX, l.x1, l.x2);
      minY = Math.min(minY, l.y1, l.y2); maxY = Math.max(maxY, l.y1, l.y2);
    });
    layout.rings.forEach((r: any) => {
      r.fns.forEach((f: any) => {
        minX = Math.min(minX, f.x); maxX = Math.max(maxX, f.x);
        minY = Math.min(minY, f.y); maxY = Math.max(maxY, f.y);
      });
    });
    const pad = 60;
    return {
      x: minX - pad, y: minY - pad,
      w: maxX - minX + pad * 2, h: maxY - minY + pad * 2,
    };
  }, [layout]);

  const textAnchor = (angle: number) => {
    // 根据角度决定文字在节点哪一侧
    const a = ((angle % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
    if (a > Math.PI / 2 && a < (Math.PI * 3) / 2) return 'end';
    return 'start';
  };

  const zoom = (delta: number) => {
    setView((v) => {
      const nk = Math.max(0.3, Math.min(2.5, v.k + delta));
      return { ...v, k: nk };
    });
  };

  return (
    <div style={{ position: 'relative', width: '100%', height: '78vh', border: '1px solid var(--mac-border, #e6eaf0)', borderRadius: 16, overflow: 'hidden', background: 'var(--gradient-surface, #fbfcfe)', touchAction: 'none' }}>
      {/* 顶部工具条 */}
      <div style={{ position: 'absolute', top: 10, left: 10, zIndex: 5, display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{ fontWeight: 600, fontSize: 14, background: 'rgba(255,255,255,.8)', padding: '6px 12px', borderRadius: 999, boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
          🧠 {productName}
        </span>
        <button type="button" onClick={() => zoom(0.2)} style={{ border: 'none', background: 'rgba(255,255,255,.85)', borderRadius: 8, padding: '4px 10px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>＋</button>
        <button type="button" onClick={() => zoom(-0.2)} style={{ border: 'none', background: 'rgba(255,255,255,.85)', borderRadius: 8, padding: '4px 10px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>－</button>
        <button
          type="button"
          onClick={() => setView({ x: 0, y: 0, k: 1 })}
          style={{ border: 'none', background: 'rgba(255,255,255,.85)', borderRadius: 8, padding: '4px 10px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}
        >⤢ 复位</button>
      </div>
      <div style={{ position: 'absolute', top: 10, right: 10, zIndex: 5, color: '#8892a6', fontSize: 12, background: 'rgba(255,255,255,.7)', padding: '4px 10px', borderRadius: 999 }}>
        拖动平移 · 缩放查看
      </div>

      <svg
        ref={svgRef}
        width="100%"
        height="100%"
        viewBox={`${bounds.x} ${bounds.y} ${bounds.w} ${bounds.h}`}
        style={{ display: 'block', cursor: 'grab', transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, transformOrigin: 'center', transition: 'transform .05s linear' }}
        onMouseDown={(e) => { drag.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y }; (e.currentTarget as SVGElement).style.cursor = 'grabbing'; }}
        onMouseMove={(e) => {
          if (!drag.current) return;
          setView((v) => ({ ...v, x: drag.current!.ox + (e.clientX - drag.current!.sx), y: drag.current!.oy + (e.clientY - drag.current!.sy) }));
        }}
        onMouseUp={() => { drag.current = null; if (svgRef.current) svgRef.current.style.cursor = 'grab'; }}
        onMouseLeave={() => { drag.current = null; if (svgRef.current) svgRef.current.style.cursor = 'grab'; }}
        onWheel={(e) => zoom(e.deltaY < 0 ? 0.15 : -0.15)}
      >
        {/* 连线 */}
        {layout.lines.map((l: any, i) => (
          <line key={i} x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={l.color} strokeWidth={1.6} strokeOpacity={0.5} />
        ))}

        {/* 中心：产品 */}
        <g>
          <circle cx={layout.center.x} cy={layout.center.y} r={38} fill={CENTER_COLOR} />
          <text x={layout.center.x} y={layout.center.y + 4} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={13} fontWeight={700}>{productName.slice(0, 6)}</text>
          <text x={layout.center.x} y={layout.center.y + 4} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={9} dy={14} opacity={0.85}>{interfaces.length} 界面</text>
        </g>

        {/* 界面 + 功能 */}
        {layout.rings.map((r: any, i: number) => {
          const ifaceAngle = Math.atan2(r.y - layout.center.y, r.x - layout.center.x);
          const ifaceAnchor = textAnchor(ifaceAngle);
          return (
            <g key={i}>
              <g>
                {r.iface.description && (
                  <title>{r.iface.description}</title>
                )}
                <rect
                  x={r.x - (ifaceAnchor === 'end' ? 70 : 0)}
                  y={r.y - 12}
                  width={70}
                  height={24}
                  rx={12}
                  fill="#fff"
                  stroke={r.color}
                  strokeWidth={1.5}
                />
                <text
                  x={r.x + (ifaceAnchor === 'end' ? -35 : 35)}
                  y={r.y + 4}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={11}
                  fontWeight={600}
                  fill={r.color}
                >{r.iface.name}</text>
              </g>
              {/* 功能 */}
              {r.fns.map((f: any, j: number) => {
                const a = Math.atan2(f.y - r.y, f.x - r.x);
                const assigned = f.fn.engineers && f.fn.engineers.length > 0;
                return (
                  <g key={j}>
                    <circle cx={f.x} cy={f.y} r={assigned ? 5 : 3.5} fill={assigned ? r.color : '#c6ccd8'} />
                    <text
                      x={f.x + (a > Math.PI / 2 || a < -Math.PI / 2 ? -8 : 8)}
                      y={f.y + 3.5}
                      textAnchor={a > Math.PI / 2 || a < -Math.PI / 2 ? 'end' : 'start'}
                      fontSize={10}
                      fill="#45506b"
                    >
                      {f.fn.name}
                      {assigned && `（${f.fn.engineers.map((eid: string) => engName(eid, candidates)).join('、')}）`}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
