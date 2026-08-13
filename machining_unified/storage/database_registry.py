"""四套向量库的名称、位置和职责说明。"""

from dataclasses import dataclass
from pathlib import Path

from machining_unified.config.paths import (
    CAD_VECTOR_DIR,
    ENTERPRISE_VECTOR_DIR,
    MULTIMODAL_VECTOR_DIR,
    PROCESS_VECTOR_DIR,
)


@dataclass(frozen=True)
class VectorStoreSpec:
    key: str
    directory: Path
    collection: str
    purpose: str


VECTOR_STORES = (
    VectorStoreSpec("process", PROCESS_VECTOR_DIR, "machining_cases", "工艺案例与工艺资料"),
    VectorStoreSpec("cad_semantic", CAD_VECTOR_DIR, "cad_models", "CAD 中文工程语义"),
    VectorStoreSpec("enterprise", ENTERPRISE_VECTOR_DIR, "enterprise_knowledge", "STEP、BOM 与工程图证据"),
    VectorStoreSpec("multimodal", MULTIMODAL_VECTOR_DIR, "unified_cad_models", "CLIP 图文与 STEP 多模态"),
)
