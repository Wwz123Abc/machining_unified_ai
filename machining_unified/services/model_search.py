"""统一模型检索的业务编排；不包含 Streamlit 页面代码。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from machining_unified.cad.extraction import extract_step_features
from machining_unified.cad.retrieval import retrieve_similar_cad
from machining_unified.cad.visual import retrieve_by_image
from machining_unified.knowledge.engineering import FAMILY_LABELS, hierarchical_retrieve
from machining_unified.retrieval.cad_rag import retrieve_cad_rag, retrieve_cad_rag_by_text
from machining_unified.retrieval.multimodal import (
    retrieve_unified_by_image,
    retrieve_unified_by_step,
    retrieve_unified_by_text,
)


def save_step_upload(uploaded_file: Any) -> Path:
    """把上传 STEP 保存到并发安全的临时文件并验证扩展名。"""

    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in {".step", ".stp"}:
        raise ValueError("仅支持 .step 或 .stp 文件")
    source_stem = re.sub(r"[^0-9A-Za-z_-]+", "_", Path(uploaded_file.name).stem)[:48] or "step_query"
    handle, name = tempfile.mkstemp(prefix=f"{source_stem}_", suffix=suffix)
    os.close(handle)
    path = Path(name)
    path.write_bytes(uploaded_file.getvalue())
    return path


def search_by_step(path: Path, top_k: int, use_unified: bool = False) -> dict[str, Any]:
    """并行保留严格几何、BGE 语义和可选 CLIP 结果，不混成伪统一分数。"""

    query = extract_step_features(path, part_id="QUERY", use_filename_hint=False)
    return {
        "query": query,
        "geometry": retrieve_similar_cad(query, top_k=top_k),
        "semantic": retrieve_cad_rag(query, top_k=top_k),
        "unified": retrieve_unified_by_step(query, top_k=top_k) if use_unified else [],
    }


def search_by_text(question: str, top_k: int, use_unified: bool = False) -> dict[str, Any]:
    """执行中文向量召回、BM25/知识路由混排和可选 CLIP 文本召回。"""

    hybrid, family_codes = hierarchical_retrieve(question, top_k=top_k)
    family_labels = [FAMILY_LABELS.get(code, code) for code in family_codes]
    return {
        "semantic": retrieve_cad_rag_by_text(question, top_k=top_k),
        "hybrid": hybrid,
        "families": family_labels,
        "unified": retrieve_unified_by_text(question, top_k=top_k) if use_unified else [],
    }


def search_by_image(image: Any, catalog: list[dict[str, Any]], top_k: int, use_unified: bool = False) -> dict[str, Any]:
    """执行专用视觉排序，并把统一多模态召回作为可选补充证据。"""

    return {
        "visual": retrieve_by_image(image, catalog, top_k=top_k),
        "unified": retrieve_unified_by_image(image, top_k=top_k) if use_unified else [],
    }
