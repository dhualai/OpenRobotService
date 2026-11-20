export const LC_BANDS = [
  { id: "pre", label: "设计中" },
  { id: "dev", label: "开发中" },
  { id: "test", label: "测试中" },
  { id: "done", label: "已完成" },
] as const;

export function lcBandRank(band?: string | null): number {
  const i = LC_BANDS.findIndex((x) => x.id === band);
  return i < 0 ? 0 : i;
}

export function bandLabel(band?: string | null, _progress?: number | null): string {
  const b = (band || "").toLowerCase();
  if (b === "pre") return "设计中";
  if (b === "dev") return "开发中";
  if (b === "test") return "测试中";
  if (b === "joint_test") return "联合测试中";
  if (b === "done") return "已完成";
  return band || "";
}

export const REQ_STATUS: Record<string, string> = {
  draft: "草稿",
  review: "待评审",
  pending_link: "待关联",
  developing: "开发中",
  done: "已完成",
};

export const REQ_VERDICT: Record<string, string> = {
  pass: "通过",
  clarify: "待明确",
  reject: "拒绝",
};
