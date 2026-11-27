// 【AI 检视测试样本 F】深度检视专属：前端 XSS + 开放重定向 + 行内评论验证
// 本文件为 AI Review 观察期测试样本（伪代码，不进构建），不合并。
// 预期快筛：0 命中、保持沉默（前端写法无对应内容规则）。
// 预期 AI 深度检视（打 ai-review 标签触发，按安全红线 12 条）：
//   1. XSS 高危：用户输入 rawHtml 未转义直接进 DOM（约 17 行）；
//   2. 开放重定向高危：returnUrl 来自 URL 参数、未做同源白名单（约 24 行）；
//   3. 两条 location 行号均落在 diff 新增行集合内 → 预期 Files changed
//      出现行内评论（各钉 1 条），总结报告对应剔除、避免重复。
import React from "react";

export function NoticeCard({
  rawHtml,
  returnUrl,
}: {
  rawHtml: string;
  returnUrl: string;
}) {
  return (
    <div>
      {/* 预期 AI 1：未转义直插 DOM */}
      <div dangerouslySetInnerHTML={{ __html: rawHtml }} />
      <a
        href="#"
        onClick={() => {
          // 预期 AI 2：重定向目标未校验
          window.location.href = returnUrl;
        }}
      >
        返回
      </a>
    </div>
  );
}
