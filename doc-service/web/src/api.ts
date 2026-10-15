export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

const DETAIL_ZH: Record<string, string> = {
  hard_cap: "超出本周上限",
  title_mismatch: "正文标题须与结构图名称一致",
  settled: "已结算",
  forbidden: "无权",
  invalid_hours: "工时无效",
  unauthorized: "未登录",
  not_found: "不存在",
  bad_product: "产品无效",
  edit_conflict: "正文已变更，请重新导出后再导入",
  write_revoked: "当前账号已无此模块写权限",
  need_password: "密码至少 8 位",
  invalid_role: "角色无效",
  last_admin: "不能去掉最后一名管理员",
  missing_pack: "工程包不完整",
  not_zip: "请上传 zip",
  product_mismatch: "产品不一致",
  unconfigured: "未配置测试管家",
  butler_unreachable: "自测系统不可达",
  butler_auth: "测试管家认证失败",
  butler_error: "自测系统返回错误",
  bad_username: "账号无法同步到自测系统",
  module_mismatch: "模块不一致",
  exists: "已存在",
  bad_tag: "标签无效",
  bad_semver: "版本号无效",
  bad_password: "原密码不正确",
  invalid_band: "无效状态",
  cannot_regress: "不能回退到更早阶段",
  page_only: "请在页面上修改状态",
  last_version: "至少保留一个版本",
  not_newer: "须高于当前 latest",
  not_done: "当前版本未完成",
  frozen: "历史版本不可改",
  last_protected_version: "模块已保护，至少保留一个受保护版本",
  protected: "已保护",
  out_of_scope_delete: "无权删除",
  invalid_priority: "优先级须为 P0–P4",
  unresolved_comments: "需求下仍有未处理评论",
  need_reviewers: "请选择评审人",
  invalid_reviewer: "评审人无效",
  not_draft: "仅草稿可发起评审",
  not_review: "当前不是待评审",
  not_reviewer: "不是评审人",
  invalid_verdict: "评审无效",
  need_reason: "请填写原因",
  not_pending_link: "当前不是待关联",
  need_title: "请填写标题",
  need_body: "请填写内容",
  already_public: "已发布",
};

function parseDetail(raw: string): string {
  try {
    const j = JSON.parse(raw);
    const d = j.detail;
    if (typeof d === "string") return DETAIL_ZH[d] || d;
    if (d && typeof d === "object" && typeof d.error === "string") {
      if (typeof d.hint === "string" && d.hint.trim()) return d.hint;
      return DETAIL_ZH[d.error] || d.error;
    }
    if (Array.isArray(d)) return d.map((x) => x.msg || String(x)).join("; ");
  } catch {
    /* raw text */
  }
  return raw;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const r = await fetch(path, { ...init, headers, credentials: "include" });
  if (r.status === 401 && !path.includes("/auth/login")) {
    if (window.location.pathname !== "/login") window.location.assign("/login");
    throw new ApiError(401, "未登录");
  }
  if (!r.ok) {
    const t = await r.text();
    throw new ApiError(r.status, parseDetail(t) || r.statusText);
  }
  const ct = r.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try {
      return (await r.json()) as T;
    } catch {
      throw new ApiError(r.status, "响应无效");
    }
  }
  return r as unknown as T;
}

export type Me = {
  ok: boolean;
  user: { username: string; name?: string; role: string; products: string[] };
  week?: string;
  can_log_hours?: boolean;
  can_edit_requirements?: boolean;
  can_manage_users?: boolean;
  can_delete?: boolean;
  can_protect?: boolean;
  can_product_versions?: boolean;
  can_auth_pack?: boolean;
  migrated_url?: string;
};
