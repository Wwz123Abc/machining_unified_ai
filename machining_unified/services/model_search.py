"""统一模型检索的业务编排；不包含 Streamlit 页面代码。

各检索模块内部返回自己的字典表示，本层负责在跨出服务边界前
包装成 ``machining_unified.dto`` 中的类型化结果，供页面层按属性消费。
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from machining_unified.cad.extraction import extract_step_features
from machining_unified.cad.retrieval import retrieve_similar_cad
from machining_unified.cad.visual import retrieve_by_image
from machining_unified.dto import (
    GeometryHit,
    HybridHit,
    ImageSearchResult,
    SemanticHit,
    StepSearchResult,
    TextSearchResult,
    UnifiedHit,
    VisualHit,
)
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


def _geometry_hits(items: list[dict[str, Any]]) -> tuple[GeometryHit, ...]:
    return tuple(
        GeometryHit(
            part_id=str(item["part_id"]),
            score=float(item["score"]),
            reasons=tuple(item.get("reasons", ())),
            file_name=str(item.get("file_name") or ""),
            source_file=str(item.get("source_file") or ""),
            model_group_id=str(item.get("model_group_id") or item["part_id"]),
            model_group_type=str(item.get("model_group_type") or "单模型"),
            component_count=int(item.get("component_count") or 1),
            search_text=str(item.get("search_text") or ""),
        )
        for item in items
    )


def _semantic_hits(items: list[dict[str, Any]]) -> tuple[SemanticHit, ...]:
    return tuple(
        SemanticHit(
            part_id=str(item["document"].metadata.get("part_id", "")),
            score=float(item["score"]),
            source_file=str(item["document"].metadata.get("source_file", "")),
            document=item["document"],
        )
        for item in items
    )


def _unified_hits(items: list[dict[str, Any]]) -> tuple[UnifiedHit, ...]:
    return tuple(
        UnifiedHit(
            part_id=str(item["part_id"]),
            score=float(item["score"]),
            source_file=str(item.get("source_file") or ""),
            embedding_method=str(item.get("embedding_method") or ""),
        )
        for item in items
    )


def _visual_hits(items: list[dict[str, Any]]) -> tuple[VisualHit, ...]:
    return tuple(
        VisualHit(
            part_id=str(item["record"]["part_id"]),
            score=float(item["score"]),
            source_file=str(item["record"].get("source_file") or ""),
            method=str(item["method"]),
            preview=item["preview"],
        )
        for item in items
    )


def _hybrid_hits(items: list[dict[str, Any]]) -> tuple[HybridHit, ...]:
    return tuple(
        HybridHit(
            part_id=str(item["record"]["part_id"]),
            family_label=str(item["profile"]["name"]),
            score=float(item["score"]),
            vector_score=float(item["vector_score"]),
            lexical_score=float(item["lexical_score"]),
            ensemble_score=float(item["ensemble_score"]),
            graph_score=float(item["graph_score"]),
            evidence=tuple(item.get("evidence", ())),
            functions=tuple(item["profile"].get("functions", ())),
            source_file=str(item["record"].get("source_file") or ""),
            retrieval_warning=item.get("retrieval_warning"),
        )
        for item in items
    )


def search_by_step(path: Path, top_k: int, use_unified: bool = False) -> StepSearchResult:
    """并行保留严格几何、BGE 语义和可选 CLIP 结果，不混成伪统一分数。"""

    query = extract_step_features(path, part_id="QUERY", use_filename_hint=False)
    return StepSearchResult(
        query=query,
        geometry=_geometry_hits(retrieve_similar_cad(query, top_k=top_k)),
        semantic=_semantic_hits(retrieve_cad_rag(query, top_k=top_k)),
        unified=_unified_hits(retrieve_unified_by_step(query, top_k=top_k)) if use_unified else (),
    )


def search_by_text(question: str, top_k: int, use_unified: bool = False) -> TextSearchResult:
    """执行中文向量召回、BM25/知识路由混排和可选 CLIP 文本召回。"""

    hybrid, family_codes = hierarchical_retrieve(question, top_k=top_k)
    return TextSearchResult(
        semantic=_semantic_hits(retrieve_cad_rag_by_text(question, top_k=top_k)),
        hybrid=_hybrid_hits(hybrid),
        families=tuple(FAMILY_LABELS.get(code, code) for code in family_codes),
        unified=_unified_hits(retrieve_unified_by_text(question, top_k=top_k)) if use_unified else (),
    )


def search_by_image(
    image: Any, catalog: list[dict[str, Any]], top_k: int, use_unified: bool = False
) -> ImageSearchResult:
    """执行专用视觉排序，并把统一多模态召回作为可选补充证据。"""

    return ImageSearchResult(
        visual=_visual_hits(retrieve_by_image(image, catalog, top_k=top_k)),
        unified=_unified_hits(retrieve_unified_by_image(image, top_k=top_k)) if use_unified else (),
    )
