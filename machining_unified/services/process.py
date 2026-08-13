# os 用于从系统环境和 .env 文件中读取 DeepSeek 密钥。
import os
import hashlib

# Streamlit 负责网页界面；其余库负责 RAG 的各个环节。
import streamlit as st
from dotenv import load_dotenv
from machining_unified.cad.extraction import build_cad_context, extract_step_features
from machining_unified.cad.retrieval import detect_modality_conflicts, retrieve_similar_cad
from machining_unified.config.paths import PROCESS_VECTOR_DIR, PROJECT_ROOT
from machining_unified.knowledge.drawing import build_drawing_context, extract_uploaded_drawing
from machining_unified.knowledge.manifests import build_part_manifest_context
from machining_unified.knowledge.normalization import normalize_extracted_features
from machining_unified.knowledge.safety import (
    build_incomplete_requirement_answer,
    build_uncovered_requirement_answer,
    get_missing_required_features,
    get_uncovered_features,
)
from machining_unified.knowledge.support import format_support_context, select_support_data
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI
from streamlit.errors import StreamlitSecretNotFoundError


# 只有这些已在教学知识库中覆盖的材料/零件类型才用于元数据精确筛选。
supported_materials = {
    "45钢",
    "304不锈钢",
    "HT250",
    "6061铝合金",
}

supported_part_types = {
    "轴类",
    "套筒类",
    "箱体类",
    "盘类",
}

# 检索得分过低时，不让模型在缺少依据的情况下编造工艺。
# 低于该阈值时宁可提示资料不足，也不让模型脱离资料猜测工艺。
MIN_RELEVANCE_SCORE = 0.35
# BGE 对“短问题检索长文档”建议添加的查询提示；文档本身不加此前缀。
BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def build_metadata_filter(features: dict) -> dict | None:
    """依据模型提取的特征，构建 Chroma 筛选条件。"""

    # Chroma 的多个条件需要放进 $and，先逐个收集有效条件。
    filters = []

    material = features.get("material")
    if material in supported_materials:
        filters.append({"material": material})

    part_type = features.get("part_type")
    if part_type in supported_part_types:
        filters.append({"part_type": part_type})

    if not filters:
        return None

    if len(filters) == 1:
        return filters[0]

    # 同时识别出材料和零件类型时，要求两个条件都满足。
    return {"$and": filters}


def get_source_label(document: Document) -> str:
    """为结构化案例或文档片段生成统一的来源标签。"""

    # JSON 案例使用 case_id；手册切片使用 chunk_id；最后才退回源文件名。
    metadata = document.metadata
    return metadata.get(
        "case_id",
        metadata.get("chunk_id", metadata.get("source_file", "资料片段")),
    )


def get_deepseek_api_key() -> str | None:
    """优先读取本地 .env，其次读取 Streamlit Community Cloud Secrets。"""

    # 本地开发环境继续使用 .env；该文件必须保持在 .gitignore 中。
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if api_key:
        return api_key

    # 云端不上传 .env，而是在应用后台以 Secrets 的方式注入密钥。
    try:
        return st.secrets.get("DEEPSEEK_API_KEY")
    except (FileNotFoundError, KeyError, StreamlitSecretNotFoundError):
        return None


@st.cache_resource
def load_rag_resources():
    """加载模型、向量库和 LangChain 链；网页运行期间只加载一次。"""

    # 本地读取 .env，云端读取 Streamlit Secrets，均不把密钥写入源码。
    api_key = get_deepseek_api_key()

    if not api_key:
        raise ValueError("未找到 DEEPSEEK_API_KEY，请检查 .env 或 Streamlit Secrets。")

    # 同一模型既负责写入时的文档向量，也负责查询时的问题向量。
    embeddings = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
        query_encode_kwargs={
            "normalize_embeddings": True,
            "prompt": BGE_QUERY_INSTRUCTION,
        },
    )

    # 只打开已重建好的本地 Chroma 库，不在网页请求中重复写库。
    vector_store = Chroma(
        collection_name="machining_cases",
        persist_directory=str(PROCESS_VECTOR_DIR),
        embedding_function=embeddings,
    )

    # 回答模型温度较低，减少无依据的随机发挥。
    answer_llm = ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0.2,
    )

    # 特征模型强制 JSON 输出，方便后续组成元数据过滤条件。
    feature_llm = ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0,
        model_kwargs={
            "response_format": {"type": "json_object"},
        },
    )

    feature_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """你是机械零件特征提取助手。

请从用户描述中提取加工相关信息，并且只输出一个 JSON 对象。
无法明确判断的字段必须填 null，绝不能猜测。
安装板、法兰盘和盖板归为“盘类”。

JSON 格式必须如下：
{{
  "material": "材料名称或null",
  "part_type": "轴类/套筒类/箱体类/盘类/其他/null",
  "outer_diameter_mm": 数字或null,
  "inner_diameter_mm": 数字或null,
  "roughness_ra": "例如Ra1.6或null",
  "precision_requirement": "例如IT7或null",
  "heat_treatment_requirement": "例如调质或null",
  "special_features": ["键槽", "薄壁"] 或 []
}}
""",
            ),
            ("human", "用户描述：{question}"),
        ]
    )

    answer_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """你是一名机械加工工艺推荐助手。

请严格依据“参考工艺案例”回答用户问题：
1. 先给出推荐工艺路线；
2. 说明推荐依据及需要确认的关键条件；
3. 不能把案例中更高精度的要求，直接当作用户的必需要求；
4. 若参考案例不足以判断，明确说“知识库资料不足”，不要编造具体工艺参数；
5. 在回答末尾列出使用的案例编号。

参考工艺案例：
{context}
""",
            ),
            ("human", "用户问题：{question}"),
        ]
    )

    # LCEL 管道：提示词 → 大模型 → 解析器。
    feature_chain = feature_prompt | feature_llm | JsonOutputParser()
    answer_chain = answer_prompt | answer_llm | StrOutputParser()

    return vector_store, feature_chain, answer_chain


def resolve_part_id(question: str, drawing_file=None, cad_file=None, requested_part_id: str = "") -> str:
    """优先使用用户编号，否则依据当前输入生成可复现的教学编号。"""

    if requested_part_id.strip():
        return requested_part_id.strip()
    names = [getattr(item, "name", "") for item in (drawing_file, cad_file) if item]
    seed = "|".join([question.strip(), *names])
    return f"AUTO-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:10].upper()}"


def save_uploaded_cad_temp(cad_file):
    """将 Streamlit 上传对象安全写入已关闭的 Windows 临时文件。"""

    import tempfile
    from pathlib import Path

    suffix = Path(getattr(cad_file, "name", "model.step")).suffix.lower()
    if suffix not in {".step", ".stp"}:
        suffix = ".step"
    file_descriptor, temp_path = tempfile.mkstemp(suffix=suffix)
    os.close(file_descriptor)
    Path(temp_path).write_bytes(cad_file.getvalue())
    return Path(temp_path)


def attach_cad_design_metadata(cad_info: dict) -> dict:
    """加载企业维护的材料/公差/孔槽等属性，不存在时保持空值。"""

    import json
    from pathlib import Path

    metadata_path = PROJECT_ROOT / "data" / "catalogs" / "cad_metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        cad_info["design_metadata"].update(metadata.get(cad_info["part_id"], {}))
    return cad_info


def recommend_process(
    question: str,
    drawing_file=None,
    cad_file=None,
    requested_part_id: str = "",
    on_progress=None,
) -> dict:
    """特征提取 → 筛选检索 → DeepSeek 生成工艺建议。"""

    part_id = resolve_part_id(question, drawing_file, cad_file, requested_part_id)
    drawing_info = extract_uploaded_drawing(drawing_file) if drawing_file else None
    if drawing_info:
        drawing_info["part_id"] = part_id
    drawing_context = build_drawing_context(drawing_info) if drawing_info else ""
    cad_info = None
    cad_similarity_results = []
    modality_conflicts = []
    cad_context = ""
    if cad_file:
        temp_path = save_uploaded_cad_temp(cad_file)
        try:
            cad_info = attach_cad_design_metadata(extract_step_features(temp_path, part_id))
        finally:
            temp_path.unlink(missing_ok=True)
        cad_context = build_cad_context(cad_info)
        cad_similarity_results = retrieve_similar_cad(cad_info)
    manifest_context = build_part_manifest_context(part_id)
    enriched_question = f"{question}{drawing_context}{cad_context}{manifest_context}"

    # 回调只负责刷新网页进度，不参与 RAG 结果计算。
    if on_progress:
        on_progress(12, "阶段 1 / 4｜连接本地工艺知识库（Chroma + BGE-ZH）")
    vector_store, feature_chain, answer_chain = load_rag_resources()

    # 特征提取失败时仍可退化为全库语义检索，保证机器人不会直接崩溃。
    try:
        if on_progress:
            on_progress(32, "阶段 2 / 4｜提取零件特征（LangChain 编排）")
        features = feature_chain.invoke({"question": enriched_question})
        features = normalize_extracted_features(features, enriched_question)
        metadata_filter = build_metadata_filter(features)
        modality_conflicts = detect_modality_conflicts(features, cad_info)
    except Exception:
        features = {}
        metadata_filter = None

    # 当前知识库只覆盖少量已标注材料和零件类型；缺少基本输入时不允许生成。
    missing_features = get_missing_required_features(features)
    if missing_features:
        if on_progress:
            on_progress(100, "信息不足：等待补充零件关键特征")
        return {
            "features": features,
            "metadata_filter": metadata_filter,
            "support_data": {"equipment": [], "tools": [], "risks": []},
            "drawing_info": drawing_info,
            "cad_info": cad_info,
            "part_id": part_id,
            "cad_similarity_results": cad_similarity_results,
            "modality_conflicts": modality_conflicts,
            "answer": build_incomplete_requirement_answer(missing_features),
            "results": [],
            "is_grounded": False,
            "top_score": 0.0,
        }

    # 即使信息齐全，只要特征不在当前知识库覆盖范围，也不能用相近案例替代。
    uncovered_features = get_uncovered_features(features, supported_materials, supported_part_types)
    if uncovered_features:
        if on_progress:
            on_progress(100, "资料未覆盖：等待导入已审核工艺资料")
        return {
            "features": features,
            "metadata_filter": None,
            "support_data": {"equipment": [], "tools": [], "risks": []},
            "drawing_info": drawing_info,
            "cad_info": cad_info,
            "part_id": part_id,
            "cad_similarity_results": cad_similarity_results,
            "modality_conflicts": modality_conflicts,
            "answer": build_uncovered_requirement_answer(uncovered_features),
            "results": [],
            "is_grounded": False,
            "top_score": 0.0,
        }

    support_data = select_support_data(features)

    if on_progress:
        on_progress(56, "阶段 3 / 4｜检索相似案例（Chroma + BGE-ZH）")
    # 默认返回最相近的 3 条资料，并尽可能附加材料/类型筛选。
    search_kwargs = {"k": 3}
    if metadata_filter:
        search_kwargs["filter"] = metadata_filter

    results = vector_store.similarity_search_with_relevance_scores(
        enriched_question,
        **search_kwargs,
    )
    # 防御性限制分数范围，避免旧 L2 索引或异常值被直接显示为负数。
    results = [(document, max(0.0, min(1.0, score))) for document, score in results]

    top_score = results[0][1] if results else 0.0
    # 严格筛选没有找到可靠资料时，回退全库，避免未标元数据的手册永远无法命中。
    if metadata_filter and (not results or top_score < MIN_RELEVANCE_SCORE):
        fallback_results = vector_store.similarity_search_with_relevance_scores(
            enriched_question,
            k=3,
        )
        fallback_results = [
            (document, max(0.0, min(1.0, score)))
            for document, score in fallback_results
        ]
        fallback_top_score = fallback_results[0][1] if fallback_results else 0.0
        if fallback_top_score > top_score:
            results = fallback_results
            top_score = fallback_top_score
            metadata_filter = None

    if not results or top_score < MIN_RELEVANCE_SCORE:
        if on_progress:
            on_progress(100, "检索完成：知识库依据不足")
        return {
            "features": features,
            "metadata_filter": metadata_filter,
            "support_data": support_data,
            "drawing_info": drawing_info,
            "cad_info": cad_info,
            "part_id": part_id,
            "cad_similarity_results": cad_similarity_results,
            "modality_conflicts": modality_conflicts,
            "answer": (
                "知识库资料不足，暂时无法给出可靠的加工工艺推荐。"
                "请补充材料、零件类型、关键尺寸、精度要求，"
                "或向知识库导入相关工艺资料。"
            ),
            "results": results,
            "is_grounded": False,
            "top_score": top_score,
        }

    # 将检索资料整理成上下文，作为答案模型唯一可靠的工艺依据。
    context = "\n\n".join(
        (
            f"参考来源：{get_source_label(document)}\n"
            f"来源类型：{document.metadata.get('source_type', '未知')}\n"
            f"{document.page_content.strip()}"
        )
        for document, _ in results
    )
    context = f"{context}\n\n{format_support_context(support_data)}"
    if modality_conflicts:
        context += "\n\n跨模态冲突提示（必须人工确认）：" + "；".join(modality_conflicts)

    if on_progress:
        on_progress(68, "阶段 4 / 4｜推演工艺方案（DeepSeek）")
    answer = answer_chain.invoke(
        {
            "context": context,
            "question": question,
        }
    )

    if on_progress:
        on_progress(100, "推演完成｜DeepSeek 已生成可追溯方案")

    return {
        "features": features,
        "metadata_filter": metadata_filter,
        "answer": answer,
        "results": results,
        "support_data": support_data,
        "drawing_info": drawing_info,
        "cad_info": cad_info,
        "part_id": part_id,
        "cad_similarity_results": cad_similarity_results,
        "modality_conflicts": modality_conflicts,
        "is_grounded": True,
        "top_score": top_score,
    }


def search_cad_models(cad_file, requested_part_id: str = "") -> tuple[dict, list[dict]]:
    """CAD-only 模式：只解析上传模型并检索相似企业模型，不调用 DeepSeek。"""

    if not cad_file:
        raise ValueError("请上传 STEP 或 STP 文件。")
    part_id = resolve_part_id("", None, cad_file, requested_part_id)
    temp_path = save_uploaded_cad_temp(cad_file)
    try:
        cad_info = attach_cad_design_metadata(extract_step_features(temp_path, part_id))
    finally:
        temp_path.unlink(missing_ok=True)
    return cad_info, retrieve_similar_cad(cad_info, top_k=5)


