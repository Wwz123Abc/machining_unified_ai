"""机械智能制造统一工作台的 Streamlit 入口。"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st
from PIL import Image

from machining_unified.cad.retrieval import load_cad_catalog
from machining_unified.cad.viewer import render_step_payload, step_mesh_payload
from machining_unified.config.logging_setup import configure_logging
from machining_unified.knowledge.enterprise import answer_assistant_question, answer_enterprise_question
from machining_unified.retrieval.cad_rag import generate_rag_explanation
from machining_unified.services.model_search import save_step_upload, search_by_image, search_by_step, search_by_text
from machining_unified.storage.chat_history import append_message, list_conversations, load_conversation, new_conversation_id
from machining_unified.ui import components, retrieval_components
from machining_unified.ui.styles import apply_industrial_style


# 入口负责配置日志；库层只取 logger。该函数幂等，可安全承受 Streamlit 的反复重跑。
logger = configure_logging()

st.set_page_config(
    page_title="工艺智核｜机械智能制造统一工作台",
    page_icon=":material/precision_manufacturing:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_industrial_style()


def initialize_state() -> None:
    """集中初始化跨重跑、跨工作区的会话状态。"""

    st.session_state.setdefault("chat_messages", [])
    st.session_state.setdefault("conversation_id", new_conversation_id())
    st.session_state.setdefault("loaded_conversation_id", None)
    # 检索结果只在提交那一次重跑里产生。存入会话状态后，调整返回数量、
    # 切换查询方式等交互引发的重跑才不会把已有结果整块清空。
    st.session_state.setdefault("model_search", None)


def start_new_conversation() -> None:
    """在控件回调阶段重置企业资料对话。"""

    st.session_state.chat_messages = []
    st.session_state.conversation_id = new_conversation_id()
    st.session_state.loaded_conversation_id = None
    st.session_state.history_picker = "新对话"


def stream_answer_text(answer: str):
    """按自然段流式写出已核验完成的回答，不修改文字和引用。"""

    for line in answer.splitlines(keepends=True) or [answer]:
        yield line


initialize_state()
catalog = load_cad_catalog()
catalog_by_id = {str(record["part_id"]): record for record in catalog}

components.render_sidebar()
with st.sidebar:
    st.divider()
    st.markdown("##### 全局检索设置")
    top_k = st.slider("返回数量", min_value=1, max_value=8, value=3, key="top_k", persist_state="session")
    st.caption(f"当前 CAD 目录包含 {len(catalog)} 个可检索记录。")

workspace = components.render_workspace_selector()


if workspace == "模型检索":
    submitted, query_mode, query_text, uploaded_file, use_unified, progress_slot = (
        components.render_model_search_workbench()
    )
    if submitted:
        if query_mode in {"STEP 模型", "零件图片"} and uploaded_file is None:
            st.error("请先上传查询文件。", icon=":material/upload_file:")
        elif query_mode == "文字描述" and not query_text.strip():
            st.error("请先输入零件描述。", icon=":material/input:")
        elif query_mode == "STEP 模型":
            # UI 是请求边界：这里刻意捕获 BaseException 之外的所有异常，
            # 避免一次查询失败让整页崩溃；代价由 logger.exception 记录完整堆栈补偿。
            query_path: Path | None = None
            started = time.perf_counter()
            try:
                with progress_slot.status("正在解析 STEP 并执行多路检索", expanded=True) as status:
                    query_path = save_step_upload(uploaded_file)
                    st.write(":material/view_in_ar: 提取 OCP/XCAF 几何事实")
                    results = search_by_step(query_path, top_k=top_k, use_unified=use_unified)
                    # 临时文件稍后删除，这里先把网格取出来随结果一起保存。
                    query_mesh = step_mesh_payload(str(query_path.resolve()))
                    status.update(label="STEP 模型检索完成", state="complete", expanded=False)
                st.session_state.model_search = {
                    "mode": query_mode,
                    "results": results,
                    "query_mesh": query_mesh,
                    "explanation": generate_rag_explanation(results["query"], results["semantic"]),
                }
                logger.info(
                    "STEP 检索完成",
                    extra={
                        "query_mode": query_mode,
                        "file_name": getattr(uploaded_file, "name", None),
                        "top_k": top_k,
                        "use_unified": use_unified,
                        "geometry_hits": len(results["geometry"]),
                        "semantic_hits": len(results["semantic"]),
                        "unified_hits": len(results["unified"]),
                        "triangle_count": query_mesh["triangle_count"],
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            except Exception as error:
                st.session_state.model_search = None
                logger.exception(
                    "STEP 检索失败",
                    extra={
                        "query_mode": query_mode,
                        "file_name": getattr(uploaded_file, "name", None),
                        "top_k": top_k,
                        "use_unified": use_unified,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
                st.error(f"STEP 检索失败：{error}", icon=":material/error:")
            finally:
                # Windows 上文件可能仍被句柄占用；清理失败不应掩盖上面的真实错误。
                if query_path is not None:
                    try:
                        query_path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("查询临时文件清理失败", extra={"path": str(query_path)})

        elif query_mode == "文字描述":
            started = time.perf_counter()
            try:
                with progress_slot.status("正在执行中文语义与工程混合检索", expanded=True) as status:
                    results = search_by_text(query_text.strip(), top_k=top_k, use_unified=use_unified)
                    status.update(label="文字模型检索完成", state="complete", expanded=False)
                st.session_state.model_search = {"mode": query_mode, "results": results}
                logger.info(
                    "文字检索完成",
                    extra={
                        "query_mode": query_mode,
                        "query_length": len(query_text.strip()),
                        "top_k": top_k,
                        "use_unified": use_unified,
                        "semantic_hits": len(results["semantic"]),
                        "hybrid_hits": len(results["hybrid"]),
                        "families": results["families"],
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            except Exception as error:
                st.session_state.model_search = None
                logger.exception(
                    "文字检索失败",
                    extra={
                        "query_mode": query_mode,
                        "query_length": len(query_text.strip()),
                        "top_k": top_k,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
                st.error(f"文字模型检索失败：{error}", icon=":material/error:")

        else:
            started = time.perf_counter()
            try:
                image = Image.open(uploaded_file).convert("RGB")
                with progress_slot.status("正在执行视觉模型检索", expanded=True) as status:
                    results = search_by_image(image, catalog, top_k=top_k, use_unified=use_unified)
                    status.update(label="图片模型检索完成", state="complete", expanded=False)
                st.session_state.model_search = {"mode": query_mode, "results": results, "query_image": image}
                logger.info(
                    "图片检索完成",
                    extra={
                        "query_mode": query_mode,
                        "file_name": getattr(uploaded_file, "name", None),
                        "image_size": list(image.size),
                        "top_k": top_k,
                        "use_unified": use_unified,
                        "visual_hits": len(results["visual"]),
                        "visual_method": results["visual"][0]["method"] if results["visual"] else None,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            except Exception as error:
                st.session_state.model_search = None
                logger.exception(
                    "图片检索失败",
                    extra={
                        "query_mode": query_mode,
                        "file_name": getattr(uploaded_file, "name", None),
                        "top_k": top_k,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
                st.error(f"图片模型检索失败：{error}", icon=":material/error:")

    # 从会话状态渲染：切换查询方式时不展示上一种方式留下的结果。
    search_state = st.session_state.model_search
    if search_state and search_state["mode"] == query_mode:
        results = search_state["results"]
        if query_mode == "STEP 模型":
            st.markdown("#### 查询模型 3D 预览")
            render_step_payload(search_state["query_mesh"], key="query-step-model")
            retrieval_components.render_geometry_results(results["geometry"])
            retrieval_components.render_semantic_results(results["semantic"], catalog_by_id, "step-semantic")
            if results["unified"]:
                retrieval_components.render_unified_results(results["unified"], catalog_by_id, "step-unified")
            if search_state["explanation"]:
                with st.container(border=True):
                    st.markdown("#### 基于检索证据的差异说明")
                    st.write(search_state["explanation"])
        elif query_mode == "文字描述":
            retrieval_components.render_semantic_results(results["semantic"], catalog_by_id, "text-semantic")
            retrieval_components.render_hybrid_results(results["hybrid"], results["families"])
            if results["unified"]:
                retrieval_components.render_unified_results(results["unified"], catalog_by_id, "text-unified")
        else:
            st.image(search_state["query_image"], caption="查询图片", width=320)
            retrieval_components.render_image_results(results["visual"])
            if results["unified"]:
                retrieval_components.render_unified_results(results["unified"], catalog_by_id, "image-unified")


else:
    components.render_enterprise_header()
    with st.sidebar:
        st.divider()
        answer_mode = st.segmented_control(
            "回答模式",
            ["严谨知识库", "AI 助手"],
            default="严谨知识库",
            key="answer_mode",
            persist_state="session",
            help="AI 助手允许补充通用建议，但必须与企业资料事实明确区分。",
        ) or "严谨知识库"
        conversations = list_conversations()
        labels = {item["id"]: item["label"] for item in conversations}
        selected_history = st.selectbox(
            "历史对话",
            ["新对话", *labels],
            format_func=lambda item: item if item == "新对话" else labels[item],
            key="history_picker",
            persist_state="session",
        )
        if selected_history != "新对话" and selected_history != st.session_state.loaded_conversation_id:
            st.session_state.chat_messages = load_conversation(selected_history)
            st.session_state.conversation_id = selected_history
            st.session_state.loaded_conversation_id = selected_history
        st.button("新建对话", icon=":material/add_comment:", on_click=start_new_conversation)

    suggestion = None
    if not st.session_state.chat_messages:
        suggestion = st.pills(
            "可尝试提问",
            [
                "630DTXT806-300-000 装配包含哪些 BOM 零件？",
                "DTXT806-300-012 的材料和表面处理是什么？",
                "DTXT706-200-005 工程图有哪些要求？",
            ],
            selection_mode="single",
            key="question_suggestions",
        )

    for message in st.session_state.chat_messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message.get("evidence"):
                with st.expander("查看证据来源"):
                    retrieval_components.render_enterprise_evidence(message["evidence"])
            elif message.get("source_refs"):
                st.caption("历史资料引用：" + "、".join(message["source_refs"]))

    prompt = st.chat_input(
        "提出关于零件、BOM、工程图或装配资料的问题",
        submit_mode="disable",
        key="enterprise_chat_input",
    ) or suggestion
    if prompt:
        current_mode = "strict" if answer_mode == "严谨知识库" else "assistant"
        user_message = {"role": "user", "content": prompt, "mode": current_mode}
        st.session_state.chat_messages.append(user_message)
        append_message(st.session_state.conversation_id, "user", prompt, current_mode)
        with st.chat_message("user"):
            st.write(prompt)
        with st.chat_message("assistant", avatar=":material/engineering:"):
            with st.status("正在检索企业资料并生成回答…", expanded=True) as status:
                if answer_mode == "严谨知识库":
                    result = answer_enterprise_question(prompt, top_k=top_k, history=st.session_state.chat_messages)
                else:
                    st.info("企业资料事实带 [S#] 引用；其余内容必须标记为通用建议或推测。")
                    result = answer_assistant_question(prompt, top_k=top_k, history=st.session_state.chat_messages)
                status.update(label="回答已生成", state="complete", expanded=False)
            streamed_answer = st.write_stream(stream_answer_text(result["answer"]))
            if result.get("warning"):
                st.warning(result["warning"], icon=":material/warning:")
            with st.expander("查看证据来源", expanded=True):
                retrieval_components.render_enterprise_evidence(result["evidence"])

        source_refs = [item["document"].metadata["source_id"] for item in result["evidence"]]
        assistant_message = {
            "role": "assistant",
            "content": streamed_answer,
            "evidence": result["evidence"],
            "mode": current_mode,
            "source_refs": source_refs,
        }
        st.session_state.chat_messages.append(assistant_message)
        append_message(st.session_state.conversation_id, "assistant", streamed_answer, current_mode, source_refs)
