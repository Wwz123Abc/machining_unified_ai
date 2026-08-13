"""STEP 特征的 LangChain + Chroma RAG 检索与可选回答生成。"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

from machining_unified.cad.extraction import textify_cad_features
from machining_unified.config.paths import CAD_VECTOR_DIR, PROJECT_ROOT
from machining_unified.knowledge.engineering import enriched_text


VECTOR_DIR = CAD_VECTOR_DIR
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"


# 向量索引由 scripts/build_vector_index.py 离线构建；查询时仅连接它，
# 防止普通检索请求意外改写当前持久化 Chroma 数据库。


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    # 归一化保证不同用户查询之间的余弦距离换算一致。
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache(maxsize=1)
def get_vector_store() -> Chroma:
    # 模型与向量库均按进程缓存一次：每次 Streamlit 重跑都加载会很慢，
    # 且不会让持久化索引更新得更及时。
    if not VECTOR_DIR.exists():
        raise FileNotFoundError("尚未建立 CAD RAG 向量库，请先运行 scripts/build_vector_index.py")
    return Chroma(
        collection_name="cad_models",
        embedding_function=get_embeddings(),
        persist_directory=str(VECTOR_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )


def build_cad_documents(records: list[dict[str, Any]]) -> list[Document]:
    # 文本内容提供语义和几何语言；元数据仅保留界面展示可溯源证据所需的最小标识。
    return [
        Document(
            page_content=enriched_text(record) + "\n" + (record.get("search_text") or textify_cad_features(record)),
            metadata={
                "part_id": record.get("part_id", ""),
                "model_group_id": record.get("model_group_id", record.get("part_id", "")),
                "source_file": record.get("source_file", ""),
                "source_type": record.get("source_type", ""),
            },
        )
        for record in records
    ]


def retrieve_cad_rag(query_record: dict[str, Any], top_k: int = 5) -> list[dict[str, Any]]:
    """用 CAD 特征文本做语义召回，并把距离转换为 0~1 相似度。"""
    query_text = query_record.get("search_text") or textify_cad_features(query_record)
    pairs = get_vector_store().similarity_search_with_score(query_text, k=top_k)
    results: list[dict[str, Any]] = []
    for document, distance in pairs:
        # Chroma 返回余弦距离；进行防御性截断，使展示分数始终位于 0 到 1，
        # 即使底层实现的数值边界发生变化也不会影响界面。
        results.append(
            {
                "document": document,
                "score": round(max(0.0, min(1.0, 1.0 - float(distance))), 4),
            }
        )
    return results


def retrieve_cad_rag_by_text(question: str, top_k: int = 5) -> list[dict[str, Any]]:
    """以工程文字直接查询 CAD 语义库，保留命中的 STEP 来源和相似度。"""
    pairs = get_vector_store().similarity_search_with_score(question, k=top_k)
    return [
        {
            "document": document,
            "score": round(max(0.0, min(1.0, 1.0 - float(distance))), 4),
        }
        for document, distance in pairs
    ]


def generate_rag_explanation(query_record: dict[str, Any], results: list[dict[str, Any]]) -> str | None:
    """有 DeepSeek Key 时生成说明；没有 Key 时返回 None，不影响检索。"""
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key or not results:
        return None

    # 模型只接收已检索到的文档，建立生成说明的证据边界，
    # 而不是把整个 CAD 目录交给模型自由发挥。
    context = "\n\n".join(
        f"[相似资料 {index}] 相似度={item['score']:.3f}\n{item['document'].page_content}"
        for index, item in enumerate(results, start=1)
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是 CAD 资料检索助手。只根据给定的模型特征和检索资料进行简短说明，"
                "不要编造未提供的尺寸、材料或工艺。",
            ),
            (
                "human",
                "查询模型：\n{query}\n\n相似资料：\n{context}\n\n"
                "请说明最相似资料及相似原因，并指出需要人工确认的差异。",
            ),
        ]
    )
    model = ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0,
    )
    # LCEL 串联提示词与模型；提示词明确禁止无证据的几何、材料、尺寸与工艺断言。
    response = (prompt | model).invoke(
        {"query": textify_cad_features(query_record), "context": context}
    )
    return response.content if isinstance(response.content, str) else str(response.content)
