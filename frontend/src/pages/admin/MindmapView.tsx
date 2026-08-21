// 责任模块树总览 —— 产品居中，界面左右对称延伸，功能水平外延（SVG 可缩放/平移）
// 纯 SVG 画布 + 自适应缩放/平移，一次性展示该产品下所有界面与功能。
import { useLayoutEffect, useMemo, useRef, useState } from 'react';

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
  const boxRef = useRef<HTMLDivElement>(null);
  // 用 viewBox 参数 (x,y,w,h) 完全控制视角：缩放围绕可视中心，平移移动 x/y，不会偏移出界
  const [vb, setVb] = useState({ x: -100, y: -100, w: 200, h: 200 });
  const drag = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null);
  // 触摸手势状态：单指平移 / 双指捏合缩放
  const touch = useRef<{
    mode: 'pan' | 'pinch';
    x1: number; y1: number; x2?: number; y2?: number;  // 起始触点(像素)
    ox: number; oy: number; ow: number; oh: number;    // 起始 vb
    d0?: number;                                        // 起始两指距离
  } | null>(null);

  // ── 布局：左右对称总览（产品居中，界面垂直均布于左右两侧，功能水平外延） ──
  const ROW_H = 30;      // 每行（功能）垂直高度
  const PAD = 20;        // 顶部/底部留白
  const WALL = 180;      // 中心到界面节点 x 距离
  const FW = 150;        // 界面节点宽
  const FN_W = 210;      // 功能节点宽度

  const layout = useMemo(() => {
    const N = interfaces.length;
    if (N === 0) return { center: { x: 0, y: 0 }, branches: [] as any[], width: 0, height: 0 };
    // 每个界面占用的垂直行数 = 功能数（至少 1 行）
    const items: { iface: InterfaceNode; fns: FuncNode[]; h: number }[] = [];
    interfaces.forEach((iface) => {
      const fns = iface.functions || [];
      items.push({ iface, fns, h: Math.max(fns.length, 1) * ROW_H });
    });
    // 贪心均衡分配到左/右：把界面放到功能总数较少的一侧，保证左右对称
    let sumL = 0, sumR = 0;
    const placed: { iface: InterfaceNode; fns: FuncNode[]; y: number; side: 1 | -1 }[] = [];
    let cursorL = PAD, cursorR = PAD;
    items.forEach((it) => {
      if (sumL <= sumR) {
        placed.push({ iface: it.iface, fns: it.fns, y: cursorL + it.h / 2, side: -1 });
        cursorL += it.h; sumL += it.fns.length;
      } else {
        placed.push({ iface: it.iface, fns: it.fns, y: cursorR + it.h / 2, side: 1 });
        cursorR += it.h; sumR += it.fns.length;
      }
    });
    const height = Math.max(cursorL, cursorR) + PAD;
    const centerY = height / 2;
    // 分支：界面节点在 WALL 外侧；功能节点在其外侧水平延伸（胶囊不遮挡界面）
    const branches: any[] = placed.map((it) => {
      const dx = it.side === 1 ? 1 : -1;               // 延伸方向
      const ix = dx * WALL;                            // 界面节点 x
      // 功能胶囊中心 x：在界面节点外侧（界面节点宽 FW + 间距 12 + 胶囊宽一半）
      const fx = ix + dx * (FW + 12 + FN_W / 2);
      const F = it.fns.length;
      const fnNodes = it.fns.map((fn, j) => {
        const fnY = it.y + (F > 1 ? (j - (F - 1) / 2) * ROW_H : 0);
        return { fn, x: fx, y: fnY, side: it.side };
      });
      return { iface: it.iface, x: ix, y: it.y, side: it.side, fnNodes, color: IFACE_COLORS[it.side === 1 ? 0 : 5] };
    });
    const width = (WALL + FW + 12 + FN_W + 60) * 2;
    return { center: { x: 0, y: centerY }, branches, width, height };
  }, [interfaces]);

  // 布局边界（完整视图，含边距）
  const fullView = useMemo(() => {
    const pad = 30;
    return {
      x: -layout.width / 2 - pad,
      y: -layout.height / 2 - pad,
      w: layout.width + pad * 2,
      h: layout.height + pad * 2,
    };
  }, [layout]);

  // 复位/初始：完整视图
  const fitView = () => setVb(fullView);
  useLayoutEffect(() => { setVb(fullView); }, [fullView.x, fullView.y, fullView.w, fullView.h]);

  // 缩放：围绕当前可视中心（svg 画布中心）缩放 viewBox
  const zoom = (factor: number) => {
    setVb((v) => {
      const cx = v.x + v.w / 2;
      const cy = v.y + v.h / 2;
      const w = v.w / factor;
      const h = v.h / factor;
      return { x: cx - w / 2, y: cy - h / 2, w, h };
    });
  };

  // ── 触摸手势（移动端）：单指平移 / 双指捏合缩放 ──
  const svgSize = (e: React.TouchEvent) => {
    const el = e.currentTarget as SVGSVGElement;
    return { cw: el.clientWidth || 1, ch: el.clientHeight || 1 };
  };

  const onTouchStart = (e: React.TouchEvent) => {
    const t = Array.from(e.touches);
    const { cw, ch } = svgSize(e);
    void cw; void ch;
    if (t.length === 1) {
      touch.current = { mode: 'pan', x1: t[0].clientX, y1: t[0].clientY, ox: vb.x, oy: vb.y, ow: vb.w, oh: vb.h };
    } else if (t.length >= 2) {
      const d0 = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
      touch.current = { mode: 'pinch', x1: t[0].clientX, y1: t[0].clientY, x2: t[1].clientX, y2: t[1].clientY, ox: vb.x, oy: vb.y, ow: vb.w, oh: vb.h, d0 };
    }
  };

  const onTouchMove = (e: React.TouchEvent) => {
    if (!touch.current) return;
    const cur = touch.current;   // 先拷贝快照，避免 updater 延迟执行时 touch.current 已被置空
    const t = Array.from(e.touches);
    const { cw, ch } = svgSize(e);
    if (cur.mode === 'pan' && t.length >= 1) {
      setVb((v) => ({
        ...v,
        x: cur.ox - (t[0].clientX - cur.x1) * (v.w / cw),
        y: cur.oy - (t[0].clientY - cur.y1) * (v.h / ch),
      }));
    } else if (cur.mode === 'pinch' && t.length >= 2) {
      const d = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
      const factor = (cur.d0 && d > 0) ? d / cur.d0 : 1;
      // 两指中点的像素坐标
      const mx = (t[0].clientX + t[1].clientX) / 2;
      const my = (t[0].clientY + t[1].clientY) / 2;
      // 中点对应的 viewBox 内容坐标（基于起始 vb），缩放后保持该点位置
      const nw = cur.ow / factor;
      const nh = cur.oh / factor;
      setVb({
        x: cur.ox + (mx / cw) * cur.ow - (mx / cw) * nw,
        y: cur.oy + (my / ch) * cur.oh - (my / ch) * nh,
        w: nw,
        h: nh,
      });
    }
  };

  const onTouchEnd = (e: React.TouchEvent) => {
    const t = Array.from(e.touches);
    if (t.length === 0) {
      touch.current = null;
    } else if (t.length === 1) {
      // 变为单指：重置为平移起点
      touch.current = { mode: 'pan', x1: t[0].clientX, y1: t[0].clientY, ox: vb.x, oy: vb.y, ow: vb.w, oh: vb.h };
    }
  };

  return (
    <div ref={boxRef} style={{ position: 'relative', width: '100%', height: '78vh', border: '1px solid var(--mac-border, #e6eaf0)', borderRadius: 16, overflow: 'hidden', background: 'var(--gradient-surface, #fbfcfe)', touchAction: 'none' }}>
      {/* 顶部工具条 */}
      <div style={{ position: 'absolute', top: 10, left: 10, zIndex: 5, display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{ fontWeight: 600, fontSize: 14, background: 'rgba(255,255,255,.8)', padding: '6px 12px', borderRadius: 999, boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>
          🧠 {productName}
        </span>
        <button type="button" onClick={() => zoom(1.2)} style={{ border: 'none', background: 'rgba(255,255,255,.85)', borderRadius: 8, padding: '4px 10px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>＋</button>
        <button type="button" onClick={() => zoom(1 / 1.2)} style={{ border: 'none', background: 'rgba(255,255,255,.85)', borderRadius: 8, padding: '4px 10px', cursor: 'pointer', boxShadow: '0 2px 8px rgba(0,0,0,.06)' }}>－</button>
        <button
          type="button"
          onClick={fitView}
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
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        style={{ display: 'block', cursor: 'grab', background: 'transparent', touchAction: 'none' }}
        onMouseDown={(e) => { drag.current = { sx: e.clientX, sy: e.clientY, ox: vb.x, oy: vb.y }; (e.currentTarget as SVGElement).style.cursor = 'grabbing'; }}
        onMouseMove={(e) => {
          if (!drag.current) return;
          // 拖动：把鼠标像素位移换算成 viewBox 单位
          const cw = (e.currentTarget as SVGSVGElement).clientWidth || 1;
          const ch = (e.currentTarget as SVGSVGElement).clientHeight || 1;
          setVb((v) => ({
            ...v,
            x: drag.current!.ox - (e.clientX - drag.current!.sx) * (v.w / cw),
            y: drag.current!.oy - (e.clientY - drag.current!.sy) * (v.h / ch),
          }));
        }}
        onMouseUp={() => { drag.current = null; if (svgRef.current) svgRef.current.style.cursor = 'grab'; }}
        onMouseLeave={() => { drag.current = null; if (svgRef.current) svgRef.current.style.cursor = 'grab'; }}
        onWheel={(e) => zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15)}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchEnd}
      >
        {/* ── 连线：中心→界面（水平贝塞尔），界面→功能（水平直线） ── */}
        {layout.branches.map((b: any, i: number) => (
          <g key={`lines-${i}`}>
            <path
              d={`M ${layout.center.x} ${layout.center.y} C ${b.x / 2} ${layout.center.y}, ${b.x / 2} ${b.y}, ${b.x} ${b.y}`}
              fill="none" stroke={CENTER_COLOR} strokeWidth={2} strokeOpacity={0.4}
            />
            {b.fnNodes.map((f: any, j: number) => (
              <line key={j} x1={b.x} y1={b.y} x2={f.x - f.side * (FN_W / 2)} y2={f.y} stroke={b.color} strokeWidth={1.4} strokeOpacity={0.3} />
            ))}
          </g>
        ))}

        {/* ── 中心：产品 ── */}
        <g>
          <circle cx={layout.center.x} cy={layout.center.y} r={54} fill={CENTER_COLOR} />
          <text x={layout.center.x} y={layout.center.y - 2} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={15} fontWeight={700}>{productName}</text>
          <text x={layout.center.x} y={layout.center.y + 16} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={10} opacity={0.92}>
            {interfaces.length} 界面 · {layout.branches.reduce((n:number, b:any)=>n+b.fnNodes.length,0)} 功能
          </text>
        </g>

        {/* ── 界面 与 功能（左右延伸） ── */}
        {layout.branches.map((b: any, i: number) => {
          const color = b.color;
          const rightSide = b.side === 1;
          const iw = 150; // 界面节点宽
          return (
            <g key={i}>
              {/* 界面节点（在中心旁） */}
              <g>
                {b.iface.description && <title>{b.iface.description}</title>}
                <rect x={b.x - (rightSide ? 0 : iw)} y={b.y - 16} width={iw} height={32} rx={16}
                  fill="#fff" stroke={color} strokeWidth={2} />
                <circle cx={b.x + (rightSide ? 13 : -13)} cy={b.y} r={5} fill={color} />
                <text x={b.x + (rightSide ? 28 : -28)} y={b.y + 1} dominantBaseline="middle"
                  fontSize={12} fontWeight={700} fill={color}
                  textAnchor={rightSide ? 'start' : 'end'}>
                  {b.iface.name}（{b.fnNodes.length}）
                </text>
              </g>
              {/* 功能节点（水平外延） */}
              {b.fnNodes.map((f: any, j: number) => {
                const assigned = f.fn.engineers && f.fn.engineers.length > 0;
                const owner = assigned ? f.fn.engineers.map((eid: string) => engName(eid, candidates)).join('、') : '';
                // 胶囊始终以 f.x 为中心（f.x 已在界面节点外侧），左右均为 FN_W 宽的居中矩形
                const rectX = f.x - FN_W / 2;
                return (
                  <g key={j}>
                    <rect x={rectX} y={f.y - 11} width={FN_W} height={22} rx={11}
                      fill={assigned ? color : '#f2f4f8'}
                      stroke={assigned ? color : '#dfe3ec'} strokeWidth={assigned ? 1.5 : 1} />
                    {/* 圆点靠近界面一侧 */}
                    <circle cx={f.x + (rightSide ? -FN_W / 2 + 11 : FN_W / 2 - 11)} cy={f.y} r={3.5} fill={assigned ? '#fff' : color} />
                    <text x={f.x + (rightSide ? -FN_W / 2 + 20 : FN_W / 2 - 20)} y={f.y + 1} dominantBaseline="middle" fontSize={10.5}
                      textAnchor={rightSide ? 'start' : 'end'}
                      fill={assigned ? '#fff' : '#45506b'} fontWeight={assigned ? 600 : 400}>
                      {f.fn.name}{owner ? ` ·${owner}` : ''}
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
