// 日报周报 Agent —— 占位页面
// 定位：对每日工单处理情况和项目进度进行智能总结，自动生成日报/周报草稿。
// 现状：Agent 对话与总结逻辑待后端接口就绪后接入（参考 ChatPanel.tsx 的 SSE 流式对话模式），
// 当前先占位展示入口与功能说明，避免二级菜单出现死链接。
export default function DailySummaryAgent() {
  return (
    <div style={{ padding: '48px 24px', textAlign: 'center' }}>
      <div style={{ fontSize: 48, marginBottom: 16 }}>🤖</div>
      <p style={{ fontSize: 16, fontWeight: 600, color: '#333' }}>日报周报 Agent 功能建设中</p>
      <p style={{ fontSize: 13, color: '#999', marginTop: 8, lineHeight: 1.6 }}>
        上线后可自动汇总每日工单处理情况与项目进展，
        <br />
        一键生成日报 / 周报草稿。敬请期待。
      </p>
    </div>
  );
}
