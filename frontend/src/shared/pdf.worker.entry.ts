// pdf.js worker 入口：先注入运行时兼容垫片，再加载 pdf worker。
// 因为 worker 运行在独立全局作用域，主线程（main.tsx）的垫片对它不生效，
// 必须在此处单独补齐（尤其是 Array/String.prototype.at，否则 worker 静默崩溃
// 并报 "t.at is not a function"）。Vite 的 ?worker 会把本文件打包成 module worker。
import './polyfills';
import 'pdfjs-dist/build/pdf.worker.min.mjs';
