"""把 CAD 特征文本写入 Chroma，供 LangChain RAG 召回。"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config.paths import CAD_CATALOG_PATH  # noqa: E402
from machining_unified.retrieval.cad_rag import (  # noqa: E402
    VECTOR_DIR,
    build_cad_documents,
    get_embeddings,
)
from langchain_chroma import Chroma  # noqa: E402


CATALOG = CAD_CATALOG_PATH


# 在同级临时目录中重建索引；只有所有嵌入均写入成功后才替换正式索引，
# 以保护原有可用的检索库。


def main() -> None:
    if not CATALOG.exists():
        raise SystemExit("未找到 CAD 目录，请先运行 build_cad_catalog.py")
    records = json.loads(CATALOG.read_text(encoding="utf-8"))
    temporary_dir = Path(tempfile.mkdtemp(prefix="cad_chroma_", dir=VECTOR_DIR.parent))
    try:
        store = Chroma(
            collection_name="cad_models",
            embedding_function=get_embeddings(),
            persist_directory=str(temporary_dir),
            collection_metadata={"hnsw:space": "cosine"},
        )
        # 文档文本与混合检索共用；ID 使用稳定的零件编号，
        # 因而向量证据始终可以关联回目录记录。
        documents = build_cad_documents(records)
        store.add_documents(documents, ids=[record.get("part_id", str(i)) for i, record in enumerate(records)])
        store._client.close()
        del store
        if VECTOR_DIR.exists():
            try:
                shutil.rmtree(VECTOR_DIR)
            except PermissionError as error:
                # 常见原因是 Navicat、DataGrip 或正在运行的 Streamlit 服务占用了 chroma.sqlite3。
                raise RuntimeError(
                    "无法替换 CAD 向量库：索引文件正被其他程序占用。"
                    "请关闭 SQLite 数据库连接和本项目服务后重试。"
                ) from error
        temporary_dir.replace(VECTOR_DIR)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    print(f"CAD RAG 向量库创建成功：{len(documents)} 条 -> {VECTOR_DIR}")


if __name__ == "__main__":
    main()
