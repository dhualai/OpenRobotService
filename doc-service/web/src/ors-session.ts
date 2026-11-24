/**
 * 摇人吧共享登录态。
 *
 * 智库作为摇人吧的子页面挂在 `/t/app/kb/`（test）或 `/p/app/kb/`（prod）下，
 * 与摇人吧**同源**，因此可以直接读它写在 localStorage 的 token
 * （key 带环境前缀：test=`t_`、prod=`p_`、dev 无前缀，见摇人吧 stores/authStorage.ts）。
 *
 * 未登录 / 登录态失效时跳回摇人吧登录页；登出也交回摇人吧处理。
 * 本服务不再有本地账号密码（后端 `/api/v1/auth/login` 已返回 410）。
 */

/** 当前挂载前缀，如 `/t/app`；开发态（base=/）返回空串。 */
export function orsPrefix(): string {
  // BASE_URL 形如 /t/app/kb/ → 摇人吧自身的挂载前缀是 /t/app（去掉末段 /kb）
  const base = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '');
  if (/\/kb$/.test(base)) return base.slice(0, -3);
  return '';
}

function key(name: string): string {
  const m = /^\/([tp])\/app$/.exec(orsPrefix());
  return (m ? `${m[1]}_` : '') + name;
}

function ls(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function read(name: string): string {
  const s = ls();
  if (!s) return "";
  // 先按当前环境前缀读，再回退无前缀 key（摇人吧保留了 legacy 回退）
  return s.getItem(key(name)) || s.getItem(name) || "";
}

export function orsToken(): string {
  return read("auth_token");
}

export function orsRefreshToken(): string {
  return read("refresh_token");
}

function saveTokens(access?: string, refresh?: string): void {
  const s = ls();
  if (!s) return;
  if (access) s.setItem(key("auth_token"), access);
  if (refresh) s.setItem(key("refresh_token"), refresh);
}

/** 摇人吧登录页地址，带 from 便于登录后跳回智库。 */
export function orsLoginUrl(): string {
  // from 必须是**相对摇人吧 base** 的路径：摇人吧登录页会拼成 <它的 base><from>，
  // 传绝对路径会拼出 /t/app/t/app/kb/ 这种重复前缀（实测踩过）。
  const kbPath = (import.meta.env.BASE_URL || '/').replace(/^\/([tp])\/app/, '') || '/';
  const from = encodeURIComponent(kbPath);
  return `${orsPrefix()}/login?from=${from}`;
}

/** 跳回摇人吧登录（未登录或登录态失效时用）。 */
export function gotoOrsLogin(): void {
  if (window.location.pathname.endsWith("/login")) return;
  window.location.assign(orsLoginUrl());
}

/**
 * 用 refresh_token 换新的 access token。
 *
 * 摇人吧的 access token 只有 30 分钟，智库页面长时间停留必须自己续，
 * 否则用户会被反复踢回登录页。失败返回 false，由调用方跳登录。
 */
export async function refreshOrsToken(): Promise<boolean> {
  const rt = orsRefreshToken();
  if (!rt) return false;
  try {
    const r = await fetch(`${orsPrefix()}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!r.ok) return false;
    const j = (await r.json()) as { access_token?: string; refresh_token?: string };
    if (!j.access_token) return false;
    saveTokens(j.access_token, j.refresh_token);
    return true;
  } catch {
    return false;
  }
}
