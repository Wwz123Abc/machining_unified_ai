"""统一工作台的模型检索结果展示组件。

本层只消费 ``machining_unified.dto`` 中的类型化结果，不再按字符串键取值：
字段改名会在这里直接变成 AttributeError，而不是页面上悄悄少一块内容。
"""

from __future__ import annotations

from typing import Any, Sequence

import streamlit as st

from machining_unified.cad.viewer import render_step_file, render_step_payload
from machining_unified.config.paths import PROJECT_ROOT
from machining_unified.dto import (
    GeometryHit,
    HybridHit,
    VisualHit,
)
from machining_unified.knowledge.engineering import expand_part_relations


# 结果卡片排成两列。
# 预览高度不能一味压小：半宽卡片约 520px 宽，高度取 190 时宽高比达 2.75:1，
# 而机械零件多为方正外形，按高度约束缩放后左右必然大片留白。
# 取 300 让画布接近 1.7:1，模型随之整体变大、空白显著减少。
RESULT_COLUMNS = 2
GRID_PREVIEW_HEIGHT = 300
# 画布在卡片内收窄居中。模型尺寸由高度约束决定，收窄不改变它，
# 只是把原本落在画布内部的左右留白挪到卡片边距，观感更紧凑。
PREVIEW_WIDTH_RATIO = 0.7


def _preview_slot(width_ratio: float = PREVIEW_WIDTH_RATIO):
    """返回卡片内居中收窄的预览容器。

    gap=None 是必要的：默认的列间距会额外吃掉宽度，
    使实际画布比设定比例更窄。
    """

    side = max((1 - width_ratio) / 2, 1e-6)
    return st.columns([side, width_ratio, side], gap=None)[1]


def _grid(items: Sequence[Any], columns: int = RESULT_COLUMNS):
    """按固定列数分行铺开结果，逐个产出（列容器, 条目, 从 1 开始的序号）。

    条目数为奇数时最后一行只填左列，右列留空——不做补位，
    否则会出现一个没有内容却带边框的空卡片。
    """

    for row_start in range(0, len(items), columns):
        row = items[row_start : row_start + columns]
        cells = st.columns(columns, gap="medium")
        for offset, (cell, item) in enumerate(zip(cells, row)):
            yield cell, item, row_start + offset + 1


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


def render_geometry_results(
    items: Sequence[GeometryHit],
    query_mesh: dict[str, Any] | None = None,
) -> None:
    """展示可解释的结构化几何相似结果。

    传入 ``query_mesh`` 时，查询模型作为首个卡片与候选同行、同尺寸展示——
    比对形状时视线不必在整宽预览与半宽候选之间来回换算大小。
    """

    st.markdown("#### 结构化几何相似结果")
    if not items:
        st.warning("CAD 目录中没有可比较的模型。", icon=":material/search_off:")
    # None 占位代表查询模型卡片，其余为候选命中；两者同走一个网格才能保证尺寸一致。
    cards: list[GeometryHit | None] = ([None] if query_mesh is not None else []) + list(items)
    if not cards:
        return
    rank = 0
    for cell, card, _ in _grid(cards):
        with cell, st.container(border=True):
            if card is None:
                st.write("**查询模型** · 本次检索的输入")
                st.caption("下方候选均与它比较；相似度是几何加权分，不是向量相似度。")
                with _preview_slot():
                    render_step_payload(query_mesh, key="query-step-model", height=GRID_PREVIEW_HEIGHT)
                continue
            rank += 1
            st.write(f"**{rank}. {card.part_id}** · 几何相似度 **{card.score:.3f}**")
            st.caption(f"资料组：{card.model_group_id} ｜来源：`{card.source_file or card.file_name}`")
            if card.source_file:
                with _preview_slot():
                    render_step_file(
                        (PROJECT_ROOT / card.source_file).resolve(),
                        key=f"geometry-result-{rank}-{card.part_id}",
                        height=GRID_PREVIEW_HEIGHT,
                    )
            st.write("相似依据：" + ("；".join(card.reasons) or "可比较字段有限"))
            render_graph_relations(card.part_id)


def render_image_results(items: Sequence[VisualHit]) -> None:
    """展示逐模型视觉检索结果。"""

    st.markdown("#### 视觉逐模型比对结果")
    if not items:
        st.warning("视觉检索没有返回模型。", icon=":material/search_off:")
        return
    for cell, hit, index in _grid(items):
        with cell, st.container(border=True):
            st.write(f"**{index}. {hit.part_id}** · 视觉相似度 **{hit.score:.3f}**")
            # 展示的正是参与比对的那张渲染图，而不是另行生成的示意图。
            with _preview_slot():
                st.image(hit.preview, width="stretch")
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
    for cell, hit, index in _grid(items):
        with cell, st.container(border=True):
            st.write(f"**{index}. {hit.part_id} · {hit.family_label}**")
            st.caption(
                f"混合相关度 {hit.score:.3f} ｜向量 {hit.vector_score:.3f} ｜"
                f"BM25 {hit.lexical_score:.3f} ｜图谱 {hit.graph_score:.3f}"
            )
            st.write("证据维度：" + "、".join(hit.evidence))
            st.write("功能候选：" + "、".join(hit.functions))
            st.caption(f"来源：`{hit.source_file}`")
