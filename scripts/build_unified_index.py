"""构建文本、图片和 STEP 多视角共享的统一多模态向量库。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config.paths import CAD_CATALOG_PATH  # noqa: E402
from machining_unified.retrieval.multimodal import (  # noqa: E402
    UNIFIED_VECTOR_DIR,
    build_unified_index,
)


def main() -> None:
    catalog_file = CAD_CATALOG_PATH
    records = json.loads(catalog_file.read_text(encoding="utf-8"))
    manifest = build_unified_index(records)
    print(
        f"统一多模态向量库构建完成：{manifest['searchable_model_count']} 个模型，"
        f"{manifest['embedding_dimension']} 维 -> {UNIFIED_VECTOR_DIR}"
    )


if __name__ == "__main__":
    main()
