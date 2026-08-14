"""第一期统一多模态嵌入：文本、图片与 STEP 多视角投影共用 CLIP 空间。"""

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
from machining_unified.config.retrieval_params import get_retrieval_params


UNIFIED_VECTOR_DIR = MULTIMODAL_VECTOR_DIR
UNIFIED_MANIFEST = MULTIMODAL_MANIFEST_PATH
COLLECTION_NAME = "unified_cad_models"


def normalize(vector: np.ndarray) -> np.ndarray:
    """执行 L2 归一化，使内积可作为余弦相似度。"""
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


def factual_cad_text(record: dict[str, Any]) -> str:
    """只使用 STEP 提取的几何事实，不将模板化功能推断写入统一空间。"""
    return textify_cad_features(record)


def _geometry_vector(record: dict[str, Any]) -> tuple[np.ndarray, int]:
    """把 STEP 的多个真实网格视角投影到 CLIP 图像空间并取均值。"""
    views = model_previews(record)
    view_vectors = np.asarray(
        get_clip_model().encode(views, normalize_embeddings=True, batch_size=16), dtype=np.float32
    )
    return normalize(view_vectors.mean(axis=0)), len(views)


def _record_modal_vectors(record: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """分别生成文本向量和 STEP 几何多视角向量，二者均位于 CLIP 共享空间。"""
    model = get_clip_model()
    text = factual_cad_text(record)
    text_vector = np.asarray(model.encode([text], normalize_embeddings=True)[0], dtype=np.float32)
    geometry_vector, view_count = _geometry_vector(record)
    weights = get_retrieval_params().unified_embedding
    # 权重写进审计信息：索引是用哪一组权重合成的必须可回溯，
    # 否则事后调过配置就无法判断库内向量与当前配置是否一致。
    audit = {
        "text_embedding_dim": int(text_vector.size),
        "geometry_embedding_dim": int(geometry_vector.size),
        "render_view_count": view_count,
        "text_weight": weights.text,
        "geometry_weight": weights.geometry,
        "method": "CLIP shared image-text space + eight real STEP mesh views",
    }
    return text_vector, geometry_vector, audit


def build_unified_embedding(record: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    """为单个 STEP 生成统一向量及可审计的模态构成信息。

    当前阶段将 STEP 通过八个真实网格渲染视角送入 CLIP 图像编码器；
    它是共享空间原型，不是已经用企业数据微调过的原生点云编码器。
    """
    text_vector, geometry_vector, audit = _record_modal_vectors(record)
    unified_vector = normalize(audit["text_weight"] * text_vector + audit["geometry_weight"] * geometry_vector)
    return unified_vector, audit


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
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    manifest = {
        "status": "prototype",
        "collection": COLLECTION_NAME,
        "model": "sentence-transformers/clip-ViT-B-32",
        "searchable_model_count": len(records),
        "embedding_dimension": len(embeddings[0]) if embeddings else 0,
        "limitation": "STEP uses real multi-view mesh renders. Native 3D encoder domain fine-tuning requires more enterprise triplets.",
        "records": audit_records,
    }
    UNIFIED_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


@lru_cache(maxsize=1)
def get_unified_collection():
    """打开已构建的统一向量库，供后续文字、图片或 STEP 查询共同使用。"""
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


def retrieve_unified_by_text(question: str, top_k: int = 5) -> list[dict[str, Any]]:
    """以文字直接检索 STEP 模型，无需先经过 BGE 或关键词分支。"""
    vector = np.asarray(get_clip_model().encode([question], normalize_embeddings=True)[0], dtype=np.float32)
    return _query_vector(vector, top_k)


def retrieve_unified_by_image(image, top_k: int = 5) -> list[dict[str, Any]]:
    """以图片直接检索 STEP 模型；图片与 STEP 多视角处于同一 CLIP 空间。"""
    vector = np.asarray(get_clip_model().encode([image.convert("RGB")], normalize_embeddings=True)[0], dtype=np.float32)
    return _query_vector(vector, top_k)


def retrieve_unified_by_step(record: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
    """以 STEP 几何多视角直接检索，不使用上传文件名或文本描述。

    索引里存的是文本与几何按权重混合后的向量，这里刻意只用纯几何向量查询：
    上传模型没有可信的企业文本描述，掺入自动生成的文本会引入伪证据。
    代价是查询向量与索引向量不同构，因此该分支的分数只能用于排序，
    不能与 BGE 语义分或几何加权分横向比较。
    """
    geometry_vector, _ = _geometry_vector(record)
    return _query_vector(geometry_vector, top_k)
