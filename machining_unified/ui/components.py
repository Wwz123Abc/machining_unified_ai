"""工艺智核的可复用 Streamlit 页面组件。"""

# Any 用于标注 Streamlit 的上传对象和页面占位容器。
from typing import Any

import streamlit as st


def render_sidebar() -> None:
    """渲染统一工作台的系统状态侧栏。"""

    # with 代码块内的所有组件都会被放入 Streamlit 侧栏。
    with st.sidebar:
        st.markdown(
            """
            <div class="side-brand">
              <div class="side-kicker">MACHINING INTELLIGENCE</div>
              <div class="side-title">工艺智核</div>
              <div class="side-line"></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.badge("知识库在线", icon=":material/radio_button_checked:", color="green")
        st.caption("CAD 检索 · 多模态召回 · 企业资料问答")
        st.space("medium")
        st.markdown("##### 使用提示")
        st.caption("模型检索和企业资料问答相互独立，所有结果均保留来源和证据类型。")
        st.space("medium")
        st.warning(
            "几何候选、检索排序和装配推断在生产使用前均须由工程师复核。",
            icon=":material/engineering:",
        )


def render_workspace_selector() -> str:
    """在统一入口中选择功能区，避免隐藏或不可达的检索分支。"""

    with st.container(horizontal=True, horizontal_alignment="center"):
        selected = st.segmented_control(
            "工作区",
            ["模型检索", "企业资料问答"],
            default="模型检索",
            key="workspace_mode",
            persist_state="session",
        )
    return selected or "模型检索"


def render_model_search_workbench() -> tuple[bool, str, str, Any, bool, Any]:
    """渲染统一模型检索入口并返回已批量提交的查询参数。"""

    with st.container(key="command_deck", gap=None):
        st.markdown(
            """
            <div class="console-topbar">
              <div class="console-brand"><span class="brand-mark">◈</span> 工艺智核 <small>MODEL RETRIEVAL</small></div>
              <div class="console-status"><i></i> CAD INDEX / ONLINE</div>
            </div>
            <div class="tech-rail">
              <div><i>几何解析</i><b>OCP + XCAF</b><span>STEP 结构事实</span></div>
              <div><i>语义检索</i><b>BGE + Chroma</b><span>中文工程语义</span></div>
              <div><i>融合证据</i><b>BM25 + CLIP</b><span>词法与多模态补充</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        intro_column, input_column = st.columns([1.05, 1.55], gap="medium", vertical_alignment="center")
        with intro_column:
            st.markdown(
                """
                <section class="console-intro">
                  <div class="eyebrow">MULTIMODAL CAD RETRIEVAL</div>
                  <h1>从模型与描述<br><em>找到工程近邻</em></h1>
                  <p>结构化几何负责严格比较，语义与视觉分支负责扩大召回；不同分数分别展示。</p>
                  <div class="console-signals">
                    <div><b>01</b><span>几何事实</span></div>
                    <div><b>02</b><span>混合召回</span></div>
                    <div><b>03</b><span>三维核对</span></div>
                  </div>
                </section>
                """,
                unsafe_allow_html=True,
            )

        with input_column:
            query_mode = st.segmented_control(
                "查询方式",
                ["STEP 模型", "文字描述", "零件图片"],
                default="STEP 模型",
                key="model_query_mode",
                persist_state="session",
            ) or "STEP 模型"
            use_unified = st.toggle(
                "启用 CLIP 统一多模态补充召回",
                value=False,
                key="use_unified_multimodal",
                help="适合比较整体外形和图文语义；不会替代严格尺寸与特征检索。",
            )
            query_text = ""
            uploaded_file = None
            with st.form(f"model_search_{query_mode}", border=False):
                if query_mode == "STEP 模型":
                    uploaded_file = st.file_uploader(
                        "上传查询 STEP/STP 模型",
                        type=["step", "stp"],
                        key="model_step_upload",
                    )
                elif query_mode == "文字描述":
                    query_text = st.text_area(
                        "描述要查找的零件",
                        height=130,
                        key="model_text_query",
                        placeholder="例如：细长轴类零件，包含多级圆柱面和端面，用于旋转支承……",
                    )
                else:
                    uploaded_file = st.file_uploader(
                        "上传机械示意图或 CAD 截图",
                        type=["png", "jpg", "jpeg", "webp"],
                        key="model_image_upload",
                    )
                submitted = st.form_submit_button(
                    "开始模型检索",
                    type="primary",
                    icon=":material/manage_search:",
                    width="stretch",
                )

        progress_slot = st.container()
        st.markdown(
            """
            <div class="console-footer">
              <span>● OCP GEOMETRY READY</span><span>◌ CHROMA VECTOR STORE</span><span>◈ EVIDENCE SEPARATED</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    return submitted, query_mode, query_text, uploaded_file, use_unified, progress_slot


def render_enterprise_header() -> None:
    """渲染企业资料问答的工业风页头。"""

    with st.container(key="command_deck", gap=None):
        st.markdown(
            """
            <div class="console-topbar">
              <div class="console-brand"><span class="brand-mark">◈</span> 工艺智核 <small>ENTERPRISE KNOWLEDGE</small></div>
              <div class="console-status"><i></i> EVIDENCE BASE / ONLINE</div>
            </div>
            <div class="tech-rail">
              <div><i>企业模型</i><b>STEP 几何</b><span>可追溯模型事实</span></div>
              <div><i>装配资料</i><b>BOM + 图纸</b><span>图号与来源优先</span></div>
              <div><i>回答约束</i><b>Evidence First</b><span>事实逐条引用</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("### 企业机械资料问答")
        st.caption("检索 STEP、装配 BOM 与工程图 PDF；企业事实必须附带 [S#] 证据编号。")
