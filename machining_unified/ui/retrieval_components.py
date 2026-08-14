"""统一工作台的模型检索与企业证据展示组件。

本层只消费 ``machining_unified.dto`` 中的类型化结果，不再按字符串键取值：
字段改名会在这里直接变成 AttributeError，而不是页面上悄悄少一块内容。
"""

from __future__ import annotations

from typing import Any, Sequence

import streamlit as st

from machining_unified.cad.viewer import render_step_file, render_step_model
from machining_unified.config.paths import PROJECT_ROOT
from machining_unified.dto import (
    EnterpriseEvidence,
    GeometryHit,
    HybridHit,
    SemanticHit,
    UnifiedHit,
    VisualHit,
)
from machining_unified.knowledge.engineering import expand_part_relations


def render_graph_relations(part_id: str) -> None:
    """展示该零件在知识图谱中的直接邻域。

    导入的 BOM/工程图关系是事实，类别、功能和圆柱接口是几何规则候选，
    两者必须分开呈现，不能让候选看起来像已确认的装配关系。
    """

    relations = expand_part_relations(str(part_id))
    if not relations["facts"] and not relations["candidates"]:
        return
    with st.expander("知识图谱关联", icon=":material/hub:"):
        if relations["facts"]:
            st.caption("导入资料事实（装配 BOM / 工程图）")
            for item in relations["facts"]:
                st.write(f"• {item['relation']}：{item['node']}")
        if relations["candidates"]:
            st.caption("几何规则候选（需人工确认）")
            for item in relations["candidates"]:
                st.write(f"• {item['relation']}：{item['node']}")


def render_geometry_results(items: Sequence[GeometryHit]) -> None:
    """展示可解释的结构化几何相似结果。"""

    st.markdown("#### 结构化几何相似结果")
    if not items:
        st.warning("CAD 目录中没有可比较的模型。", icon=":material/search_off:")
        return
    for index, hit in enumerate(items, start=1):
        with st.container(border=True):
            st.write(f"**{index}. {hit.part_id}** · 几何相似度 **{hit.score:.3f}**")
            st.caption(f"资料组：{hit.model_group_id} ｜来源：`{hit.source_file or hit.file_name}`")
            st.write("相似依据：" + ("；".join(hit.reasons) or "可比较字段有限"))
            render_graph_relations(hit.part_id)
            if hit.source_file:
                render_step_file(
                    (PROJECT_ROOT / hit.source_file).resolve(),
                    key=f"geometry-result-{index}-{hit.part_id}",
                )


def render_semantic_results(
    items: Sequence[SemanticHit],
    catalog_by_id: dict[str, dict[str, Any]],
    key_prefix: str,
) -> None:
    """展示 BGE 中文语义召回结果及对应 STEP 预览。"""

    st.markdown("#### 中文工程语义结果")
    if not items:
        st.warning("语义向量库没有返回模型。", icon=":material/search_off:")
        return
    for index, hit in enumerate(items, start=1):
        with st.container(border=True):
            st.write(f"**{index}. {hit.part_id}** · 语义相似度 **{hit.score:.3f}**")
            st.caption(f"来源：`{hit.source_file}` ｜方式：BGE 中文语义向量")
            record = catalog_by_id.get(hit.part_id)
            if record:
                render_step_model(record, key=f"{key_prefix}-{index}-{hit.part_id}")


def render_unified_results(
    items: Sequence[UnifiedHit],
    catalog_by_id: dict[str, dict[str, Any]],
    key_prefix: str,
) -> None:
    """展示 CLIP 统一图文/STEP 空间中的补充召回证据。"""

    st.markdown("#### 统一多模态补充结果")
    st.info(
        "该分支比较整体形态与图文语义，不代表尺寸、公差、孔径或工艺等效。",
        icon=":material/info:",
    )
    if not items:
        st.warning("统一多模态向量库没有返回模型。", icon=":material/search_off:")
        return
    for index, hit in enumerate(items, start=1):
        record = catalog_by_id.get(hit.part_id)
        with st.container(border=True):
            st.write(f"**{index}. {hit.part_id}** · 多模态相似度 **{hit.score:.3f}**")
            st.caption(f"来源：`{hit.source_file}` ｜方式：{hit.embedding_method}")
            if record:
                render_step_model(record, key=f"{key_prefix}-{index}-{hit.part_id}")


def render_image_results(items: Sequence[VisualHit]) -> None:
    """展示逐模型视觉检索结果。"""

    st.markdown("#### 视觉逐模型比对结果")
    if not items:
        st.warning("视觉检索没有返回模型。", icon=":material/search_off:")
        return
    for index, hit in enumerate(items, start=1):
        preview, details = st.columns([1, 3], vertical_alignment="center")
        with preview:
            st.image(hit.preview, caption=hit.part_id)
        with details:
            st.write(f"**{index}. {hit.part_id}** · 视觉相似度 **{hit.score:.3f}**")
            st.caption(f"方式：{hit.method} ｜来源：`{hit.source_file}`")


def render_hybrid_results(items: Sequence[HybridHit], families: Sequence[str]) -> None:
    """展示 BGE、BM25 与工程类别知识融合后的文字检索结果。"""

    st.markdown("#### 工程混合排序结果")
    if families:
        st.caption("候选零件族路由：" + "、".join(families))
    if not items:
        st.warning("工程混合检索没有返回模型。", icon=":material/search_off:")
        return
    warning = next((hit.retrieval_warning for hit in items if hit.retrieval_warning), None)
    if warning:
        st.warning(warning, icon=":material/warning:")
    for index, hit in enumerate(items, start=1):
        with st.container(border=True):
            st.write(f"**{index}. {hit.part_id} · {hit.family_label}**")
            st.caption(
                f"混合相关度 {hit.score:.3f} ｜向量 {hit.vector_score:.3f} ｜"
                f"BM25 {hit.lexical_score:.3f} ｜图谱 {hit.graph_score:.3f}"
            )
            st.write("证据维度：" + "、".join(hit.evidence))
            st.write("功能候选：" + "、".join(hit.functions))
            st.caption(f"来源：`{hit.source_file}`")


def render_enterprise_evidence(items: Sequence[EnterpriseEvidence]) -> None:
    """展示企业知识库命中的原始 STEP、BOM 或工程图证据。"""

    warning = next((item.warning for item in items if item.warning), None)
    if warning:
        st.warning(warning, icon=":material/warning:")
    if not items:
        st.caption("本次回答没有可展示的企业资料证据。")
        return
    for item in items:
        with st.container(border=True):
            st.markdown(f"**[{item.citation}] [{item.source_id}] {item.title}**")
            st.caption(
                f"资料类型：{item.source_kind} ｜相关度：{item.score:.3f} ｜"
                f"来源：`{item.source_file}`"
            )
            st.write(item.excerpt)
            if item.excerpt_truncated:
                st.caption("内容较长，此处仅展示前 1200 字；请打开来源文件核对全文。")
