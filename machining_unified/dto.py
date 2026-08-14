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
- 每种检索分支一个类型：几何、语义、多模态、视觉、混合排序的分数含义互不相同，
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
    """BGE 中文语义召回结果。"""

    part_id: str
    score: float
    source_file: str
    document: Document


@dataclass(frozen=True)
class UnifiedHit:
    """CLIP 统一多模态补充召回结果。分数与 BGE、几何分不可横向比较。"""

    part_id: str
    score: float
    source_file: str
    embedding_method: str


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
class EnterpriseEvidence:
    """企业资料证据单元。``citation`` 是回答中引用的 [S#] 编号。"""

    citation: str
    score: float
    vector_score: float
    lexical_score: float
    identifier_match: bool
    excerpt: str
    excerpt_truncated: bool
    warning: str | None
    document: Document

    @property
    def source_id(self) -> str:
        return str(self.document.metadata["source_id"])

    @property
    def title(self) -> str:
        return str(self.document.metadata["title"])

    @property
    def source_kind(self) -> str:
        return str(self.document.metadata["source_kind"])

    @property
    def source_file(self) -> str:
        return str(self.document.metadata["source_file"])


@dataclass(frozen=True)
class EnterpriseAnswer:
    """企业资料问答结果。``generated`` 区分模型生成与本地证据摘要。"""

    answer: str
    evidence: tuple[EnterpriseEvidence, ...]
    generated: bool
    warning: str | None = None
    assistant_mode: bool = False


@dataclass(frozen=True)
class StepSearchResult:
    """STEP 查询：几何、语义、多模态三路结果互相独立，分数不合并。"""

    query: dict[str, Any]
    geometry: tuple[GeometryHit, ...]
    semantic: tuple[SemanticHit, ...]
    unified: tuple[UnifiedHit, ...]


@dataclass(frozen=True)
class TextSearchResult:
    """文字查询：语义召回与工程混合排序并列展示。"""

    semantic: tuple[SemanticHit, ...]
    hybrid: tuple[HybridHit, ...]
    families: tuple[str, ...]
    unified: tuple[UnifiedHit, ...]


@dataclass(frozen=True)
class ImageSearchResult:
    """图片查询：专用视觉排序，多模态为可选补充。"""

    visual: tuple[VisualHit, ...]
    unified: tuple[UnifiedHit, ...]
