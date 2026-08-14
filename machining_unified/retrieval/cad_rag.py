"""STEP 特征的 LangChain + Chroma RAG 检索与可选回答生成。"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Sequence

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI

from machining_unified.cad.extraction import textify_cad_features
from machining_unified.config.paths import CAD_VECTOR_DIR, PROJECT_ROOT
from machining_unified.dto import SemanticHit
from machining_unified.knowledge.manifests import assembly_manifest_for


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


def semantic_document_text(record: dict[str, Any]) -> str:
    """构造语义索引文本：族级叙述 + 逐模型的区分性几何量。

    查询侧与文档侧必须共用本函数。两边文本构造方式不同会引入系统性不对称：
    公共 token 主导余弦相似度，真正的数值差异反被淹没。

    关于是否保留 ``enriched_text`` 的族级功能/装配/维护叙述，实测结论如下。
    这些叙述在同族内 24/24 完全一致，对"以模型搜模型"确实是噪声——去掉后
    自检索命中率从 37.5% 升到 62.5%。但它同时是"以文字搜模型"唯一能匹配
    功能词（旋转支承、容纳轴承）的信号，去掉后功能性查询的纯语义命中
    从 3/3 掉到 1/3 甚至 0/3。

    真正的解法不在索引文本，而在排序：STEP 分支改为几何重排后，
    名次不再依赖语义分，保留叙述反而让候选集更集中在同族
    （异族混入 10% -> 4%）。因此叙述保留，噪声问题由重排解决。

    **本函数的输出必须是 STEP 文件内容的纯函数。** 凡是只存在于目录记录、
    而无法由现场解析重建的字段（part_id、BOM、回填的设计属性），一律排除。
    满足这一条，"查询文本 == 自身文档文本"就成为结构性不变量，
    自检索候选覆盖率恒为 100%，可以当作管道断裂的探测器使用；
    否则该性质只是"多数记录恰好没有这些字段"的副产品，
    会随资料补全而失效——那正好是最不该失去探测能力的时候。
    """

    features = record.get("features", {})
    semantics = features.get("geometry_semantics") or {}
    surfaces = features.get("surface_types", {})
    assembly = features.get("assembly_structure", {})
    # 一律现算而不复用目录里的 search_text：后者带 part_id 与回填的设计属性，
    # 而上传查询的 part_id 恒为 QUERY、也没有任何设计属性，两侧会因此固定错开。
    lines = [
        textify_cad_features(record, include_identity=False, include_design_metadata=False),
        f"形态：{semantics.get('shape')}；复杂度：{semantics.get('complexity')}；{semantics.get('radius_profile')}。",
        f"长径比 {semantics.get('aspect_ratio')}；扁平比 {semantics.get('thin_ratio')}。",
        f"拓扑：实体 {features.get('solid_count')}，面 {features.get('face_count')}，"
        f"边 {features.get('edge_count')}，顶点 {features.get('vertex_count')}，"
        f"圆柱面 {surfaces.get('cylinder')}，平面 {surfaces.get('plane')}。",
    ]
    if assembly.get("available"):
        lines.append(
            f"STEP 产品树：自由根 {assembly.get('free_shape_count')}，"
            f"装配根 {assembly.get('assembly_root_count')}。"
        )
    # 延迟导入：engineering 在函数体内反向引用 cad_rag，模块级导入会成环。
    from machining_unified.knowledge.engineering import enriched_text

    lines.append(enriched_text(record, include_identity=False))
    return "\n".join(lines)


def build_cad_documents(records: list[dict[str, Any]]) -> list[Document]:
    # 文本内容只保留区分性几何信号；元数据仅保留界面展示可溯源证据所需的最小标识。
    return [
        Document(
            page_content=semantic_document_text(record),
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
    """用 CAD 特征文本做语义召回，并把距离转换为 0~1 相似度。

    查询文本与索引文本共用 :func:`semantic_document_text`，保证两侧同构。
    """
    pairs = get_vector_store().similarity_search_with_score(semantic_document_text(query_record), k=top_k)
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


def generate_rag_explanation(query_record: dict[str, Any], results: Sequence[SemanticHit]) -> str | None:
    """有 DeepSeek Key 时生成说明；没有 Key 时返回 None，不影响检索。"""
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key or not results:
        return None

    # 模型只接收已检索到的文档，建立生成说明的证据边界，
    # 而不是把整个 CAD 目录交给模型自由发挥。
    context = "\n\n".join(
        f"[相似资料 {index}] 相似度={item.score:.3f}\n{item.document.page_content}"
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
