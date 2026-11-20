const HIT = new Set(["rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text"]);

export function svgPoint(svg: SVGSVGElement, clientX: number, clientY: number): { x: number; y: number } {
  const pt = svg.createSVGPoint();
  pt.x = clientX;
  pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  const p = pt.matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

export function num(el: Element, attr: string, fallback = 0): number {
  const v = el.getAttribute(attr);
  if (v == null || v === "") return fallback;
  const n = parseFloat(v);
  return Number.isFinite(n) ? n : fallback;
}

export function setNum(el: Element, attr: string, v: number): void {
  const n = Math.round(v * 100) / 100;
  el.setAttribute(attr, String(n));
}

export function hitTarget(t: EventTarget | null, svg: SVGSVGElement): Element | null {
  let el: Element | null = t instanceof Element ? t : null;
  while (el && el !== svg) {
    if ((el as HTMLElement).dataset?.figUi) return el;
    const tag = el.tagName.toLowerCase();
    if (tag === "tspan" && el.parentElement) {
      el = el.parentElement;
      continue;
    }
    if (HIT.has(tag)) return el;
    el = el.parentElement;
  }
  return null;
}

function parsePoints(raw: string | null): { x: number; y: number }[] {
  if (!raw) return [];
  const nums = raw.trim().split(/[\s,]+/).map(parseFloat).filter((n) => Number.isFinite(n));
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i + 1 < nums.length; i += 2) out.push({ x: nums[i], y: nums[i + 1] });
  return out;
}

function writePoints(el: Element, pts: { x: number; y: number }[]): void {
  el.setAttribute("points", pts.map((p) => `${Math.round(p.x * 100) / 100},${Math.round(p.y * 100) / 100}`).join(" "));
}

export function moveEl(el: Element, dx: number, dy: number): void {
  const tag = el.tagName.toLowerCase();
  if (tag === "rect" || tag === "text") {
    setNum(el, "x", num(el, "x") + dx);
    setNum(el, "y", num(el, "y") + dy);
    return;
  }
  if (tag === "circle" || tag === "ellipse") {
    setNum(el, "cx", num(el, "cx") + dx);
    setNum(el, "cy", num(el, "cy") + dy);
    return;
  }
  if (tag === "line") {
    setNum(el, "x1", num(el, "x1") + dx);
    setNum(el, "y1", num(el, "y1") + dy);
    setNum(el, "x2", num(el, "x2") + dx);
    setNum(el, "y2", num(el, "y2") + dy);
    return;
  }
  if (tag === "polygon" || tag === "polyline") {
    writePoints(
      el,
      parsePoints(el.getAttribute("points")).map((p) => ({ x: p.x + dx, y: p.y + dy })),
    );
    return;
  }
  const t = el.getAttribute("transform") || "";
  const m = t.match(/^translate\(\s*([-0-9.eE]+)(?:[,\s]+([-0-9.eE]+))?\s*\)(.*)$/);
  if (m) {
    const x = parseFloat(m[1]) + dx;
    const y = parseFloat(m[2] || "0") + dy;
    el.setAttribute("transform", `translate(${Math.round(x * 100) / 100} ${Math.round(y * 100) / 100})${m[3]}`);
  } else {
    const rest = t.trim();
    el.setAttribute(
      "transform",
      `translate(${Math.round(dx * 100) / 100} ${Math.round(dy * 100) / 100})${rest ? ` ${rest}` : ""}`,
    );
  }
}

export function resizeRect(el: Element, handle: string, orig: { x: number; y: number; w: number; h: number }, x: number, y: number): void {
  let { x: nx, y: ny, w, h } = orig;
  const right = orig.x + orig.w;
  const bottom = orig.y + orig.h;
  if (handle.includes("e")) w = Math.max(4, x - orig.x);
  if (handle.includes("s")) h = Math.max(4, y - orig.y);
  if (handle.includes("w")) {
    nx = Math.min(x, right - 4);
    w = right - nx;
  }
  if (handle.includes("n")) {
    ny = Math.min(y, bottom - 4);
    h = bottom - ny;
  }
  setNum(el, "x", nx);
  setNum(el, "y", ny);
  setNum(el, "width", w);
  setNum(el, "height", h);
}

export function resizeCircle(el: Element, x: number, y: number): void {
  const cx = num(el, "cx");
  const cy = num(el, "cy");
  setNum(el, "r", Math.max(2, Math.hypot(x - cx, y - cy)));
}

export function moveLineEnd(el: Element, which: "1" | "2", x: number, y: number): void {
  setNum(el, `x${which}`, x);
  setNum(el, `y${which}`, y);
}

export function handleSize(svg: SVGSVGElement): number {
  const ctm = svg.getScreenCTM();
  const scale = ctm ? Math.hypot(ctm.a, ctm.b) || 1 : 1;
  return 8 / scale;
}

function addHandle(svg: SVGSVGElement, x: number, y: number, name: string, size: number): void {
  const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  r.setAttribute("data-fig-ui", "1");
  r.setAttribute("data-fig-handle", name);
  r.setAttribute("x", String(x - size / 2));
  r.setAttribute("y", String(y - size / 2));
  r.setAttribute("width", String(size));
  r.setAttribute("height", String(size));
  r.setAttribute("class", "fig-handle");
  svg.appendChild(r);
}

export function clearHandles(svg: SVGSVGElement): void {
  svg.querySelectorAll("[data-fig-ui]").forEach((n) => n.remove());
}

export function syncHandles(svg: SVGSVGElement, selected: Element | null): void {
  clearHandles(svg);
  if (!selected || !svg.contains(selected)) return;
  const s = handleSize(svg);
  const tag = selected.tagName.toLowerCase();
  if (tag === "rect") {
    const x = num(selected, "x");
    const y = num(selected, "y");
    const w = num(selected, "width");
    const h = num(selected, "height");
    addHandle(svg, x, y, "nw", s);
    addHandle(svg, x + w, y, "ne", s);
    addHandle(svg, x, y + h, "sw", s);
    addHandle(svg, x + w, y + h, "se", s);
    return;
  }
  if (tag === "circle") {
    addHandle(svg, num(selected, "cx") + num(selected, "r"), num(selected, "cy"), "r", s);
    return;
  }
  if (tag === "line") {
    addHandle(svg, num(selected, "x1"), num(selected, "y1"), "p1", s);
    addHandle(svg, num(selected, "x2"), num(selected, "y2"), "p2", s);
  }
}

export function setSelected(svg: SVGSVGElement, els: Element | Element[] | null): void {
  const list = !els ? [] : Array.isArray(els) ? els : [els];
  svg.querySelectorAll("[data-fig-sel]").forEach((n) => n.removeAttribute("data-fig-sel"));
  for (const el of list) el.setAttribute("data-fig-sel", "1");
  syncHandles(svg, list.length === 1 ? list[0] : null);
}
