"""图片检索的内部加速层：STEP 多视角投影落在 CLIP 图像空间的预计算索引。

本模块不面向 UI。它只服务 ``cad/visual.retrieve_by_image`` 的粗召回步骤：
真实精排（渲染候选图 + 与查询图片重新比对）仍在 ``cad/visual.py`` 完成，
这里只负责用一次 ANN 查询把 508 个候选缩小到几十个，省掉对全库渲染+编码。

**索引内容是纯几何向量，不再混合任何文本嵌入。** 早期版本按
``text_weight * 文本向量 + geometry_weight * 几何向量`` 合成索引向量，
目的是让文字查询也能直接命中这个空间；但 CLIP（clip-ViT-B-32）是英文
图文模型，中文文本经它编码后基本是噪声（实测三条中文描述族级命中 0/9），
混入图片检索的粗召回反而是污染信号，而不是补充信号。现在索引只由
STEP 的真实多视角渲染图编码而成，查询侧也只用图片，两者同构。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
import numpy as np

from machining_unified.cad.extraction import textify_cad_features
from machining_unified.cad.visual import get_clip_model, model_previews
from machining_unified.config.paths import MULTIMODAL_MANIFEST_PATH, MULTIMODAL_VECTOR_DIR


UNIFIED_VECTOR_DIR = MULTIMODAL_VECTOR_DIR
UNIFIED_MANIFEST = MULTIMODAL_MANIFEST_PATH
COLLECTION_NAME = "unified_cad_models"
# 粗召回候选数。远大于任何 UI 上会展示的 top_k，
# 因为这一步只负责把渲染+精排的候选集从全库（508+）缩小到可承受的规模，
# 不是最终排序——真正的名次由 cad/visual.retrieve_by_image 的精排决定。
COARSE_RECALL_LIMIT = 50


def normalize(vector: np.ndarray) -> np.ndarray:
    """执行 L2 归一化，使内积可作为余弦相似度。"""
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


def factual_cad_text(record: dict[str, Any]) -> str:
    """只使用 STEP 提取的几何事实，供 Chroma 文档字段存人可读的追溯文本。

    这段文本不参与嵌入计算（索引向量是纯几何渲染），只是让人在检索到某条
    记录时能看懂它是什么，不必反查 CAD 目录。
    """
    return textify_cad_features(record)


def build_unified_embedding(record: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """把 STEP 的多个真实网格视角投影到 CLIP 图像空间并取均值，作为该零件的索引向量。

    只用几何渲染、不掺文本：查询侧（`coarse_visual_candidates`）同样只编码图片，
    两侧同构才能让粗召回的相似度有意义。
    """
    views = model_previews(record)
    view_vectors = np.asarray(
        get_clip_model().encode(views, normalize_embeddings=True, batch_size=16), dtype=np.float32
    )
    vector = normalize(view_vectors.mean(axis=0))
    audit = {
        "geometry_embedding_dim": int(vector.size),
        "render_view_count": len(views),
        "method": "CLIP image space, eight real STEP mesh views (geometry-only)",
    }
    return vector, audit


def build_unified_index(records: list[dict[str, Any]]) -> dict[str, Any]:
    """原子构建统一向量库；失败时不覆盖已有可用索引。"""
    temporary_dir = Path(tempfile.mkdtemp(prefix="unified_chroma_", dir=UNIFIED_VECTOR_DIR.parent))
    try:
        client = chromadb.PersistentClient(path=str(temporary_dir))
        collection = client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        embeddings: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []
        audit_records: list[dict[str, Any]] = []
        for record in records:
            vector, audit = build_unified_embedding(record)
            part_id = str(record["part_id"])
            embeddings.append(vector.tolist())
            documents.append(factual_cad_text(record))
            metadatas.append(
                {
                    "part_id": part_id,
                    "source_file": str(record.get("source_file", "")),
                    "embedding_method": audit["method"],
                }
            )
            audit_records.append({"part_id": part_id, "source_file": record.get("source_file", ""), **audit})
        collection.add(ids=[str(record["part_id"]) for record in records], embeddings=embeddings, documents=documents, metadatas=metadatas)
        # Chroma 在 Windows 上会保持 SQLite/HNSW 文件句柄；必须在目录替换前显式停止客户端。
        client._system.stop()
        del collection
        client = None
        if UNIFIED_VECTOR_DIR.exists():
            try:
                shutil.rmtree(UNIFIED_VECTOR_DIR)
            except PermissionError as error:
                raise RuntimeError("无法替换统一向量库：请关闭占用该 SQLite 文件的数据库工具后重试。") from error
        temporary_dir.replace(UNIFIED_VECTOR_DIR)
    except Exception:  # noqa: BLE001 - 清理后原样重抛，不吞任何异常
        # 无论何种失败都必须删掉半成品临时目录，否则会残留在 vector_stores 旁边；
        # 随后 raise 保持原始异常与堆栈不变，调用方看到的仍是真实错误。
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    manifest = {
        "status": "internal-accelerator",
        "collection": COLLECTION_NAME,
        "model": "sentence-transformers/clip-ViT-B-32",
        "searchable_model_count": len(records),
        "embedding_dimension": len(embeddings[0]) if embeddings else 0,
        "purpose": "cad/visual.retrieve_by_image 的粗召回加速层，不面向 UI 展示。",
        "limitation": "STEP uses real multi-view mesh renders. Native 3D encoder domain fine-tuning requires more enterprise triplets.",
        "records": audit_records,
    }
    UNIFIED_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


@lru_cache(maxsize=1)
def get_unified_collection():
    """打开已构建的统一向量库，供图片检索的粗召回步骤使用。"""
    if not UNIFIED_VECTOR_DIR.exists():
        raise FileNotFoundError("尚未建立统一多模态向量库，请运行 scripts/build_unified_index.py")
    client = chromadb.PersistentClient(path=str(UNIFIED_VECTOR_DIR))
    return client, client.get_collection(COLLECTION_NAME)


def _query_vector(vector: np.ndarray, top_k: int) -> list[dict[str, Any]]:
    """在统一空间中查询，并返回与原始 STEP 关联的可审计结果。"""
    client, collection = get_unified_collection()
    result = collection.query(query_embeddings=[vector.tolist()], n_results=top_k, include=["documents", "metadatas", "distances"])
    return [
        {
            "part_id": result["ids"][0][index],
            "score": round(max(0.0, min(1.0, 1.0 - float(result["distances"][0][index]))), 4),
            "source_file": result["metadatas"][0][index]["source_file"],
            "embedding_method": result["metadatas"][0][index]["embedding_method"],
        }
        for index in range(len(result["ids"][0]))
    ]


def coarse_visual_candidates(image: Any, limit: int = COARSE_RECALL_LIMIT) -> list[dict[str, Any]]:
    """CLIP 粗召回：用查询图片直接匹配统一库里的纯几何多视角向量。

    仅供 ``cad/visual.retrieve_by_image`` 内部调用，不是独立的检索分支：
    这里的分数只用于圈定候选范围，不作为最终排序或展示给用户的证据——
    真正的名次由精排阶段对候选重新渲染、重新比对后给出。
    """
    vector = np.asarray(get_clip_model().encode([image.convert("RGB")], normalize_embeddings=True)[0], dtype=np.float32)
    return _query_vector(vector, limit)
