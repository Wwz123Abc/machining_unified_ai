"""从真实 STEP、BOM 和工程图 PDF 构建企业知识库向量索引。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from langchain_chroma import Chroma

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.knowledge.enterprise import (  # noqa: E402
    KB_VECTOR_DIR,
    enterprise_documents,
)
from machining_unified.retrieval.cad_rag import get_embeddings  # noqa: E402


def main() -> None:
    documents = enterprise_documents()
    if not documents:
        raise SystemExit("未发现可构建企业知识库的 STEP、BOM 或工程图资料。")
    temporary_dir = Path(tempfile.mkdtemp(prefix="enterprise_kb_", dir=KB_VECTOR_DIR.parent))
    try:
        store = Chroma(
            collection_name="enterprise_knowledge",
            embedding_function=get_embeddings(),
            persist_directory=str(temporary_dir),
            collection_metadata={"hnsw:space": "cosine"},
        )
        store.add_documents(documents, ids=[document.metadata["source_id"] for document in documents])
        store._client.close()
        del store
        if KB_VECTOR_DIR.exists():
            shutil.rmtree(KB_VECTOR_DIR)
        temporary_dir.replace(KB_VECTOR_DIR)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    print(f"企业知识库已构建：{len(documents)} 条资料 -> {KB_VECTOR_DIR}")


if __name__ == "__main__":
    main()
