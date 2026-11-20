import { useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { CheckIcon, CloseIcon, PencilIcon, UndoIcon } from "./icons";
import {
  hitTarget,
  moveEl,
  moveLineEnd,
  num,
  resizeCircle,
  resizeRect,
  setSelected,
  svgPoint,
  syncHandles,
} from "./fig-geom";
import { extractSvgBlocks, isSchematicEl, isSchematicSvg, replaceSvgBlock, serializeSvg } from "./fig-md";

type Drag =
  | { kind: "move"; lastX: number; lastY: number; moved: boolean }
  | { kind: "rect"; handle: string; orig: { x: number; y: number; w: number; h: number } }
  | { kind: "circle" }
  | { kind: "line"; which: "1" | "2" };

type Frame = { i: number; wrap: HTMLElement };

function isChromeSvg(svg: Element): boolean {
  return Boolean(svg.closest(".fig-corner, .fig-text-pop, .icon-act, .btn-q"));
}

function contentSvgs(root: ParentNode): SVGSVGElement[] {
  return Array.from(root.querySelectorAll("svg")).filter((s) => !isChromeSvg(s));
}

function svgInFrame(wrap: HTMLElement): SVGSVGElement | null {
  return contentSvgs(wrap)[0] || null;
}

function ensureFrame(svg: SVGSVGElement): HTMLElement {
  const fig = svg.closest(".fig");
  if (fig instanceof HTMLElement && contentSvgs(fig).length === 1) {
    fig.classList.add("fig-frame");
    return fig;
  }
  const parent = svg.parentElement;
  if (parent?.classList.contains("fig-frame")) return parent;
  const wrap = document.createElement("div");
  wrap.className = "fig-frame";
  parent?.insertBefore(wrap, svg);
  wrap.appendChild(svg);
  return wrap;
}

function cleanClone(svg: SVGSVGElement): Element {
  const clone = svg.cloneNode(true) as Element;
  clone.querySelectorAll("[data-fig-ui]").forEach((n) => n.remove());
  clone.querySelectorAll("[data-fig-sel]").forEach((n) => n.removeAttribute("data-fig-sel"));
  (clone as HTMLElement).classList.remove("is-fig-editing");
  return clone;
}

export function FigEditor({
  hostRef,
  html,
  markdown,
  onSave,
  saving,
  error,
}: {
  hostRef: RefObject<HTMLElement | null>;
  html: string;
  markdown: string;
  onSave: (markdown: string) => void;
  saving?: boolean;
  error?: string;
}) {
  const [frames, setFrames] = useState<Frame[]>([]);
  const [active, setActive] = useState<number | null>(null);
  const [help, setHelp] = useState(false);
  const [canUndo, setCanUndo] = useState(false);
  const [text, setText] = useState<{ el: Element; value: string; top: number; left: number; w: number } | null>(null);
  const drag = useRef<Drag | null>(null);
  const orig = useRef("");
  const svgRef = useRef<SVGSVGElement | null>(null);
  const selRef = useRef<Element[]>([]);
  const editI = useRef<number | null>(null);
  const undo = useRef<string[]>([]);
  const preDrag = useRef<string | null>(null);
  const textRef = useRef(text);
  textRef.current = text;
  const mdRef = useRef(markdown);
  mdRef.current = markdown;
  const saveRef = useRef(onSave);
  saveRef.current = onSave;

  function clearEditDom() {
    const host = hostRef.current;
    const svg = svgRef.current;
    const frame = (svg?.closest(".fig-frame") as HTMLElement | null) || null;
    if (svg?.isConnected) {
      svg.querySelectorAll("[data-fig-ui]").forEach((n) => n.remove());
      svg.querySelectorAll("[data-fig-sel]").forEach((n) => n.removeAttribute("data-fig-sel"));
      svg.classList.remove("is-fig-editing");
    }
    frame?.classList.remove("is-editing");
    host?.querySelectorAll(".fig-frame.is-editing").forEach((el) => el.classList.remove("is-editing"));
    host?.querySelectorAll("svg.is-fig-editing").forEach((el) => {
      if (!isChromeSvg(el)) el.classList.remove("is-fig-editing");
    });
    host?.querySelectorAll("[data-fig-sel]").forEach((el) => el.removeAttribute("data-fig-sel"));
    host?.querySelectorAll("[data-fig-ui]").forEach((n) => n.remove());
  }

  function resetEdit() {
    clearEditDom();
    svgRef.current = null;
    orig.current = "";
    drag.current = null;
    selRef.current = [];
    editI.current = null;
    undo.current = [];
    preDrag.current = null;
    setText(null);
    setHelp(false);
    setCanUndo(false);
    setActive(null);
  }

  function cancelEdit() {
    const svg = svgRef.current;
    const frame = (svg?.closest(".fig-frame") as HTMLElement | null) || null;
    if (svg?.isConnected && orig.current) {
      svg.outerHTML = orig.current;
      svgRef.current = null;
    }
    frame?.classList.remove("is-editing");
    resetEdit();
  }

  function commit() {
    const svg = svgRef.current;
    const i = editI.current;
    const md = mdRef.current;
    const save = saveRef.current;
    if (!svg || i == null || !md || !save) return;
    const next = serializeSvg(cleanClone(svg));
    const out = replaceSvgBlock(md, i, next);
    if (out) save(out);
  }

  function pushUndo(snap: string) {
    if (undo.current[undo.current.length - 1] === snap) return;
    undo.current.push(snap);
    setCanUndo(true);
  }

  function snapInner(): string {
    const svg = svgRef.current;
    return svg ? cleanClone(svg).innerHTML : "";
  }

  function undoOnce() {
    const svg = svgRef.current;
    const prev = undo.current.pop();
    if (!svg || prev == null) return;
    svg.querySelectorAll("[data-fig-ui]").forEach((n) => n.remove());
    svg.innerHTML = prev;
    selRef.current = [];
    setSelected(svg, []);
    setCanUndo(undo.current.length > 0);
  }

  function begin(svg: SVGSVGElement, i: number) {
    if (editI.current != null && svgRef.current && svgRef.current !== svg) cancelEdit();
    orig.current = svg.outerHTML;
    svgRef.current = svg;
    editI.current = i;
    selRef.current = [];
    undo.current = [];
    svg.classList.add("is-fig-editing");
    svg.closest(".fig-frame")?.classList.add("is-editing");
    setText(null);
    setHelp(false);
    setCanUndo(false);
    setActive(i);
  }

  useLayoutEffect(() => {
    resetEdit();
    const host = hostRef.current;
    if (!host || !markdown) {
      setFrames([]);
      return;
    }
    const blocks = extractSvgBlocks(markdown);
    const nodes = contentSvgs(host);
    if (blocks.length !== nodes.length) {
      setFrames([]);
      return;
    }
    const next: Frame[] = [];
    nodes.forEach((svg, i) => {
      if (!isSchematicSvg(blocks[i]) || !isSchematicEl(svg)) return;
      next.push({ i, wrap: ensureFrame(svg) });
    });
    setFrames(next);
  }, [html, markdown, hostRef]);

  useLayoutEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const root = host;

    function onDown(e: PointerEvent) {
      if (e.button !== 0) return;
      const t = e.target as Element;
      if (t.closest(".fig-corner, .fig-text-pop, .help-pop")) return;
      const svg = svgRef.current;
      if (!svg || editI.current == null) return;
      if (!svg.contains(t)) return;
      e.preventDefault();
      e.stopPropagation();
      const p = svgPoint(svg, e.clientX, e.clientY);
      const handle = (t as HTMLElement).dataset?.figHandle;
      const cur = selRef.current.filter((el) => el.isConnected);
      if (handle && cur.length === 1) {
        const el = cur[0];
        const tag = el.tagName.toLowerCase();
        preDrag.current = snapInner();
        if (tag === "rect") {
          drag.current = {
            kind: "rect",
            handle,
            orig: { x: num(el, "x"), y: num(el, "y"), w: num(el, "width"), h: num(el, "height") },
          };
        } else if (tag === "circle" && handle === "r") {
          drag.current = { kind: "circle" };
        } else if (tag === "line" && (handle === "p1" || handle === "p2")) {
          drag.current = { kind: "line", which: handle === "p1" ? "1" : "2" };
        }
        try {
          svg.setPointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
        return;
      }
      const hit = hitTarget(t, svg);
      if (hit && !(hit as HTMLElement).dataset?.figUi) {
        const additive = e.shiftKey || e.ctrlKey || e.metaKey;
        let next = cur;
        if (additive) {
          next = next.includes(hit) ? next.filter((el) => el !== hit) : [...next, hit];
          selRef.current = next;
          setSelected(svg, next);
          return;
        }
        if (!next.includes(hit)) next = [hit];
        selRef.current = next;
        setSelected(svg, next);
        preDrag.current = snapInner();
        drag.current = { kind: "move", lastX: p.x, lastY: p.y, moved: false };
        try {
          svg.setPointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
      } else if (!additiveClick(e)) {
        selRef.current = [];
        setSelected(svg, []);
      }
    }

    function additiveClick(e: PointerEvent) {
      return e.shiftKey || e.ctrlKey || e.metaKey;
    }

    function onMove(e: PointerEvent) {
      const svg = svgRef.current;
      const d = drag.current;
      if (!svg || !d) return;
      const p = svgPoint(svg, e.clientX, e.clientY);
      const cur = selRef.current.filter((el) => el.isConnected);
      if (d.kind === "move") {
        const dx = p.x - d.lastX;
        const dy = p.y - d.lastY;
        if (dx || dy) d.moved = true;
        for (const el of cur) moveEl(el, dx, dy);
        d.lastX = p.x;
        d.lastY = p.y;
        syncHandles(svg, cur.length === 1 ? cur[0] : null);
        return;
      }
      const el = cur[0];
      if (!el) return;
      if (d.kind === "rect") resizeRect(el, d.handle, d.orig, p.x, p.y);
      else if (d.kind === "circle") resizeCircle(el, p.x, p.y);
      else if (d.kind === "line") moveLineEnd(el, d.which, p.x, p.y);
      syncHandles(svg, el);
    }

    function onUp() {
      const d = drag.current;
      drag.current = null;
      if (!d) return;
      const before = preDrag.current;
      preDrag.current = null;
      if (before != null && snapInner() !== before) pushUndo(before);
    }

    function onDbl(e: MouseEvent) {
      const svg = svgRef.current;
      if (!svg) return;
      const t = e.target as Element;
      if (!svg.contains(t)) return;
      const hit = hitTarget(t, svg);
      if (!hit || hit.tagName.toLowerCase() !== "text") return;
      e.preventDefault();
      const r = hit.getBoundingClientRect();
      setText({
        el: hit,
        value: (hit.textContent || "").replace(/\s+/g, " ").trim(),
        top: r.bottom + 6,
        left: r.left,
        w: Math.max(160, r.width),
      });
    }

    function typingInField(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      if (!t) return false;
      const tag = t.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
      return Boolean(t.isContentEditable);
    }

    function onKey(e: KeyboardEvent) {
      if (editI.current == null) return;
      if (typingInField(e)) return;
      if (e.key === "Escape") {
        if (textRef.current) {
          setText(null);
          return;
        }
        e.preventDefault();
        cancelEdit();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        if (textRef.current) return;
        e.preventDefault();
        undoOnce();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        commit();
        return;
      }
      if (textRef.current) return;
      const svg = svgRef.current;
      const cur = selRef.current.filter((el) => el.isConnected && !(el as HTMLElement).dataset?.figUi);
      if (!cur.length || !svg) return;
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        pushUndo(snapInner());
        for (const el of cur) el.remove();
        selRef.current = [];
        setSelected(svg, []);
        return;
      }
      const step = e.shiftKey ? 10 : 1;
      let dx = 0;
      let dy = 0;
      if (e.key === "ArrowLeft") dx = -step;
      else if (e.key === "ArrowRight") dx = step;
      else if (e.key === "ArrowUp") dy = -step;
      else if (e.key === "ArrowDown") dy = step;
      else return;
      e.preventDefault();
      pushUndo(snapInner());
      for (const el of cur) moveEl(el, dx, dy);
      syncHandles(svg, cur.length === 1 ? cur[0] : null);
    }

    root.addEventListener("pointerdown", onDown, true);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    root.addEventListener("dblclick", onDbl);
    window.addEventListener("keydown", onKey);
    return () => {
      root.removeEventListener("pointerdown", onDown, true);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      root.removeEventListener("dblclick", onDbl);
      window.removeEventListener("keydown", onKey);
    };
  }, [html, hostRef]);

  return (
    <>
      {error ? <p className="err fig-edit-err">{error}</p> : null}
      {frames.map((f) =>
        f.wrap.isConnected
          ? createPortal(
              <div className="fig-corner">
                {active === f.i ? (
                  <>
                    <button
                      type="button"
                      className="icon-act"
                      aria-label="撤销"
                      disabled={saving || !canUndo}
                      onClick={() => undoOnce()}
                    >
                      <UndoIcon />
                    </button>
                    <button type="button" className="icon-act is-ok" aria-label="保存" disabled={saving} onClick={() => commit()}>
                      <CheckIcon />
                    </button>
                    <button
                      type="button"
                      className="icon-act is-off"
                      aria-label="取消"
                      disabled={saving}
                      onClick={() => cancelEdit()}
                    >
                      <CloseIcon />
                    </button>
                    <button type="button" className="btn-q" aria-label="帮助" aria-expanded={help} onClick={() => setHelp(!help)}>
                      ?
                    </button>
                    {help ? (
                      <div className="help-pop" role="dialog">
                        <ul>
                          <li>点图形后拖动；Shift/Ctrl 多选后一起拖</li>
                          <li>角点缩放矩形，圆边缩放半径</li>
                          <li>双击文字修改</li>
                          <li>Delete / Backspace 删除选中图元</li>
                          <li>Ctrl/⌘+Z 撤销 · Ctrl/⌘+S 保存 · Esc 取消</li>
                        </ul>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <button
                    type="button"
                    className="icon-act"
                    aria-label="编辑"
                    disabled={saving}
                    onClick={() => {
                      const svg = svgInFrame(f.wrap);
                      if (svg) begin(svg, f.i);
                    }}
                  >
                    <PencilIcon />
                  </button>
                )}
              </div>,
              f.wrap,
            )
          : null,
      )}
      {text ? (
        <form
          className="fig-text-pop"
          style={{ top: text.top, left: text.left, width: text.w }}
          onSubmit={(e) => {
            e.preventDefault();
            pushUndo(snapInner());
            text.el.textContent = text.value;
            const svg = svgRef.current;
            if (svg) syncHandles(svg, selRef.current.length === 1 ? selRef.current[0] : text.el);
            setText(null);
          }}
        >
          <input
            autoFocus
            value={text.value}
            onChange={(e) => setText({ ...text, value: e.target.value })}
            onBlur={(e) => {
              const v = e.currentTarget.value;
              if (v !== text.value || v !== (text.el.textContent || "").replace(/\s+/g, " ").trim()) {
                pushUndo(snapInner());
              }
              text.el.textContent = v;
              setText(null);
            }}
          />
        </form>
      ) : null}
    </>
  );
}
