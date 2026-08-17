"""两套向量库的名称、位置和职责说明。"""

from dataclasses import dataclass
from pathlib import Path

from machining_unified.config.paths import (
    CAD_VECTOR_DIR,
    MULTIMODAL_VECTOR_DIR,
)


@dataclass(frozen=True)
class VectorStoreSpec:
    key: str
    directory: Path
    collection: str
    purpose: str


VECTOR_STORES = (
    VectorStoreSpec("cad_semantic", CAD_VECTOR_DIR, "cad_models", "CAD 中文工程语义"),
    VectorStoreSpec("multimodal", MULTIMODAL_VECTOR_DIR, "unified_cad_models", "CLIP 图片检索粗召回加速（纯几何多视角）"),
)
