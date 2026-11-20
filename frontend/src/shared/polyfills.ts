// 运行环境兼容垫片（runtime polyfills）
//
// 项目依赖 pdf.js v5 / DOMPurify 等，使用了较新的 JS 能力：
//   - Promise.withResolvers()                     [ES2024, Chrome 119+]
//   - Array / String / TypedArray.prototype.at()  [ES2022, Chrome 92+]
//   - Array.prototype.findLast / findLastIndex    [ES2023, Chrome 104+]
//   - Array.prototype.with / Object.hasOwn        [ES2022/2023]
//   - structuredClone                             [Chrome 98+]
//
// 部分运行环境（如较旧的微信 WebView / 旧版 Chromium 内核）缺失这些方法，
// 会导致 PDF 加载时直接抛错：
//   - "undefined is not a function"：pdf.js 在类字段初始化时调用 Promise.withResolvers()
//   - "t.at is not a function"：pdf.js worker 调用 Array/String.prototype.at()
//
// 该模块为「副作用模块」：被 import 时即执行 installPolyfills()，把缺失方法补齐。
// 必须在主线程入口（main.tsx）与 pdf worker 入口（pdf.worker.entry.ts）都注入，
// 因为 worker 运行在独立的全局作用域，主线程的垫片对其不生效。

/* eslint-disable @typescript-eslint/no-explicit-any */
function installPolyfills() {
  const g: any = globalThis;

  // ---- Promise.withResolvers() [ES2024] ----
  // pdf.js 在类字段初始化与多处能力对象上大量使用（如 PDFDocumentLoadingTask）。
  if (typeof g.Promise?.withResolvers !== 'function') {
    g.Promise.withResolvers = function () {
      let resolve!: (value?: any) => void;
      let reject!: (reason?: any) => void;
      const promise = new Promise<any>((res, rej) => {
        resolve = res;
        reject = rej;
      });
      return { promise, resolve, reject };
    };
  }

  // ---- Array / String / TypedArray.prototype.at() [ES2022] ----
  // 注意：worker 端缺失 .at() 会被静默吞成 "t.at is not a function" 崩溃。
  const atImpl = function (this: any, n: number) {
    const len = this.length;
    const k = n >= 0 ? n : len + n;
    return k < 0 || k >= len ? undefined : this[k];
  };
  if (typeof (Array.prototype as any).at !== 'function') {
    Object.defineProperty(Array.prototype, 'at', {
      value: atImpl,
      configurable: true,
      writable: true,
    });
  }
  if (typeof (String.prototype as any).at !== 'function') {
    Object.defineProperty(String.prototype, 'at', {
      value: atImpl,
      configurable: true,
      writable: true,
    });
  }
  const TAProto = Object.getPrototypeOf(Uint8Array.prototype);
  if (TAProto && typeof (TAProto as any).at !== 'function') {
    Object.defineProperty(TAProto, 'at', {
      value: atImpl,
      configurable: true,
      writable: true,
    });
  }

  // ---- Array.prototype.findLast / findLastIndex [ES2023] ----
  if (typeof (Array.prototype as any).findLast !== 'function') {
    Object.defineProperty(Array.prototype, 'findLast', {
      value: function (pred: any, thisArg?: any) {
        for (let i = this.length - 1; i >= 0; i--) {
          if (pred.call(thisArg, this[i], i, this)) return this[i];
        }
        return undefined;
      },
      configurable: true,
      writable: true,
    });
  }
  if (typeof (Array.prototype as any).findLastIndex !== 'function') {
    Object.defineProperty(Array.prototype, 'findLastIndex', {
      value: function (pred: any, thisArg?: any) {
        for (let i = this.length - 1; i >= 0; i--) {
          if (pred.call(thisArg, this[i], i, this)) return i;
        }
        return -1;
      },
      configurable: true,
      writable: true,
    });
  }

  // ---- Array.prototype.with [ES2023] ----
  if (typeof (Array.prototype as any).with !== 'function') {
    Object.defineProperty(Array.prototype, 'with', {
      value: function (index: number, value: any) {
        const copy = this.slice();
        copy[index] = value;
        return copy;
      },
      configurable: true,
      writable: true,
    });
  }

  // ---- Object.hasOwn [ES2022] ----
  if (typeof (Object as any).hasOwn !== 'function') {
    Object.defineProperty(Object, 'hasOwn', {
      value: function (obj: object, prop: PropertyKey) {
        return Object.prototype.hasOwnProperty.call(obj, prop);
      },
      configurable: true,
      writable: true,
    });
  }

  // ---- structuredClone [Chrome 98+] ----
  // 同步、真实的结构化克隆（JSON 回退无法处理 TypedArray/ArrayBuffer/Map/Set/Date，
  // 而 pdf.js 在 canvas 渲染路径会用 structuredClone 克隆二进制数据，JSON 回退会损坏）。
  // 旧内核（缺 .at 即 <Chrome92）通常也缺 structuredClone，故提供完整深拷贝实现。
  // transfer 选项（第二个参数）被忽略：以克隆代替零拷贝转移，功能等价。
  if (typeof g.structuredClone !== 'function') {
    const cloneValue = (val: any, seen: Map<any, any>): any => {
      if (val === null || typeof val !== 'object') return val;
      if (seen.has(val)) return seen.get(val);

      if (val instanceof Date) return new Date(val.getTime());
      if (val instanceof RegExp) return new RegExp(val.source, val.flags);
      if (val instanceof Map) {
        const m = new Map();
        seen.set(val, m);
        val.forEach((v, k) => m.set(cloneValue(k, seen), cloneValue(v, seen)));
        return m;
      }
      if (val instanceof Set) {
        const s = new Set();
        seen.set(val, s);
        val.forEach((v) => s.add(cloneValue(v, seen)));
        return s;
      }
      // ArrayBuffer / TypedArray / DataView
      if (typeof ArrayBuffer !== 'undefined' && val instanceof ArrayBuffer) {
        return val.slice(0);
      }
      if (ArrayBuffer.isView(val)) {
        const v = val as any;
        const Ctor = v.constructor;
        if (val instanceof DataView) {
          return new DataView((v.buffer as ArrayBuffer).slice(0), v.byteOffset, v.byteLength);
        }
        return new Ctor((v.buffer as ArrayBuffer).slice(0), v.byteOffset, v.length);
      }
      if (Array.isArray(val)) {
        const arr = new Array(val.length);
        seen.set(val, arr);
        for (let i = 0; i < val.length; i++) arr[i] = cloneValue(val[i], seen);
        return arr;
      }
      const obj: Record<string, any> = {};
      seen.set(val, obj);
      for (const key of Object.keys(val)) obj[key] = cloneValue(val[key], seen);
      return obj;
    };
    g.structuredClone = function (value: any) {
      if (value === undefined) return undefined;
      return cloneValue(value, new Map());
    };
  }
}

installPolyfills();

export {};
