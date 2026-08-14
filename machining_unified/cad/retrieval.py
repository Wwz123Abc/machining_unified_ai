"""CAD 特征相似检索、解释和跨模态冲突检测。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from machining_unified.config.paths import CAD_CATALOG_PATH
from machining_unified.config.retrieval_params import get_retrieval_params

# 本模块是可解释、以几何为先的“以模型搜模型”分支。
# 它不依赖 Chroma，因此向量索引故障不会影响结构相似检索。


@lru_cache(maxsize=4)
def _load_cad_catalog(path_text: str, modified_ns: int) -> list[dict[str, Any]]:
    # modified_ns 是缓存键的一部分：JSON 目录重建后，运行中的 Streamlit
    # 无需手动重启即可读取新数据。
    path = Path(path_text)
    del modified_ns
    return json.loads(path.read_text(encoding="utf-8"))


def load_cad_catalog() -> list[dict[str, Any]]:
    path = CAD_CATALOG_PATH
    if not path.exists():
        return []
    return _load_cad_catalog(str(path), path.stat().st_mtime_ns)


def _dimension_vector(record: dict[str, Any]) -> list[float]:
    bbox = record.get("features", {}).get("bounding_box") or {}
    return [float(bbox.get(key) or 0) for key in ("length_x_mm", "length_y_mm", "length_z_mm")]


def _dimension_similarity(left: list[float], right: list[float]) -> float:
    # 先排序三个外包尺寸，使模型朝向不影响形状比例；本项刻意忽略绝对尺度。
    if not left or not right or not all(left) or not all(right):
        return 0.0
    left = sorted(left)
    right = sorted(right)
    ratios = [min(a, b) / max(a, b) for a, b in zip(left, right)]
    return sum(ratios) / len(ratios)


def _size_proximity(left: list[float], right: list[float], mode: str) -> float | None:
    """绝对尺寸邻近度，与 ``_dimension_similarity`` 互补。

    前者只看三边排序后的**比例**（刻意忽略尺度），本函数看**实际大小**。
    两个几何相似但一个 20 mm 一个 2000 mm 的零件，在前者眼里完全一样，
    在这里则相差两个数量级——"找同尺寸替换件"要的正是后一种判据。
    """

    if not left or not right or not all(left) or not all(right):
        return None
    if mode == "volume_ratio":
        first = left[0] * left[1] * left[2]
        second = right[0] * right[1] * right[2]
    else:  # max_edge_ratio，配置加载期已校验取值合法
        first, second = max(left), max(right)
    if first <= 0 or second <= 0:
        return None
    return min(first, second) / max(first, second)


def _candidate_similarity(left: list[str], right: list[str]) -> float:
    common = set(left) & set(right)
    return min(1.0, len(common) / max(1, min(len(left), len(right))))


def score_cad_similarity(query: dict[str, Any], candidate: dict[str, Any]) -> tuple[float, list[str]]:
    """返回 0～1 的可解释相似度；缺失属性不猜测，只参与已知字段的动态归一化。"""

    q_features = query.get("features", {})
    c_features = candidate.get("features", {})
    reasons: list[str] = []
    score = 0.0
    weight_total = 0.0

    def add_match(label: str, weight: float, matched: bool, comparable: bool = True) -> None:
        nonlocal score, weight_total
        # 未知字段不计入分母，不视为不匹配；后续导入工程图后可增加信号，
        # 又不会因当前数据缺失而错误降低分数。
        if not comparable:
            return
        weight_total += weight
        if matched:
            score += weight
            reasons.append(label)

    weights = get_retrieval_params().geometry_similarity

    q_type = query.get("part_family") or query.get("features", {}).get("geometry_semantics", {}).get("family")
    c_type = candidate.get("part_family") or candidate.get("features", {}).get("geometry_semantics", {}).get("family")
    add_match("零件类型候选一致", weights.part_family, bool(q_type and c_type and q_type == c_type), bool(q_type and c_type))

    query_dimensions = _dimension_vector(query)
    candidate_dimensions = _dimension_vector(candidate)
    dimensions = _dimension_similarity(query_dimensions, candidate_dimensions)
    weight_total += weights.dimensions
    score += weights.dimensions * dimensions
    if dimensions >= 0.7:
        reasons.append(f"外包络尺寸比例相近（{dimensions:.2f}）")

    # 绝对尺寸是**追加项**，默认关闭。上面的 dimensions 项本就是尺度无关的，
    # 因此关闭时打分与历史完全一致；开启后才引入"多大"这个维度。
    size_proximity = get_retrieval_params().size_proximity
    if size_proximity.enabled:
        proximity = _size_proximity(query_dimensions, candidate_dimensions, size_proximity.mode)
        if proximity is not None:
            weight_total += size_proximity.weight
            score += size_proximity.weight * proximity
            if proximity >= 0.7:
                reasons.append(f"绝对尺寸接近（{proximity:.2f}，{size_proximity.mode}）")

    q_surfaces = q_features.get("surface_types", {})
    c_surfaces = c_features.get("surface_types", {})
    if q_surfaces.get("cylinder", 0) and c_surfaces.get("cylinder", 0):
        add_match("均包含圆柱面", weights.cylinder_present, True)
    if q_surfaces.get("plane", 0) and c_surfaces.get("plane", 0):
        add_match("均包含平面/端面", weights.plane_present, True)

    candidate_similarity = _candidate_similarity(
        q_features.get("machining_feature_candidates", []),
        c_features.get("machining_feature_candidates", []),
    )
    weight_total += weights.machining_candidates
    score += weights.machining_candidates * candidate_similarity
    if candidate_similarity:
        reasons.append("加工特征候选有交集")

    q_design = query.get("design_metadata", {})
    c_design = candidate.get("design_metadata", {})
    # 这些权重仅在企业工程图或 BOM 提供元数据后才会生效；显式保留它们，
    # 可防止系统虚构不存在的设计事实。权重值来自外置配置，字段名与配置项同名。
    comparable_fields = {
        "material": ("材料一致", weights.material),
        "surface_treatment": ("表面处理一致", weights.surface_treatment),
        "roughness_ra": ("粗糙度要求一致", weights.roughness_ra),
        "precision_requirement": ("尺寸公差/精度一致", weights.precision_requirement),
        "heat_treatment": ("热处理要求一致", weights.heat_treatment),
        "hole_types": ("孔类型一致", weights.hole_types),
        "keyways": ("键槽特征一致", weights.keyways),
        "threads": ("螺纹特征一致", weights.threads),
        "chamfers": ("倒角特征一致", weights.chamfers),
        "fillets": ("圆角特征一致", weights.fillets),
        "assembly_relations": ("装配关系一致", weights.assembly_relations),
    }
    for field, (label, weight) in comparable_fields.items():
        left, right = q_design.get(field), c_design.get(field)
        if left in (None, [], "") or right in (None, [], ""):
            continue
        matched = set(left) == set(right) if isinstance(left, list) else left == right
        add_match(label, weight, matched)

    if weight_total == 0:
        return 0.0, ["没有可比较的已确认字段"]
    return round(min(1.0, score / weight_total), 4), reasons


def retrieve_similar_cad(query: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
    """Return independent part-level results; group metadata is display context only."""

    results: list[dict[str, Any]] = []
    # 不按资料组去重：一个文件夹可能包含多个独立零件。
    # 资料组仅用于展示，不是检索结果的身份键。
    for candidate in load_cad_catalog():
        if candidate.get("part_id") == query.get("part_id"):
            continue
        score, reasons = score_cad_similarity(query, candidate)
        group_id = candidate.get("model_group_id", candidate.get("part_id"))
        result = {
                "part_id": candidate.get("part_id"),
                "model_group_id": group_id,
                "model_group_type": candidate.get("model_group_type", "单模型"),
                "component_count": candidate.get("group_component_count", 1),
                "file_name": candidate.get("file_name"),
                "source_file": candidate.get("source_file"),
                "score": score,
                "reasons": reasons,
                "search_text": candidate.get("search_text", ""),
            }
        results.append(result)
    return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]


def detect_modality_conflicts(features: dict[str, Any], cad_record: dict[str, Any] | None) -> list[str]:
    """检测文字特征与 CAD 候选之间的明显冲突并交给人工确认。"""

    if not cad_record:
        return []
    conflicts: list[str] = []
    text_type = features.get("part_type")
    cad_type = cad_record.get("part_type_candidate") or ""
    if text_type and text_type not in cad_type:
        conflicts.append(f"文字识别为{text_type}，CAD 候选为{cad_type}。请确认零件类型。")
    if "薄壁" in features.get("special_features", []) and "薄壁" not in "、".join(
        cad_record.get("features", {}).get("machining_feature_candidates", [])
    ):
        conflicts.append("文字描述包含薄壁，但 CAD 特征未明确识别薄壁；请人工确认壁厚。")
    return conflicts
