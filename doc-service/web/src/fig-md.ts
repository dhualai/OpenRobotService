const SVG_BLOCK = /<svg\b[\s\S]*?<\/svg>/gi;

export function extractSvgBlocks(markdown: string): string[] {
  return markdown.match(SVG_BLOCK) || [];
}

export function isSchematicSvg(svg: string): boolean {
  if (!svg) return false;
  if (/<image\b/i.test(svg)) return false;
  if (/data:image\//i.test(svg)) return false;
  return /<svg\b/i.test(svg);
}

export function isSchematicEl(svg: Element): boolean {
  if (svg.querySelector("image")) return false;
  return !/data:image\//i.test(svg.innerHTML || "");
}

export function replaceSvgBlock(markdown: string, index: number, next: string): string | null {
  const re = new RegExp(SVG_BLOCK.source, "gi");
  let i = 0;
  let found = false;
  const out = markdown.replace(re, (block) => {
    if (i++ === index) {
      found = true;
      return next;
    }
    return block;
  });
  return found ? out : null;
}

function escAttr(v: string): string {
  return v.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function escText(v: string): string {
  return v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const DROP_TAGS = new Set(["script", "style", "foreignobject", "image"]);

function keepClass(value: string): string {
  return value
    .split(/\s+/)
    .filter((c) => c && !c.startsWith("is-fig-"))
    .join(" ");
}

export function serializeSvg(el: Element): string {
  const tag = el.tagName.toLowerCase();
  if ((el as HTMLElement).dataset?.figUi) return "";
  if (DROP_TAGS.has(tag)) return "";
  const attrs: string[] = [];
  if (tag === "svg" && !el.hasAttribute("xmlns")) {
    attrs.push('xmlns="http://www.w3.org/2000/svg"');
  }
  for (const a of Array.from(el.attributes)) {
    if (a.name.startsWith("data-fig")) continue;
    let val = a.value;
    if (a.name === "class") {
      val = keepClass(val);
      if (!val) continue;
    }
    attrs.push(`${a.name}="${escAttr(val)}"`);
  }
  const open = attrs.length ? `<${tag} ${attrs.join(" ")}` : `<${tag}`;
  const children: string[] = [];
  for (const n of Array.from(el.childNodes)) {
    if (n.nodeType === Node.COMMENT_NODE) {
      children.push(`<!--${(n as Comment).data}-->`);
    } else if (n.nodeType === Node.TEXT_NODE) {
      const t = n.textContent || "";
      if (tag === "text" || tag === "tspan" || tag === "title" || tag === "desc") {
        children.push(escText(t));
      } else if (t.trim()) {
        children.push(escText(t));
      }
    } else if (n.nodeType === Node.ELEMENT_NODE) {
      children.push(serializeSvg(n as Element));
    }
  }
  const inner = children.join("");
  if (tag === "path" || tag === "circle" || tag === "ellipse" || tag === "line" || tag === "polygon" || tag === "polyline" || tag === "rect") {
    if (!inner) return `${open}/>`;
  }
  return `${open}>${inner}</${tag}>`;
}
