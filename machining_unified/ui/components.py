"""工艺智核的可复用 Streamlit 页面组件。"""

# Any 用于标注 Streamlit 的占位对象和灵活的 RAG 返回字典。
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
        st.caption("工艺 RAG · CAD 检索 · 企业资料问答")
        st.space("medium")
        st.markdown("##### 使用提示")
        st.caption("工艺推荐、模型检索和企业资料问答相互独立，所有结果均保留来源和证据类型。")
        st.space("medium")
        st.warning(
            "工艺参数、几何候选和装配推断在生产使用前均须由工程师复核。",
            icon=":material/engineering:",
        )


def render_workspace_selector() -> str:
    """在统一入口中选择功能区，避免隐藏或不可达的检索分支。"""

    with st.container(horizontal=True, horizontal_alignment="center"):
        selected = st.segmented_control(
            "工作区",
            ["工艺推荐", "模型检索", "企业资料问答"],
            default="工艺推荐",
            key="workspace_mode",
            persist_state="session",
        )
    return selected or "工艺推荐"


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


def render_workbench() -> tuple[bool, str, Any]:
    """渲染工艺工作台，返回提交状态、用户问题和主控台内的进度占位区。"""

    # key 让 CSS 能精确选中这个主控台容器；gap=None 交给自定义 CSS 控制间距。
    with st.container(key="command_deck", gap=None):
        st.markdown(
            """
            <div class="console-topbar">
              <div class="console-brand"><span class="brand-mark">◈</span> 工艺智核 <small>PROCESS INTELLIGENCE</small></div>
              <div class="console-status"><i></i> KNOWLEDGE BASE / ONLINE</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            """
            <div class="tech-rail">
              <div><i>模型推理</i><b>DeepSeek</b><span>中文工艺推理</span></div>
              <div><i>向量检索</i><b>Chroma + BGE-ZH</b><span>本地向量检索</span></div>
              <div><i>RAG 编排</i><b>LangChain 编排</b><span>结构化 RAG 链路</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # 左侧介绍产品能力，右侧容纳表单；比例让输入区获得更多空间。
        intro_column, input_column = st.columns([1.05, 1.55], gap="medium", vertical_alignment="center")
        with intro_column:
            st.markdown(
                """
                <section class="console-intro">
                  <div class="eyebrow">RAG-POWERED WORKBENCH</div>
                  <h1>把零件需求<br><em>变成加工路径</em></h1>
                  <p>识别零件特征，检索相似案例，再生成带有依据的工艺方案。</p>
                  <div class="console-signals">
                    <div><b>01</b><span>特征解析</span></div>
                    <div><b>02</b><span>案例检索</span></div>
                    <div><b>03</b><span>方案生成</span></div>
                  </div>
                </section>
                """,
                unsafe_allow_html=True,
            )

        with input_column:
            # 表单内修改输入不会立即触发 RAG，只有点击按钮才会提交。
            with st.form("process_request", border=False):
                st.markdown(
                    """
                    <div class="input-console-label">
                      <span>模型检索 / 工艺问答</span><small>文字描述或 STEP/STP 至少提供一种</small>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                # key 会把输入值存入 st.session_state，供“返回主界面”时清除。
                question = st.text_area(
                    "零件补充描述（可选）",
                    value="",
                    height=142,
                    key="process_question",
                    placeholder="可留空；如有需要可填写零件名称或补充说明……",
                    label_visibility="collapsed",
                )
                st.text_input(
                    "零件编号（可选）",
                    key="part_id",
                    placeholder="例如：PART-001；留空则自动生成可复现编号",
                    help="同一零件的文字、表格、图纸和 STEP 建议使用同一个 part_id。",
                )
                # 图纸先进入表单，只有点击提交后才执行 PDF 文本层/OCR 处理。
                st.file_uploader(
                    "上传二维图纸（PDF 或图片）",
                    type=["pdf", "png", "jpg", "jpeg", "webp", "bmp"],
                    key="drawing_upload",
                    help="当前优先支持 PDF 文字层；扫描图和图片需要 OCR 或人工确认。",
                )
                st.file_uploader(
                    "上传 3D 模型（STEP / STP）",
                    type=["step", "stp"],
                    key="cad_upload",
                    help="当前提取包围盒、拓扑数量和基础曲面类型；结果必须人工确认。",
                )
                st.markdown(
                    """
                    <div class="input-hints">
                      <span>材料</span><span>零件结构</span><span>关键尺寸</span><span>精度与粗糙度</span><span>特殊特征</span>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                submitted = st.form_submit_button(
                    "开始检索 / 工艺问答",
                    type="primary",
                    icon=":material/bolt:",
                    width="stretch",
                )

        # 为推演技术栈和进度条预留主控台内部区域。
        # 空占位符确定进度区位置；app.py 在提交后向其中临时写入内容。
        progress_slot = st.empty()

        st.markdown(
            """
            <div class="telemetry-band">
              <div class="telemetry-line"><span></span></div>
              <div class="telemetry-labels"><span>SYS / NOMINAL</span><span>AXIS / X·Y·Z READY</span><span>MODE / RAG INFERENCE</span><span>REV / 1.0</span></div>
            </div>
            <div class="console-footer">
              <span>● DEEPSEEK INFERENCE READY</span><span>◌ CHROMA VECTOR STORE</span><span>◈ BGE-ZH EMBEDDING</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 调用方既需要提交状态和问题，也需要精确放置进度条的占位对象。
    return submitted, question, progress_slot


def render_cad_search_result(cad_info: dict[str, Any], similar_cad: list[dict[str, Any]]) -> None:
    """渲染 CAD-only 检索结果，不触发工艺推荐。"""

    with st.container(border=True):
        st.markdown("#### 3D 模型检索结果")
        st.caption("当前模式只比较企业 CAD 目录中的几何特征，不生成加工工艺。")
        st.write(f"文件：{cad_info['file_name']}")
        st.write(f"part_id：{cad_info['part_id']}")
        st.write(f"解析器：{cad_info['features'].get('parser', '未知')}（置信度：{cad_info['features'].get('geometry_confidence', 'low')}）")
        st.json(cad_info["features"])

    with st.container(border=True):
        st.markdown("#### 相同或相似模型")
        if not similar_cad:
            st.warning("企业 CAD 目录中暂未找到可比较的模型。", icon=":material/search_off:")
        else:
            for index, item in enumerate(similar_cad, start=1):
                st.write(
                    f"{index}. {item.get('model_group_id', item['part_id'])}｜"
                    f"相似度 {item['score']:.3f}｜资料组内 STEP 数量 {item.get('component_count', 1)}｜"
                    f"代表文件 {item['file_name']}"
                )
                st.caption("相似依据：" + "、".join(item["reasons"]) if item["reasons"] else "相似依据不足")
                st.code(item.get("search_text", ""), language="text")


def render_result(response: dict[str, Any], get_source_label: Any) -> None:
    """渲染回答、结构化特征及可追溯来源。"""

    st.space("small")
    # 没有可靠资料时优先提示风险，但仍展示已检索到的内容供用户判断。
    if not response["is_grounded"]:
        if not response["results"] and response["top_score"] == 0:
            st.warning(
                "缺少材料或零件类型，系统已停止生成工艺路线。",
                icon=":material/info:",
            )
        else:
            st.warning(
                f"未找到足够相关的知识库依据（最高相似度：{response['top_score']:.3f}）。",
                icon=":material/search_off:",
            )

    with st.container(border=True):
        st.markdown("#### 推荐工艺方案")
        st.caption("由检索到的案例和工艺资料综合生成")
        st.markdown(response["answer"])

    # 将“模型识别结果”和“检索条件”并排展示，方便用户检查 RAG 是否可追溯。
    feature_column, filter_column = st.columns(2, vertical_alignment="top")
    with feature_column:
        with st.container(border=True):
            st.markdown("##### 识别到的零件特征")
            if response["features"]:
                st.json(response["features"])
            else:
                st.caption("未能提取结构化特征，已执行全库检索。")

    with filter_column:
        with st.container(border=True):
            st.markdown("##### 检索依据")
            st.metric("最高相似度", f"{response['top_score']:.3f}")
            if response["metadata_filter"]:
                st.json(response["metadata_filter"])
            else:
                st.caption("未使用元数据筛选。")

    with st.container(border=True):
        st.markdown("##### 参考资料")
        st.caption("展开查看本次生成所使用的案例与手册片段")
        # 每条来源都可展开查看，避免答案看起来像无法追溯的黑盒。
        for document, score in response["results"]:
            source_label = get_source_label(document)
            source_type = document.metadata.get("source_type", "未知来源")
            with st.expander(f"{source_label}｜相似度 {score:.3f}", icon=":material/article:"):
                st.caption(f"{source_type} · {document.metadata.get('source_file', '未标注文档')}")
                st.write(f"材料：{document.metadata.get('material', '未标注')}")
                st.write(f"零件类型：{document.metadata.get('part_type', '未标注')}")
                st.write(f"粗糙度：{document.metadata.get('roughness_ra', '未标注')}")
                st.write(f"精度要求：{document.metadata.get('precision_requirement', '未标注')}")
                st.write(f"热处理：{document.metadata.get('heat_treatment', '未标注')}")
                st.write(document.page_content.strip())

    # 设备、刀具和风险规则单独展示，避免把教学样例误认为企业设备能力。
    support_data = response.get("support_data", {})
    with st.expander("设备、刀具与风险校验（教学参考）", icon=":material/precision_manufacturing:"):
        st.caption("以下内容来自教学样例目录，最终设备、刀具和参数必须由工艺工程师确认。")
        st.write("推荐设备候选")
        st.json(support_data.get("equipment", []))
        st.write("刀具候选")
        st.json(support_data.get("tools", []))
        st.write("命中的工艺风险规则")
        st.json(support_data.get("risks", []))

    drawing_info = response.get("drawing_info")
    if drawing_info:
        with st.expander("图纸抽取结果（人工确认后使用）", icon=":material/draw:"):
            st.write(f"文件：{drawing_info['source_file']}")
            st.write(f"part_id：{drawing_info.get('part_id', response.get('part_id', '未生成'))}")
            st.write(f"状态：{'需要人工确认' if drawing_info['needs_review'] else '初步抽取完成'}")
            st.json(drawing_info["features"])
            for warning in drawing_info["warnings"]:
                st.warning(warning, icon=":material/warning:")

    cad_info = response.get("cad_info")
    if cad_info:
        with st.expander("CAD/3D 模型特征（人工确认后使用）", icon=":material/view_in_ar:"):
            st.write(f"文件：{cad_info['file_name']}")
            st.write(f"part_id：{cad_info['part_id']}")
            st.write(f"解析器：{cad_info['features'].get('parser', '未知')}")
            st.write(f"审核状态：{cad_info['review_status']}")
            st.json(cad_info["features"])
            st.write(f"零件类型候选：{cad_info.get('part_type_candidate') or '未判断'}")
            for warning in cad_info["features"].get("warnings", []):
                st.warning(warning, icon=":material/warning:")

        similar_cad = response.get("cad_similarity_results", [])
        if similar_cad:
            with st.expander("CAD 相似模型 Top-K（几何候选）", icon=":material/search:"):
                st.caption("几何相似度仅用于召回候选，不代表工艺等效；请结合文字、图纸和人工审核。")
                for item in similar_cad:
                    st.write(f"{item['part_id']}｜相似度 {item['score']:.3f}｜{item['file_name']}")
                    st.caption("依据：" + "、".join(item["reasons"]) if item["reasons"] else "依据不足")

    conflicts = response.get("modality_conflicts", [])
    if conflicts:
        with st.container(border=True):
            st.warning("发现跨模态特征冲突，推荐结果必须人工确认。", icon=":material/compare_arrows:")
            for conflict in conflicts:
                st.write(f"• {conflict}")
