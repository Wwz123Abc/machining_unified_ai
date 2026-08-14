"""工程语义、知识图谱与层级混合检索。"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from functools import lru_cache
from typing import Any

from chromadb.errors import ChromaError

from machining_unified.cad.extraction import classify_part_family, geometry_semantics
from machining_unified.cad.retrieval import load_cad_catalog
from machining_unified.config.retrieval_params import get_retrieval_params
from machining_unified.knowledge.manifests import assembly_manifest_for, load_assembly_manifests

logger = logging.getLogger(__name__)


# 每条检索结果保留独立的向量、BM25、Ensemble 与图谱贡献，
# 让工程师能够了解模型为何被选中。
FAMILY_LABELS = {
    "shaft": "轴类零件",
    "sleeve": "套筒/衬套类零件",
    "plate": "板件/法兰类零件",
    "housing": "箱体/支架类零件",
    "complex": "复杂机械零件",
    "general": "通用机械零件",
}
DOMAIN_TERMS = (
    "传递转矩", "旋转支撑", "装配定位", "轴承配合", "联轴器", "导向", "间隙控制",
    "支撑", "衬套", "承载", "安装定位", "孔位磨损", "壁厚", "裂纹", "轴类", "套筒",
    "法兰", "板件", "箱体", "支架", "维护", "装配", "轴承", "薄壁", "圆柱", "孔",
)


def tokenize(text: str) -> list[str]:
    # 中文没有空格分词。领域词表与重叠二元词为 BM25 提供轻量、无额外依赖的分词，
    # 可兼顾工程提问和零件编号。
    """Chinese-aware tokens for BM25 and evidence matching."""
    normalized = text.lower()
    tokens = re.findall(r"[a-z0-9_]+", normalized)
    for term in DOMAIN_TERMS:
        if term in normalized:
            tokens.append(term)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    tokens.extend(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return tokens or ["empty"]


def part_family(record: dict[str, Any]) -> str:
    return record.get("part_family") or classify_part_family(record.get("features", {}), record.get("file_name", ""))


def engineering_profile(record: dict[str, Any]) -> dict[str, Any]:
    family = part_family(record)
    profiles = {
        "shaft": (["传递转矩", "旋转支撑", "装配定位"], ["与轴承配合", "可与键或联轴器连接"], ["检查磨损", "检查同轴度和配合表面"]),
        "sleeve": (["导向", "间隙控制", "支撑或衬套"], ["可与轴或壳体形成配合", "用于定位或隔离"], ["检查内外圆磨损", "检查壁厚和变形"]),
        "plate": (["安装连接", "定位支撑", "载荷传递"], ["可作为法兰或安装基面", "可连接紧固件或壳体"], ["检查平面度", "检查孔位磨损"]),
        "housing": (["承载", "安装定位", "容纳旋转组件"], ["可容纳轴承", "可与轴类部件形成装配关系"], ["检查孔位磨损", "检查安装面和裂纹"]),
        "complex": (["多特征机械连接", "安装定位"], ["需结合 BOM 与装配约束确认关系"], ["检查关键孔位", "检查多特征配合面"]),
        "general": (["待人工标注功能"], ["待人工确认装配关系"], ["待人工建立维护规则"]),
    }
    functions, assembly, maintenance = profiles[family]
    semantic = record.get("features", {}).get("geometry_semantics") or geometry_semantics(record.get("features", {}))
    return {
        "family": family,
        "name": FAMILY_LABELS[family],
        "functions": functions,
        "assembly": assembly,
        "maintenance": maintenance,
        "geometry": semantic,
        "inference_note": "功能与装配关系为几何规则候选；必须由图纸、BOM 与工程师确认。",
    }


def enriched_text(record: dict[str, Any]) -> str:
    # 统一检索文本区分几何事实、推断候选和导入的 BOM 事实，
    # 避免把一种来源误认为另一种来源。
    profile = engineering_profile(record)
    features = record.get("features", {})
    bbox = features.get("bounding_box") or {}
    dimensions = " × ".join(str(bbox.get(key)) for key in ("length_x_mm", "length_y_mm", "length_z_mm") if bbox.get(key) is not None)
    geometry = profile["geometry"]
    machining = "、".join(features.get("machining_feature_candidates", [])) or "未确认"
    assembly_structure = features.get("assembly_structure", {})
    assembly_text = (
        f"STEP 装配结构：自由根 {assembly_structure.get('free_shape_count')}，装配根 {assembly_structure.get('assembly_root_count')}。"
        if assembly_structure.get("available")
        else "STEP 装配结构：未读取到可用产品树。"
    )
    manifest = assembly_manifest_for(str(record.get("part_id", "")))
    bom_text = ""
    if manifest:
        component_preview = "、".join(item["name"] for item in manifest["bom_items"][:8] if item["name"])
        bom_text = f"真实 BOM：装配 {manifest['assembly_id']} 含 {manifest['component_count']} 个物料条目；典型部件：{component_preview}。"
    return "\n".join(
        [
            f"模型 {record.get('part_id')}：{profile['name']}；尺寸 {dimensions or '未提取'} mm。",
            f"形态：{geometry['shape']}；复杂度：{geometry['complexity']}；{geometry['radius_profile']}。",
            f"几何事实：实体 {features.get('solid_count')}，面 {features.get('face_count')}，圆柱面 {features.get('surface_types', {}).get('cylinder')}，平面 {features.get('surface_types', {}).get('plane')}。",
            assembly_text,
            bom_text,
            f"加工候选：{machining}。",
            f"功能候选：{'、'.join(profile['functions'])}。装配候选：{'、'.join(profile['assembly'])}。",
            f"维护关注：{'、'.join(profile['maintenance'])}。{profile['inference_note']}",
        ]
    )


def build_knowledge_graph(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # 导入的 BOM/工程图边是事实；类别/接口边仍明确标为候选，
    # 因为仅凭几何无法证明真实的装配关系。
    records = records or load_cad_catalog()
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    node_ids: set[str] = set()

    def add_node(node_id: str, label: str, kind: str) -> None:
        if node_id not in node_ids:
            nodes.append({"id": node_id, "label": label, "kind": kind})
            node_ids.add(node_id)

    for record in records:
        part_id = str(record["part_id"])
        profile = engineering_profile(record)
        family_id = f"family:{profile['family']}"
        add_node(part_id, part_id, "model")
        add_node(family_id, profile["name"], "family")
        edges.append({"source": part_id, "target": family_id, "label": "属于"})
        for function in profile["functions"]:
            function_id = f"function:{function}"
            add_node(function_id, function, "function")
            edges.append({"source": part_id, "target": function_id, "label": "支持候选"})
    catalog_ids = {str(record["part_id"]) for record in records}
    for manifest in load_assembly_manifests():
        assembly_id = manifest["assembly_id"]
        assembly_node = f"assembly:{assembly_id}"
        add_node(assembly_node, f"{assembly_id} 装配", "assembly")
        for item in manifest["bom_items"]:
            bom_node = f"bom:{assembly_id}:{item['part_id']}"
            label = item["name"] or item["part_id"]
            add_node(bom_node, label, "bom_part")
            quantity = item.get("quantity") or "?"
            edges.append({"source": assembly_node, "target": bom_node, "label": f"BOM ×{quantity}"})
            if item.get("drawing_file"):
                edges.append({"source": bom_node, "target": f"drawing:{item['normalized_part_id']}", "label": "工程图"})
                add_node(f"drawing:{item['normalized_part_id']}", item["drawing_no"] or item["normalized_part_id"], "bom_part")
            if item["normalized_part_id"] in catalog_ids:
                edges.append({"source": bom_node, "target": item["normalized_part_id"], "label": "关联 STEP"})
    families = {part_family(record) for record in records}
    for left, right, relation in (("shaft", "sleeve", "可能配合"), ("shaft", "housing", "可能装配"), ("plate", "housing", "可能安装")):
        if left in families and right in families:
            edges.append({"source": f"family:{left}", "target": f"family:{right}", "label": relation})
    interface_edges: set[tuple[str, str]] = set()
    interface_degree: defaultdict[str, int] = defaultdict(int)
    for index, left in enumerate(records):
        left_interfaces = left.get("features", {}).get("cylindrical_interfaces", [])
        for right in records[index + 1 :]:
            if part_family(left) == part_family(right):
                continue
            right_interfaces = right.get("features", {}).get("cylindrical_interfaces", [])
            matches = [max(float(left_face["radius_mm"]), float(right_face["radius_mm"])) for left_face in left_interfaces for right_face in right_interfaces if {left_face.get("role"), right_face.get("role")} == {"inner", "outer"} and float(left_face["radius_mm"]) >= 5.0 and abs(float(left_face["radius_mm"]) - float(right_face["radius_mm"])) <= 0.03]
            matches.sort(reverse=True)
            if matches:
                key = tuple(sorted((str(left["part_id"]), str(right["part_id"]))))
                if key not in interface_edges and interface_degree[key[0]] < 2 and interface_degree[key[1]] < 2:
                    interface_edges.add(key)
                    interface_degree[key[0]] += 1
                    interface_degree[key[1]] += 1
                    edges.append({"source": key[0], "target": key[1], "label": f"圆柱接口候选 R≈{matches[0]:.2f} mm"})
    return {"nodes": nodes, "edges": edges}


@lru_cache(maxsize=1)
def _cached_knowledge_graph() -> tuple[str, ...]:
    """把图谱序列化后缓存，避免每条检索结果都重新遍历目录构图。"""
    graph = build_knowledge_graph()
    return (json.dumps(graph, ensure_ascii=False),)


def expand_part_relations(part_id: str) -> dict[str, list[dict[str, str]]]:
    """取出某个零件在知识图谱中的直接邻域，对应实现方案 L2 的图谱扩展检索。

    导入的 BOM 与工程图边是事实；类别、功能和圆柱接口边只是几何规则推断出的候选。
    两者分开返回，界面不得把它们混为同一种证据。
    """
    graph = json.loads(_cached_knowledge_graph()[0])
    labels = {node["id"]: node["label"] for node in graph["nodes"]}
    kinds = {node["id"]: node["kind"] for node in graph["nodes"]}
    # BOM 与装配节点来自真实导入资料；类别/功能/接口节点来自几何规则。
    factual_kinds = {"assembly", "bom_part"}
    facts: list[dict[str, str]] = []
    candidates: list[dict[str, str]] = []
    for edge in graph["edges"]:
        if part_id not in (edge["source"], edge["target"]):
            continue
        other = edge["target"] if edge["source"] == part_id else edge["source"]
        item = {
            "relation": edge["label"],
            "node": labels.get(other, other),
            "kind": kinds.get(other, "unknown"),
        }
        (facts if item["kind"] in factual_kinds else candidates).append(item)
    return {"facts": facts, "candidates": candidates}


def _family_score(query: str, profile: dict[str, Any]) -> float:
    query_tokens = set(tokenize(query))
    profile_tokens = set(tokenize(" ".join([profile["name"], *profile["functions"], *profile["assembly"], *profile["maintenance"]])))
    return len(query_tokens & profile_tokens) / max(1, len(query_tokens))


def route_engineering_intent(query: str, records: list[dict[str, Any]] | None = None, top_families: int = 2) -> list[str]:
    # 无关文本不返回类别；层级检索随后搜索全库，避免硬过滤误丢有效证据。
    records = records or load_cad_catalog()
    representative = {part_family(record): engineering_profile(record) for record in records}
    scored = sorted(((family, _family_score(query, profile)) for family, profile in representative.items()), key=lambda item: item[1], reverse=True)
    if not scored or scored[0][1] <= 0:
        return []
    threshold = max(0.08, scored[0][1] * 0.35)
    return [family for family, score in scored if score >= threshold][:top_families]


def _bm25_rank(query: str, records: list[dict[str, Any]]) -> dict[str, float]:
    # 当工程师使用精确术语时，词法 BM25 为语义向量提供补充。
    try:
        from rank_bm25 import BM25Plus

        corpus = [tokenize(enriched_text(record)) for record in records]
        values = [float(value) for value in BM25Plus(corpus).get_scores(tokenize(query))]
        minimum, maximum = min(values, default=0.0), max(values, default=0.0)
        span = maximum - minimum
        return {record["part_id"]: ((value - minimum) / span if span else 0.0) for record, value in zip(records, values)}
    except (ImportError, ValueError, ZeroDivisionError, TypeError):
        # 已枚举：rank_bm25 未安装、语料为空或分词结果非法。
        # 此前是完全静默的降级：BM25 失效后改用类别相似度顶替词法分，
        # 排序质量明显下降却不留任何痕迹。必须记录。
        logger.exception("BM25 词法排序不可用，已降级为类别相似度", extra={"record_count": len(records)})
        return {record["part_id"]: _family_score(query, engineering_profile(record)) for record in records}


def _langchain_ensemble_rank(query: str, records: list[dict[str, Any]]) -> dict[str, float]:
    # EnsembleRetriever 将 BM25 与限定在同一候选集的 Chroma 查询融合，
    # 防止无关文档占用排序位置。
    """Use LangChain's EnsembleRetriever to fuse BM25 and Chroma with RRF."""
    from machining_unified.retrieval.cad_rag import build_cad_documents, get_vector_store
    from langchain_classic.retrievers import EnsembleRetriever
    from langchain_community.retrievers import BM25Retriever

    documents = build_cad_documents(records)
    lexical = BM25Retriever.from_documents(documents, preprocess_func=tokenize)
    semantic = get_vector_store().as_retriever(search_kwargs={"k": len(records), "filter": {"part_id": {"$in": [record["part_id"] for record in records]}}})
    weights = get_retrieval_params().ensemble
    ensemble = EnsembleRetriever(retrievers=[lexical, semantic], weights=[weights.lexical, weights.semantic])
    ordered = ensemble.invoke(query)
    return {document.metadata.get("part_id", ""): 1.0 - index / max(1, len(ordered)) for index, document in enumerate(ordered)}


def hybrid_retrieve(query: str, top_k: int = 5, candidates: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    # 各检索分支独立降级；警告随结果返回，使界面能披露向量或融合分支不可用。
    records = candidates or load_cad_catalog()
    weights = get_retrieval_params().hybrid
    vector_scores: dict[str, float] = {}
    retrieval_warnings: list[str] = []
    try:
        from machining_unified.retrieval.cad_rag import get_vector_store

        for document, distance in get_vector_store().similarity_search_with_score(query, k=max(top_k * 5, len(records))):
            vector_scores[document.metadata.get("part_id", "")] = max(0.0, min(1.0, 1.0 - float(distance)))
    except (ChromaError, OSError, ValueError, RuntimeError) as error:
        # 与企业证据库同一组失败模式：库缺失、文件损坏或被占用、Chroma 内部错误。
        logger.exception("CAD 向量检索不可用，已降级为 BM25 与知识规则", extra={"record_count": len(records)})
        retrieval_warnings.append(f"向量检索不可用，已降级为 BM25 与知识规则：{error}")
    bm25_scores = _bm25_rank(query, records)
    try:
        ensemble_scores = _langchain_ensemble_rank(query, records)
    except (ImportError, ChromaError, OSError, ValueError, RuntimeError, KeyError) as error:
        # EnsembleRetriever 跨 langchain-classic / langchain-community 两个包，
        # 版本漂移时最常见的就是 ImportError 与 KeyError；其余同向量库失败模式。
        logger.exception("EnsembleRetriever 不可用，已使用独立混合排序", extra={"record_count": len(records)})
        ensemble_scores = {}
        retrieval_warnings.append(f"LangChain EnsembleRetriever 不可用，已使用独立混合排序：{error}")
    results = []
    for record in records:
        profile = engineering_profile(record)
        vector = vector_scores.get(record["part_id"], 0.0)
        lexical = bm25_scores.get(record["part_id"], 0.0)
        ensemble = ensemble_scores.get(record["part_id"], 0.0)
        graph_score = _family_score(query, profile)
        score = (
            ensemble * weights.ensemble + lexical * weights.ensemble_lexical + graph_score * weights.ensemble_graph
            if ensemble_scores
            else vector * weights.fallback_vector + lexical * weights.fallback_lexical + graph_score * weights.fallback_graph
        )
        evidence = []
        if vector:
            evidence.append("Chroma 向量")
        if lexical:
            evidence.append("BM25 工程文本")
        if ensemble:
            evidence.append("LangChain EnsembleRetriever")
        if graph_score:
            evidence.append("知识图谱类别/功能")
        results.append({"record": record, "profile": profile, "score": round(score, 4), "vector_score": round(vector, 4), "lexical_score": round(lexical, 4), "ensemble_score": round(ensemble, 4), "graph_score": round(graph_score, 4), "evidence": evidence or ["无有效证据"], "retrieval_warning": "\n".join(retrieval_warnings) or None})
    return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]


def hierarchical_retrieve(query: str, top_k: int = 3) -> tuple[list[dict[str, Any]], list[str]]:
    # 仅在有证据时先路由到可能类别，再排序具体零件；随后加入全库回填，
    # 以降低路由不准确时的召回损失。
    """Two-stage RAG: route engineering intent to families, then retrieve individual parts."""
    records = load_cad_catalog()
    families = route_engineering_intent(query, records)
    scores = [_family_score(query, engineering_profile(record)) for record in records]
    if not scores or max(scores) <= 0:
        return hybrid_retrieve(query, top_k=top_k, candidates=records), []
    scoped = [record for record in records if part_family(record) in families]
    backfill = hybrid_retrieve(query, top_k=max(2, top_k // 2), candidates=records)
    existing = {record["part_id"] for record in scoped}
    for item in backfill:
        if item["record"]["part_id"] not in existing:
            scoped.append(item["record"])
            existing.add(item["record"]["part_id"])
    return hybrid_retrieve(query, top_k=top_k, candidates=scoped), families


