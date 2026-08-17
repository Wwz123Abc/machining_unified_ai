"""机械智能制造统一工作台的 Streamlit 入口。"""

from __future__ import annotations

import time
from pathlib import Path

import streamlit as st
from PIL import Image

from machining_unified.cad.retrieval import load_cad_catalog
from machining_unified.cad.viewer import step_mesh_payload
from machining_unified.config.logging_setup import configure_logging
from machining_unified.retrieval.cad_rag import generate_rag_explanation
from machining_unified.services.model_search import save_step_upload, search_by_image, search_by_step, search_by_text
from machining_unified.ui import components, retrieval_components
from machining_unified.ui.status_components import render_catalog_browser, render_sidebar_status
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
    """集中初始化跨重跑的会话状态。"""

    # 检索结果只在提交那一次重跑里产生。存入会话状态后，调整返回数量、
    # 切换查询方式等交互引发的重跑才不会把已有结果整块清空。
    st.session_state.setdefault("model_search", None)


initialize_state()
catalog = load_cad_catalog()

components.render_sidebar()
with st.sidebar:
    st.divider()
    st.markdown("##### 全局检索设置")
    top_k = st.slider(
        "返回数量上限",
        min_value=1,
        max_value=8,
        value=3,
        key="top_k",
        persist_state="session",
        help="实际返回数由分数断崖自动截断决定，可能少于这个上限；这里只设置硬顶。",
    )
    st.caption(f"当前 CAD 目录包含 {len(catalog)} 个可检索记录。")
    render_sidebar_status()

submitted, query_mode, query_text, uploaded_file, progress_slot = (
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
                results = search_by_step(query_path, top_k=top_k)
                # 临时文件稍后删除，这里先把网格取出来随结果一起保存。
                # 三角化失败不能连累检索：几何提取本身有文本摘要降级路径，
                # 检索结果此时已经产生，仅仅是画不出预览而已。
                try:
                    query_mesh = step_mesh_payload(str(query_path.resolve()))
                except (OSError, ValueError, RuntimeError) as mesh_error:
                    query_mesh = None
                    mesh_warning = f"查询模型无法三维预览：{mesh_error}"
                    logger.warning(
                        "查询模型网格提取失败，检索结果不受影响",
                        extra={"file_name": getattr(uploaded_file, "name", None)},
                    )
                else:
                    mesh_warning = None
                # DeepSeek 说明是可选增强：它失败或超时不能连累已经算出的
                # 几何/语义检索结果——那些结果此时已经产生，不该被一起丢弃。
                try:
                    explanation = generate_rag_explanation(results.query, results.semantic)
                except Exception:  # noqa: BLE001 - 外部服务边界，失败模式不可枚举
                    explanation = None
                    logger.exception(
                        "STEP 差异说明生成失败，检索结果不受影响",
                        extra={"file_name": getattr(uploaded_file, "name", None)},
                    )
                status.update(label="STEP 模型检索完成", state="complete", expanded=False)
            st.session_state.model_search = {
                "mode": query_mode,
                "results": results,
                "query_mesh": query_mesh,
                "explanation": explanation,
                "mesh_warning": mesh_warning,
            }
            logger.info(
                "STEP 检索完成",
                extra={
                    "query_mode": query_mode,
                    "file_name": getattr(uploaded_file, "name", None),
                    "top_k": top_k,
                    "geometry_hits": len(results.geometry),
                    "semantic_hits": len(results.semantic),
                    "triangle_count": query_mesh["triangle_count"] if query_mesh else None,
                    "geometry_confidence": results.query["features"].get("geometry_confidence"),
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
                results = search_by_text(query_text.strip(), top_k=top_k)
                status.update(label="文字模型检索完成", state="complete", expanded=False)
            st.session_state.model_search = {"mode": query_mode, "results": results}
            logger.info(
                "文字检索完成",
                extra={
                    "query_mode": query_mode,
                    "query_length": len(query_text.strip()),
                    "top_k": top_k,
                    "semantic_hits": len(results.semantic),
                    "hybrid_hits": len(results.hybrid),
                    "families": list(results.families),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        except Exception as error:  # noqa: BLE001 - UI 请求边界，同 STEP 分支说明
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
                results = search_by_image(image, catalog, top_k=top_k)
                status.update(label="图片模型检索完成", state="complete", expanded=False)
            st.session_state.model_search = {"mode": query_mode, "results": results, "query_image": image}
            logger.info(
                "图片检索完成",
                extra={
                    "query_mode": query_mode,
                    "file_name": getattr(uploaded_file, "name", None),
                    "image_size": list(image.size),
                    "top_k": top_k,
                    "visual_hits": len(results.visual),
                    "visual_method": results.visual[0].method if results.visual else None,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        except Exception as error:  # noqa: BLE001 - UI 请求边界，同 STEP 分支说明
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
        if search_state.get("mesh_warning"):
            st.warning(search_state["mesh_warning"], icon=":material/3d_rotation:")
        # 查询模型作为几何结果网格的首个卡片，与候选同行同尺寸，便于直接比对形状。
        retrieval_components.render_geometry_results(
            results.geometry, query_mesh=search_state["query_mesh"]
        )
        # 中文工程语义结果不再单独展示：24 条库时它与几何区 98.6% 重复，
        # 508 条库时它与混合区各说各话、自称"名次仅供参考"，留着只会让用户
        # 纠结信哪个区。底层管线仍在跑——几何重排候选与 STEP 差异说明都消费它，
        # 只是不再单独摆出一块结果。
        if search_state["explanation"]:
            with st.container(border=True):
                st.markdown("#### 基于检索证据的差异说明")
                st.write(search_state["explanation"])
    elif query_mode == "文字描述":
        retrieval_components.render_hybrid_results(results.hybrid, results.families)
    else:
        st.image(search_state["query_image"], caption="查询图片", width=320)
        retrieval_components.render_image_results(results.visual)

render_catalog_browser()
