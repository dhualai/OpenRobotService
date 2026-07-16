"""
USP 智能诊断 Agent — Streamlit 测试前端

启动方式:
    cd backend
    streamlit run streamlit_app.py

需要先启动 FastAPI 后端:
    cd backend
    python -m app.main
"""
import streamlit as st
import requests
import json
import time
import uuid

# ============================================================
# 配置
# ============================================================
st.set_page_config(
    page_title="USP 智能诊断",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

API_BASE = "http://localhost:8400"


def fix_image_urls(text: str) -> str:
    """将相对路径的图片 URL 转为指向 FastAPI 后端的绝对路径"""
    import re
    return re.sub(
        r'!\[([^\]]*)\]\((/api/media/[^)]+)\)',
        rf'![\1]({API_BASE}\2)',
        text,
    )

# ============================================================
# 会话初始化
# ============================================================
if "session_id" not in st.session_state:
    st.session_state.session_id = f"web-{uuid.uuid4().hex[:8]}"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "timing_log" not in st.session_state:
    st.session_state.timing_log = []


def new_session():
    st.session_state.session_id = f"web-{uuid.uuid4().hex[:8]}"
    st.session_state.messages = []
    st.session_state.timing_log = []


# ============================================================
# 流式调用后端
# ============================================================
def call_agent_stream(query: str):
    """调用 /api/ai/qa/ask/stream，逐 event yield"""
    url = f"{API_BASE}/api/ai/qa/ask/stream"
    body = json.dumps({"session_id": st.session_state.session_id, "query": query})
    t0 = time.perf_counter()

    try:
        resp = requests.post(url, data=body, headers={"Content-Type": "application/json"},
                             stream=True, timeout=120)
        resp.raise_for_status()
    except Exception as e:
        yield {"error": str(e)}
        return

    first_token = True
    result_data = None
    current_event = None
    full_text = ""
    status_text = ""

    for line_bytes in resp.iter_lines():
        line = line_bytes.decode("utf-8", errors="ignore").strip()
        if not line:
            continue

        if line.startswith("event: "):
            current_event = line[7:].strip()
            continue

        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            # 状态事件
            if "stage" in ev:
                stage_map = {
                    "retrieving": "📚 正在检索知识库…",
                    "analyzing": "🧠 正在分析…",
                    "agent_thinking": "🧠 正在分析…",
                }
                status_text = stage_map.get(ev["stage"], f"⏳ {ev['stage']}…")
                yield {"status": status_text}

            # token 事件
            if "token" in ev:
                if first_token:
                    first_token = False
                    ttft = round((time.perf_counter() - t0) * 1000)
                    yield {"ttft": ttft}
                full_text += ev["token"]
                yield {"token": ev["token"]}

            # result 事件
            if current_event == "result":
                result_data = ev
                current_event = None

    total_ms = round((time.perf_counter() - t0) * 1000)
    yield {"done": True, "text": full_text, "result": result_data, "total_ms": total_ms}


# ============================================================
# UI 布局
# ============================================================
st.title("🤖 USP 智能诊断 Agent")
st.caption(f"Session: `{st.session_state.session_id}`")

# ---- 侧边栏 ----
with st.sidebar:
    st.header("⚙️ 控制")
    if st.button("🔄 新建会话", use_container_width=True):
        new_session()
        st.rerun()

    st.divider()
    st.header("📊 性能计时 (ms)")

    if st.session_state.timing_log:
        for i, t in enumerate(reversed(st.session_state.timing_log[-5:])):
            with st.expander(f"消息 #{len(st.session_state.timing_log) - i}", expanded=(i == 0)):
                cols = st.columns(3)
                cols[0].metric("首token", f"{t.get('ttft', '-')}ms" if t.get('ttft') else "-")
                cols[1].metric("检索", f"{t.get('retrieve', '-')}ms" if t.get('retrieve') else "-")
                cols[2].metric("总耗时", f"{t.get('total_roundtrip', '-')}ms" if t.get('total_roundtrip') else "-")
                if t.get("prompt_chars"):
                    st.caption(f"Prompt: {t['prompt_chars']} 字")
    else:
        st.caption("发送消息后显示")

    st.divider()
    st.header("📋 Agent 状态")
    # 取最后一条 assistant 消息的状态
    agent_state = {}
    for msg in reversed(st.session_state.messages):
        if msg["role"] == "assistant":
            agent_state = msg.get("agent_state", {})
            break
    if agent_state:
        st.caption(f"阶段: **{agent_state.get('phase', '?')}**")
        st.caption(f"轮次: {agent_state.get('diagnosis_rounds', '?')}")
        hyps = agent_state.get("hypotheses", [])
        if hyps:
            st.caption(f"推测: {', '.join(hyps)}")
    else:
        st.caption("暂无")

# ---- 聊天区域 ----
chat_container = st.container()
with chat_container:
    for msg in st.session_state.messages:
        role = msg["role"]
        content = msg.get("content", "")

        if role == "user":
            with st.chat_message("user"):
                st.markdown(fix_image_urls(content))
        else:
            with st.chat_message("assistant"):
                st.markdown(fix_image_urls(content))

                # 工单信息
                ticket = msg.get("ticket", {})
                if ticket:
                    with st.expander("🎫 工单详情", expanded=True):
                        cols = st.columns(3)
                        cols[0].metric("类型", ticket.get("type", "-"))
                        cols[1].metric("优先级", ticket.get("priority", "-"))
                        cols[2].metric("状态", ticket.get("status", "-"))
                        if ticket.get("title"):
                            st.caption(f"📝 {ticket['title']}")
                        if ticket.get("description"):
                            st.caption(ticket["description"])
                        # 类型专属字段
                        extra_fields = []
                        if ticket.get("location"):
                            extra_fields.append(f"现场位置: {ticket['location']}")
                        if ticket.get("robot_type"):
                            extra_fields.append(f"机器人: {ticket['robot_type']}")
                        if ticket.get("fault_code"):
                            extra_fields.append(f"故障码: {ticket['fault_code']}")
                        if ticket.get("severity"):
                            extra_fields.append(f"严重程度: {ticket['severity']}")
                        if ticket.get("version"):
                            extra_fields.append(f"版本: {ticket['version']}")
                        if extra_fields:
                            st.caption(" | ".join(extra_fields))

                # 计时
                timing = msg.get("timing", {})
                if timing:
                    parts = []
                    for k, label in [("ttft", "首token"), ("retrieve", "检索"),
                                     ("llm_agent", "LLM"), ("total_roundtrip", "总耗时")]:
                        v = timing.get(k)
                        if v is not None:
                            parts.append(f"{label}={v}ms")
                    if timing.get("prompt_chars"):
                        parts.append(f"prompt={timing['prompt_chars']}字")
                    st.caption("⏱ " + " | ".join(parts))

# ---- 输入框 ----
if prompt := st.chat_input("描述您遇到的 AGV/AMR 问题…"):
    # 添加用户消息到历史（不在循环中显式渲染，等 rerun 后统一显示）
    st.session_state.messages.append({"role": "user", "content": prompt})

    # 流式渲染 assistant 回复
    with chat_container:
        with st.chat_message("assistant"):
            text_placeholder = st.empty()
            status_placeholder = st.empty()
            full_text = ""
            timing_info = {}

            for event in call_agent_stream(prompt):
                if "error" in event:
                    st.error(f"❌ {event['error']}")
                    st.session_state.messages.append({
                        "role": "assistant", "content": f"❌ 错误: {event['error']}",
                        "timing": {}, "agent_state": {},
                    })
                    break

                if "status" in event:
                    status_placeholder.caption(event["status"])

                if event.get("ttft"):
                    timing_info["ttft"] = event["ttft"]

                if event.get("token"):
                    full_text += event["token"]
                    text_placeholder.markdown(fix_image_urls(full_text) + "▌")

                if event.get("done"):
                    status_placeholder.empty()
                    text_placeholder.markdown(fix_image_urls(full_text))

                    result = event.get("result", {}) or {}
                    timing_info["total_roundtrip"] = event.get("total_ms", 0)

                    # 从服务器返回中提取细分计时
                    server_timing = result.get("timing", {})
                    if server_timing:
                        timing_info["retrieve"] = server_timing.get("retrieve")
                        timing_info["llm_agent"] = server_timing.get("llm_agent")
                        timing_info["prompt_chars"] = server_timing.get("prompt_chars")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": full_text,
                        "timing": timing_info,
                        "agent_state": result.get("agent_state", {}),
                        "ticket": result.get("ticket", {}),
                    })
                    if timing_info:
                        st.session_state.timing_log.append(timing_info)
                    break

    st.rerun()
