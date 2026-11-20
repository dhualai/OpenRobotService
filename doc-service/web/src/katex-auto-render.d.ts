declare module "katex/dist/contrib/auto-render.mjs" {
  import type { KatexOptions } from "katex";
  type Opts = KatexOptions & {
    delimiters?: { left: string; right: string; display: boolean }[];
    throwOnError?: boolean;
    ignoredTags?: string[];
  };
  export default function renderMathInElement(elem: HTMLElement, options?: Opts): void;
}
