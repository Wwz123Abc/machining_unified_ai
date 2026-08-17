"""跨层传输对象（DTO）。

定位：**只用于服务层与页面层之间的边界**。各检索模块内部继续使用自己的
字典表示，由 ``services/`` 在返回前包装成这里的类型。

为什么需要：此前跨层传的是裸 ``dict``，UI 用 ``item["score"]``、
``item["record"]["part_id"]`` 这类字符串键取值。改字段名不会在任何环节报错，
只会在页面上静默少一块内容或运行时 KeyError，无法在集成前发现。

约定：

- 全部 ``frozen=True``：结果对象进入 ``st.session_state`` 后会跨重跑复用，
  不允许任何一次渲染就地改写它；
- 序列字段用 ``tuple`` 而非 ``list``，与不可变语义保持一致；
- 保留原始 ``Document`` / ``Image`` 对象，证据可追溯到底层原文，不做有损转换；
- 每种检索分支一个类型：几何、语义、视觉、混合排序的分数含义互不相同，
  合并成一个通用类型会重新丢失区分度。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from PIL.Image import Image


@dataclass(frozen=True)
class GeometryHit:
    """可解释几何相似结果。分数是代码计算的加权分，不是向量相似度。"""

    part_id: str
    score: float
    reasons: tuple[str, ...]
    file_name: str
    source_file: str
    model_group_id: str
    model_group_type: str
    component_count: int
    search_text: str


@dataclass(frozen=True)
class SemanticHit:
    """BGE 中文语义召回结果。

    ``rerank_score`` 不为空时，名次由它决定、``score`` 仅表示召回强度。
    两者必须同时展示：实测语义分在本库上几乎无区分度
    （全部候选落在 0.952~0.971，top-3 极差仅 0.005），
    若只显示语义分而按几何分排序，界面就成了用一种证据的数值冒充另一种证据的排序。
    """

    part_id: str
    score: float
    source_file: str
    document: Document
    rerank_score: float | None = None
    rerank_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class VisualHit:
    """逐模型视觉比对结果，附带参与比对的渲染图。"""

    part_id: str
    score: float
    source_file: str
    method: str
    preview: Image


@dataclass(frozen=True)
class HybridHit:
    """工程混合排序结果，保留各路证据的独立分量以便解释排序来由。"""

    part_id: str
    family_label: str
    score: float
    vector_score: float
    lexical_score: float
    ensemble_score: float
    graph_score: float
    evidence: tuple[str, ...]
    functions: tuple[str, ...]
    source_file: str
    retrieval_warning: str | None


@dataclass(frozen=True)
class StepSearchResult:
    """STEP 查询：几何与语义两路结果互相独立，分数不合并。"""

    query: dict[str, Any]
    geometry: tuple[GeometryHit, ...]
    semantic: tuple[SemanticHit, ...]


@dataclass(frozen=True)
class TextSearchResult:
    """文字查询：语义召回与工程混合排序并列展示。"""

    semantic: tuple[SemanticHit, ...]
    hybrid: tuple[HybridHit, ...]
    families: tuple[str, ...]


@dataclass(frozen=True)
class ImageSearchResult:
    """图片查询：逐模型视觉比对排序。

    CLIP 在这条路径上只作为内部粗召回加速手段（见 ``cad/visual.retrieve_by_image``），
    不再作为独立分支对用户展示——它的候选圈定结果不是最终排序依据。
    """

    visual: tuple[VisualHit, ...]
