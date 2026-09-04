"""capabilities.tools — 具体能力（可被 Supervisor 调度的 worker）

每个文件定义一个 BaseCapability 子类，继承即自动注册到 CapabilityRegistry：
  - log_analyze: 日志分析
  - retrieve_history: 历史工单方案检索
  - retrieve_troubleshooting: 排查树检索
  - code_search: 代码检索
  - image_analyze: 图片分析
  - attachment_parse: 非图片附件解析
  - ticket_ref: @# 跨工单引用（确定性 + 大脑决策双形态）

本目录模块由 capabilities/__init__ 导入以触发自动注册。
"""
