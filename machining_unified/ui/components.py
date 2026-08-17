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
        st.caption("CAD 检索 · 多模态召回")
        st.space("medium")
        st.markdown("##### 使用提示")
        st.caption("几何、语义与视觉三路结果相互独立，均保留来源和证据类型。")
        st.space("medium")
        st.warning(
            "几何候选、检索排序和装配推断在生产使用前均须由工程师复核。",
            icon=":material/engineering:",
        )


def render_model_search_workbench() -> tuple[bool, str, str, Any, Any]:
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
              <div><i>融合证据</i><b>BM25 + 知识图谱</b><span>词法与图谱融合</span></div>
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
    return submitted, query_mode, query_text, uploaded_file, progress_slot
