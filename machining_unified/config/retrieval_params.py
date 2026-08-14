"""检索打分权重的外置配置。

这些权重原先散落在四个模块里硬编码，调一次参数就要改源码。现在集中到本模块，
并可由 ``data/config/retrieval_params.json`` 覆盖，工艺/检索工程师无需接触 Python。

分组语义：

- ``enterprise`` / ``ensemble`` / ``hybrid`` / ``unified_embedding``
  是凸组合，各自内部必须和为 1，否则分数尺度会漂移，加载时会拒绝；
- ``geometry_similarity`` **不是**凸组合。它的分母由本次比较中真正可用的字段
  动态累加（缺失字段不计入），因此这些数值表示相对重要性，只做区间校验。

覆盖文件是部分覆盖：只需要写想改的字段，其余沿用默认值。示例——

.. code-block:: json

    {
      "enterprise": {"vector": 0.6, "lexical": 0.4},
      "geometry_similarity": {"material": 0.18}
    }

改动生效范围（重要）：

- ``enterprise`` / ``ensemble`` / ``hybrid`` / ``geometry_similarity`` 是查询期权重，
  改完刷新页面即可生效，不需要重建索引；
- ``unified_embedding`` 参与**写入期**的向量合成，改动后必须重建多模态索引
  （``scripts/build_unified_index.py``），否则库内向量与新权重不一致。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, fields
from functools import lru_cache
from typing import Any

from machining_unified.config.paths import RETRIEVAL_PARAMS_PATH

logger = logging.getLogger(__name__)

# 浮点求和不必精确到二进制相等；1e-6 足以拦截 0.7/0.15/0.1 这类真正写错的组合。
_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class EnterpriseScoreWeights:
    """企业证据库：向量语义与 BM25 词法的融合权重。"""

    vector: float = 0.72
    lexical: float = 0.28


@dataclass(frozen=True)
class EnsembleRetrieverWeights:
    """LangChain EnsembleRetriever 的 RRF 权重。"""

    lexical: float = 0.35
    semantic: float = 0.65


@dataclass(frozen=True)
class HybridScoreWeights:
    """工程混合排序。EnsembleRetriever 可用时走 ensemble_*，降级时走 fallback_*。"""

    ensemble: float = 0.70
    ensemble_lexical: float = 0.15
    ensemble_graph: float = 0.15
    fallback_vector: float = 0.55
    fallback_lexical: float = 0.35
    fallback_graph: float = 0.10


@dataclass(frozen=True)
class UnifiedEmbeddingWeights:
    """CLIP 统一空间中文本与几何多视角的合成权重（写入期）。"""

    text: float = 0.35
    geometry: float = 0.65


@dataclass(frozen=True)
class GeometrySimilarityWeights:
    """可解释几何相似度的各项相对重要性。

    前五项来自 STEP 实测几何；其余来自 BOM 或人工标注的设计属性，
    只有对应字段两侧都有值时才会计入分母。
    """

    part_family: float = 0.15
    dimensions: float = 0.20
    cylinder_present: float = 0.07
    plane_present: float = 0.03
    machining_candidates: float = 0.05
    material: float = 0.10
    surface_treatment: float = 0.05
    roughness_ra: float = 0.05
    precision_requirement: float = 0.08
    heat_treatment: float = 0.05
    hole_types: float = 0.07
    keyways: float = 0.04
    threads: float = 0.04
    chamfers: float = 0.02
    fillets: float = 0.02
    assembly_relations: float = 0.03


SIZE_PROXIMITY_MODES = ("volume_ratio", "max_edge_ratio")


@dataclass(frozen=True)
class SizeProximityWeights:
    """绝对尺寸邻近项。**这不是"尺寸归一化"开关。**

    几何相似度里的 ``dimensions`` 项本来就是尺度无关的——它先把三个外包尺寸
    排序再比比例（见 ``cad/retrieval._dimension_similarity``），绝对尺寸从未
    进入打分。所以这里的语义是**追加**一个绝对尺寸项，用来区分两种不同的检索意图：

    - 关闭（默认）：纯形状相似，"找形状像的零件"，打分与历史完全一致；
    - 开启：形状 + 绝对尺寸，"找能替换的同尺寸零件"。

    ``mode`` 决定绝对尺寸怎么比：``volume_ratio`` 用外包络体积比，对整体大小敏感；
    ``max_edge_ratio`` 只比最长边，对细长件更宽松。
    """

    enabled: bool = False
    weight: float = 0.15
    mode: str = "volume_ratio"


@dataclass(frozen=True)
class RetrievalParams:
    enterprise: EnterpriseScoreWeights = EnterpriseScoreWeights()
    ensemble: EnsembleRetrieverWeights = EnsembleRetrieverWeights()
    hybrid: HybridScoreWeights = HybridScoreWeights()
    unified_embedding: UnifiedEmbeddingWeights = UnifiedEmbeddingWeights()
    geometry_similarity: GeometrySimilarityWeights = GeometrySimilarityWeights()
    size_proximity: SizeProximityWeights = SizeProximityWeights()


_SECTIONS: dict[str, type] = {
    "enterprise": EnterpriseScoreWeights,
    "ensemble": EnsembleRetrieverWeights,
    "hybrid": HybridScoreWeights,
    "unified_embedding": UnifiedEmbeddingWeights,
    "geometry_similarity": GeometrySimilarityWeights,
    "size_proximity": SizeProximityWeights,
}

# 非数值字段的取值白名单。写错模式必须立刻失败，不能退化成"按默认模式算"——
# 那会让调参者以为改生效了。
_ENUM_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("size_proximity", "mode"): SIZE_PROXIMITY_MODES,
}

# 必须和为 1 的凸组合；值是该分组内构成一次打分的字段集合。
_CONVEX_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "enterprise": (("vector", "lexical"),),
    "ensemble": (("lexical", "semantic"),),
    "hybrid": (
        ("ensemble", "ensemble_lexical", "ensemble_graph"),
        ("fallback_vector", "fallback_lexical", "fallback_graph"),
    ),
    "unified_embedding": (("text", "geometry"),),
}


class RetrievalParamsError(ValueError):
    """配置文件结构或取值非法。启动即失败，好过让错误权重悄悄影响排序。"""


def _build_section(name: str, overrides: Any) -> Any:
    section_type = _SECTIONS[name]
    if not isinstance(overrides, dict):
        raise RetrievalParamsError(f"配置节 {name!r} 必须是对象，实际为 {type(overrides).__name__}")
    declared = {item.name: item.type for item in fields(section_type)}
    unknown = set(overrides) - set(declared)
    if unknown:
        # 静默忽略拼错的键会让调参者以为改生效了，必须直接报错。
        raise RetrievalParamsError(f"配置节 {name!r} 存在未知字段：{sorted(unknown)}；可用字段：{sorted(declared)}")
    values: dict[str, Any] = {}
    for key, value in overrides.items():
        # 按字段声明的类型分派。多数字段是 0~1 的权重，但开关是布尔、模式是枚举字符串，
        # 一律当数字校验会让它们无法配置。
        expected = declared[key]
        if expected is bool or expected == "bool":
            if not isinstance(value, bool):
                raise RetrievalParamsError(f"{name}.{key} 必须是 true 或 false，实际为 {value!r}")
            values[key] = value
            continue
        if expected is str or expected == "str":
            allowed = _ENUM_FIELDS.get((name, key))
            if not isinstance(value, str) or (allowed and value not in allowed):
                raise RetrievalParamsError(
                    f"{name}.{key} 必须是 {list(allowed) if allowed else '字符串'} 之一，实际为 {value!r}"
                )
            values[key] = value
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RetrievalParamsError(f"{name}.{key} 必须是数字，实际为 {value!r}")
        if not 0.0 <= float(value) <= 1.0:
            raise RetrievalParamsError(f"{name}.{key} 必须落在 0 到 1 之间，实际为 {value}")
        values[key] = float(value)
    return section_type(**values)


def _validate(params: RetrievalParams) -> None:
    for name, groups in _CONVEX_GROUPS.items():
        section = getattr(params, name)
        for group in groups:
            total = sum(getattr(section, key) for key in group)
            if abs(total - 1.0) > _SUM_TOLERANCE:
                raise RetrievalParamsError(
                    f"配置节 {name!r} 的 {list(group)} 必须和为 1，实际为 {total:.6f}"
                )


def _load(path_text: str, modified_ns: int) -> RetrievalParams:
    del modified_ns  # 仅作为缓存键：改文件后自动失效，无需重启。
    from pathlib import Path

    path = Path(path_text)
    if not path.exists():
        params = RetrievalParams()
        _validate(params)
        return params

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RetrievalParamsError(f"无法读取检索权重配置 {path}：{error}") from error
    if not isinstance(raw, dict):
        raise RetrievalParamsError(f"检索权重配置的顶层必须是对象，实际为 {type(raw).__name__}")
    unknown = set(raw) - set(_SECTIONS)
    if unknown:
        raise RetrievalParamsError(f"存在未知配置节：{sorted(unknown)}；可用配置节：{sorted(_SECTIONS)}")

    params = RetrievalParams(**{name: _build_section(name, raw[name]) for name in raw})
    _validate(params)
    logger.info(
        "已加载外置检索权重配置",
        extra={"config_file": str(path), "overridden_sections": sorted(raw)},
    )
    return params


@lru_cache(maxsize=4)
def _cached_load(path_text: str, modified_ns: int) -> RetrievalParams:
    return _load(path_text, modified_ns)


def get_retrieval_params() -> RetrievalParams:
    """返回当前生效的检索权重；配置文件更新后自动重新加载。"""

    path = RETRIEVAL_PARAMS_PATH
    modified_ns = path.stat().st_mtime_ns if path.exists() else 0
    return _cached_load(str(path), modified_ns)


def describe_effective_params() -> dict[str, Any]:
    """导出当前生效的全部权重，供审计与页面展示。"""

    return asdict(get_retrieval_params())
