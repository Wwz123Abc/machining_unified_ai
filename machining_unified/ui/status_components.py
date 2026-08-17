"""数据状态面板的 Streamlit 渲染。只消费 data_status 的只读采集结果。"""

from __future__ import annotations

import streamlit as st

from machining_unified.storage.data_status import catalog_rows, collect_data_status

_FAMILY_OPTIONS = ["轴类", "套筒", "板件", "箱体", "复杂", "通用"]


def render_sidebar_status() -> None:
    """侧栏一行：数量 + 健康灯 + 最近构建时间。"""

    status = collect_data_status()
    counts = " / ".join(str(store.count if store.count is not None else "?") for store in status["stores"])
    if status["healthy"]:
        st.badge(f"目录 {status['catalog_count']} · 库 {counts}", icon=":material/check_circle:", color="green")
    else:
        st.badge(f"目录 {status['catalog_count']} · 库 {counts}", icon=":material/warning:", color="orange")
    st.caption(f"最近构建：{status['catalog_mtime']}")


def render_catalog_browser() -> None:
    """主区底部 expander：状态总览 + 可筛选目录表格 + 选中详情。"""

    with st.expander("📊 数据状态与目录浏览", expanded=False):
        status = collect_data_status()
        cols = st.columns(4)
        cols[0].metric("CAD 目录", f"{status['catalog_count']} 条")
        cols[1].metric(status["stores"][0].key, f"{status['stores'][0].count if status['stores'][0].count is not None else '?'} 条")
        cols[2].metric(status["stores"][1].key, f"{status['stores'][1].count if status['stores'][1].count is not None else '?'} 条")
        cols[3].metric("带设计属性", f"{status['attributed_count']} 条")
        for issue in status["issues"]:
            st.warning(issue, icon=":material/warning:")
        st.caption(
            f"目录构建 {status['catalog_mtime']} · 台账 {status['ledger'].get('mtime', '未导入拆解台账')} "
            f"· 多模态清单 {status['multimodal_manifest'].get('mtime', '缺失')} · "
            f"拆解规则版本：{status['ledger'].get('rule_version') or '未记录'} · "
            "完整一致性核对请运行 scripts/check_databases.py"
        )

        rows = catalog_rows()
        left, mid, right = st.columns([1.4, 1.4, 1])
        with left:
            families = st.multiselect("零件族", _FAMILY_OPTIONS, key="panel_family")
        with mid:
            keyword = st.text_input("图号/文件名关键词", key="panel_keyword")
        with right:
            only_design = st.toggle("仅看带设计属性", key="panel_design_only")
        filtered = [
            row
            for row in rows
            if (not families or row["family"] in families)
            and (not keyword or keyword.lower() in f"{row['part_id']} {row['file_name']}".lower())
            and (not only_design or row["has_design"])
        ]
        st.caption(f"显示 {len(filtered)} / {len(rows)} 条")
        event = st.dataframe(
            filtered,
            height=420,
            hide_index=True,
            column_order=["part_id", "file_name", "family", "group", "dims", "faces", "has_design"],
            column_config={"has_design": st.column_config.CheckboxColumn("设计属性")},
            selection_mode="single-row",
            on_select="rerun",
            key="panel_table",
        )
        if event.selection.rows:
            row = filtered[event.selection.rows[0]]
            st.caption(f"来源：`{row['source_file']}` ｜ 被装配共用：{'、'.join(row['shared']) or '仅来源装配'}")
