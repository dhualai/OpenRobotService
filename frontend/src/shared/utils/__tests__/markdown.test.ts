/**
 * shared/utils/markdown 的单测：
 * toAppUrl / appUrlTransform 的环境前缀补全与 URL 清洗行为。
 *
 * ENV_PREFIX 由 config/api.ts 顶层从 import.meta.env.BASE_URL 推导
 * （'/'→''、'/t/app/'→'/t'、'/p/app/'→'/p'），故各用例先 stubEnv 再
 * resetModules + 动态 import，确保模块级常量按 stub 后的 BASE_URL 重新求值。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

const loadModule = async () => {
  vi.resetModules();
  return await import('../markdown');
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe('toAppUrl —— 裸 /api/ 补环境前缀', () => {
  it('dev（BASE_URL=/）：裸 /api/ 原样保留，走 vite 代理', async () => {
    vi.stubEnv('BASE_URL', '/');
    const { toAppUrl } = await loadModule();
    expect(toAppUrl('/api/tasks/files/helpdesk-comment/spec-doc/images/a.png')).toBe(
      '/api/tasks/files/helpdesk-comment/spec-doc/images/a.png',
    );
  });

  it('test（BASE_URL=/t/app/）：裸 /api/ 补 /t 前缀', async () => {
    vi.stubEnv('BASE_URL', '/t/app/');
    const { toAppUrl } = await loadModule();
    expect(toAppUrl('/api/tasks/files/x.png')).toBe('/t/api/tasks/files/x.png');
  });

  it('prod（BASE_URL=/p/app/）：裸 /api/ 补 /p 前缀', async () => {
    vi.stubEnv('BASE_URL', '/p/app/');
    const { toAppUrl } = await loadModule();
    expect(toAppUrl('/api/call/files/y.mp4')).toBe('/p/api/call/files/y.mp4');
  });

  it('已带环境前缀的 /t|/p/api/ 不重复补', async () => {
    vi.stubEnv('BASE_URL', '/t/app/');
    const { toAppUrl } = await loadModule();
    expect(toAppUrl('/t/api/tasks/files/x.png')).toBe('/t/api/tasks/files/x.png');
    expect(toAppUrl('/p/api/tasks/files/x.png')).toBe('/p/api/tasks/files/x.png');
  });

  it('绝对 URL 与其他相对路径原样返回', async () => {
    vi.stubEnv('BASE_URL', '/p/app/');
    const { toAppUrl } = await loadModule();
    expect(toAppUrl('https://example.com/a.png')).toBe('https://example.com/a.png');
    expect(toAppUrl('/tasks/44123')).toBe('/tasks/44123');
    expect(toAppUrl('docs/a.md')).toBe('docs/a.md');
  });
});

describe('appUrlTransform —— react-markdown urlTransform 统一出口', () => {
  it('data:image/ 放行（base64 内嵌图不裂）', async () => {
    vi.stubEnv('BASE_URL', '/p/app/');
    const { appUrlTransform } = await loadModule();
    const dataUrl = 'data:image/png;base64,iVBORw0KGgo=';
    expect(appUrlTransform(dataUrl)).toBe(dataUrl);
  });

  it('data:text/html 等危险协议仍被默认白名单清洗为空串', async () => {
    vi.stubEnv('BASE_URL', '/p/app/');
    const { appUrlTransform } = await loadModule();
    expect(appUrlTransform('data:text/html,<script>alert(1)</script>')).toBe('');
    expect(appUrlTransform('javascript:alert(1)')).toBe('');
  });

  it('test 环境：md 内裸 /api/ 引用补 /t 前缀（裂图修复主场景）', async () => {
    vi.stubEnv('BASE_URL', '/t/app/');
    const { appUrlTransform } = await loadModule();
    expect(appUrlTransform('/api/tasks/files/helpdesk-comment/spec-doc/images/a.png')).toBe(
      '/t/api/tasks/files/helpdesk-comment/spec-doc/images/a.png',
    );
  });

  it('prod 环境：https 外链原样保留（默认白名单放行合法协议）', async () => {
    vi.stubEnv('BASE_URL', '/p/app/');
    const { appUrlTransform } = await loadModule();
    expect(appUrlTransform('https://example.com/a.png')).toBe('https://example.com/a.png');
    expect(appUrlTransform('http://example.com/b.zip')).toBe('http://example.com/b.zip');
  });

  it('clipboard: 协议放行（讨论区粘贴引用芯片）', async () => {
    vi.stubEnv('BASE_URL', '/');
    const { appUrlTransform } = await loadModule();
    const url = 'clipboard:clipboard%28207%E8%A1%8C%29.txt';
    expect(appUrlTransform(url)).toBe(url);
  });

  it('普通相对路径（如 SPA 路由 /tasks/1）不被改写', async () => {
    vi.stubEnv('BASE_URL', '/t/app/');
    const { appUrlTransform } = await loadModule();
    expect(appUrlTransform('/tasks/44123')).toBe('/tasks/44123');
  });
});
