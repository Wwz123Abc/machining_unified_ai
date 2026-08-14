"""企业资料知识库：索引真实 STEP、BOM 和工程图 PDF，并提供可溯源问答。"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from chromadb.errors import ChromaError
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from rank_bm25 import BM25Plus

from machining_unified.cad.extraction import textify_cad_features
from machining_unified.cad.retrieval import load_cad_catalog
from machining_unified.config.paths import ASSEMBLY_PACKAGES_DIR, ENTERPRISE_VECTOR_DIR, PROJECT_ROOT
from machining_unified.config.retrieval_params import get_retrieval_params
from machining_unified.dto import EnterpriseAnswer, EnterpriseEvidence
from machining_unified.knowledge.part_ids import extract_part_ids, normalized_part_id
from machining_unified.retrieval.cad_rag import get_embeddings


logger = logging.getLogger(__name__)

KB_VECTOR_DIR = ENTERPRISE_VECTOR_DIR




def _relative_path(path: Path) -> str:
    """返回项目内相对路径，作为界面与回答中的可审计来源。"""
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _tokens(text: str) -> list[str]:
    """为中文工程资料提供简单、可复现的 BM25 分词。"""
    normalized = text.lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9._-]*", normalized)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    tokens.extend(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return tokens or ["empty"]


def _manifest_documents() -> list[Document]:
    """把导入清单中的装配信息和 BOM 行写成来源明确的文档。"""
    documents: list[Document] = []
    for manifest_path in sorted(ASSEMBLY_PACKAGES_DIR.glob("*/assembly_manifest.json")):
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assembly_id = str(manifest["assembly_id"])
        documents.append(
            Document(
                page_content=(
                    f"企业装配 BOM。装配图号：{assembly_id}。"
                    f"BOM 文件：{manifest.get('bom_file', '')}。"
                    f"物料条目数：{manifest.get('component_count', 0)}。"
                ),
                metadata={
                    "source_id": f"assembly:{assembly_id}",
                    "source_kind": "装配 BOM",
                    "title": f"{assembly_id} 装配 BOM",
                    "source_file": _relative_path(manifest_path),
                    "part_id": assembly_id,
                },
            )
        )
        for index, item in enumerate(manifest.get("bom_items", []), start=1):
            part_id = str(item.get("part_id", ""))
            fields = {
                "零件编号": part_id,
                "标准零件编号": item.get("normalized_part_id", ""),
                "名称": item.get("name", ""),
                "规格": item.get("specification", ""),
                "图号": item.get("drawing_no", ""),
                "材料": item.get("material", ""),
                "表面处理": item.get("surface_treatment", ""),
                "数量": item.get("quantity", ""),
                "单位": item.get("unit", ""),
            }
            content = "；".join(f"{name}：{value}" for name, value in fields.items() if value not in (None, ""))
            normalized_id = str(item.get("normalized_part_id") or normalized_part_id(part_id))
            documents.append(
                Document(
                    page_content=f"企业 BOM 条目。所属装配：{assembly_id}。{content}。",
                    metadata={
                        "source_id": f"bom:{assembly_id}:{index}",
                        "source_kind": "BOM 条目",
                        "title": item.get("name") or part_id,
                        "source_file": manifest.get("bom_file", _relative_path(manifest_path)),
                        "part_id": part_id,
                        "normalized_part_id": normalized_id,
                        "drawing_file": item.get("drawing_file") or "",
                    },
                )
            )
    return documents


def _drawing_documents() -> list[Document]:
    """按页提取工程图 PDF 的真实文本；空文本会明确标记为需 OCR。"""
    documents: list[Document] = []
    paths = sorted(set(ASSEMBLY_PACKAGES_DIR.rglob("*.pdf")) | set(ASSEMBLY_PACKAGES_DIR.rglob("*.PDF")))
    for path in paths:
        try:
            reader = PdfReader(path)
            pages = [page.extract_text() or "" for page in reader.pages]
        except (OSError, PyPdfError) as error:
            # 已枚举的失败模式：文件读不了，或 PDF 结构损坏/加密（PyPdfError 是 pypdf 的根异常）。
            # 单份图纸损坏不能中断整个证据库构建，但必须留下可追溯记录，
            # 否则这份图纸会以“无法读取”的占位文本静默进入索引。
            logger.exception("工程图 PDF 解析失败", extra={"source_file": _relative_path(path)})
            pages = [f"工程图 PDF 无法读取：{error}"]
        for page_index, text in enumerate(pages, start=1):
            body = text.strip() or "该工程图页未提取到可搜索文字，需要 OCR 后才能问答。"
            normalized_id = normalized_part_id(path.stem)
            documents.append(
                Document(
                    page_content=(
                        f"企业工程图。文件：{path.name}。标准零件编号：{normalized_id}。"
                        f"页码：{page_index}。\n{body}"
                    ),
                    metadata={
                        "source_id": f"drawing:{path.stem}:{page_index}",
                        "source_kind": "工程图 PDF",
                        "title": path.name,
                        "source_file": _relative_path(path),
                        "part_id": path.stem,
                        "normalized_part_id": normalized_id,
                        "page": str(page_index),
                    },
                )
            )
    return documents


def _cad_documents() -> list[Document]:
    """索引实际 STEP 提取的几何事实，不写入类别功能模板。"""
    documents: list[Document] = []
    for record in load_cad_catalog():
        documents.append(
            Document(
                page_content=f"企业 STEP 几何资料。{textify_cad_features(record)}",
                metadata={
                    "source_id": f"step:{record['part_id']}",
                    "source_kind": "STEP 几何",
                    "title": record["part_id"],
                    "source_file": record.get("source_file", ""),
                    "part_id": record["part_id"],
                },
            )
        )
    return documents


@lru_cache(maxsize=1)
def enterprise_documents() -> tuple[Document, ...]:
    """汇总当前企业资料库；索引重建或资料更新后应重启应用以刷新缓存。"""
    return tuple(_cad_documents() + _manifest_documents() + _drawing_documents())


@lru_cache(maxsize=1)
def enterprise_vector_store() -> Chroma:
    if not KB_VECTOR_DIR.exists():
        raise FileNotFoundError("企业知识库尚未建立，请先运行 scripts/build_enterprise_kb.py")
    return Chroma(
        collection_name="enterprise_knowledge",
        embedding_function=get_embeddings(),
        persist_directory=str(KB_VECTOR_DIR),
        collection_metadata={"hnsw:space": "cosine"},
    )


def _bm25_scores(question: str, documents: tuple[Document, ...]) -> dict[str, float]:
    ranker = BM25Plus([_tokens(document.page_content) for document in documents])
    values = list(ranker.get_scores(_tokens(question)))
    minimum, maximum = min(values, default=0.0), max(values, default=0.0)
    span = maximum - minimum
    return {
        document.metadata["source_id"]: ((float(value) - minimum) / span if span else 0.0)
        for document, value in zip(documents, values)
    }


def retrieve_enterprise_knowledge(question: str, top_k: int = 5) -> tuple[EnterpriseEvidence, ...]:
    """融合真实资料的向量语义与关键词检索，并保留每条来源。

    引用编号 ``S#`` 在此按最终排名直接赋予：证据对象是不可变的，
    不能等到生成回答时再就地写入，否则同一份证据在不同调用间会互相污染。
    """
    documents = enterprise_documents()
    weights = get_retrieval_params().enterprise
    bm25_scores = _bm25_scores(question, documents)
    requested_part_ids = extract_part_ids(question)
    vector_scores: dict[str, float] = {}
    warning: str | None = None
    try:
        for document, distance in enterprise_vector_store().similarity_search_with_score(question, k=min(len(documents), top_k * 4)):
            vector_scores[document.metadata["source_id"]] = max(0.0, min(1.0, 1.0 - float(distance)))
    except (ChromaError, OSError, ValueError, RuntimeError) as error:
        # 已枚举：库目录缺失（FileNotFoundError）、SQLite/HNSW 文件损坏或被占用（OSError）、
        # Chroma 自身错误（ChromaError 是其根异常）、维度不匹配等参数错误。
        # 降级为纯 BM25 会显著改变召回质量，必须同时上报界面（warning）与日志（可追溯）。
        logger.exception(
            "企业知识库向量检索不可用，已降级为关键词检索",
            extra={"vector_dir": str(KB_VECTOR_DIR), "document_count": len(documents)},
        )
        warning = f"企业知识库向量检索不可用，已降级为关键词检索：{error}"

    results = []
    for document in documents:
        source_id = document.metadata["source_id"]
        vector = vector_scores.get(source_id, 0.0)
        lexical = bm25_scores.get(source_id, 0.0)
        score = weights.vector * vector + weights.lexical * lexical if vector_scores else lexical
        document_part_ids = extract_part_ids(
            " ".join(
                [
                    document.page_content,
                    str(document.metadata.get("part_id", "")),
                    str(document.metadata.get("normalized_part_id", "")),
                    str(document.metadata.get("source_file", "")),
                ]
            )
        )
        identifier_match = bool(requested_part_ids & document_part_ids)
        raw_text = document.page_content.replace("\n", " ")
        excerpt_limit = 1200
        excerpt = raw_text[:excerpt_limit]
        results.append(
            {
                "document": document,
                "score": round(float(score), 4),
                "vector_score": round(float(vector), 4),
                "lexical_score": round(float(lexical), 4),
                "excerpt": excerpt,
                "excerpt_truncated": len(raw_text) > excerpt_limit,
                "warning": warning,
                "identifier_match": identifier_match,
            }
        )
    # 精确图号是企业资料查询中最强的证据，应优先于泛化的语义相似度。
    ranked = sorted(results, key=lambda item: (item["identifier_match"], item["score"]), reverse=True)[:top_k]
    return tuple(
        EnterpriseEvidence(
            citation=f"S{index}",
            score=item["score"],
            vector_score=item["vector_score"],
            lexical_score=item["lexical_score"],
            identifier_match=item["identifier_match"],
            excerpt=item["excerpt"],
            excerpt_truncated=item["excerpt_truncated"],
            warning=item["warning"],
            document=item["document"],
        )
        for index, item in enumerate(ranked, start=1)
    )


def _history_text(history: list[dict[str, Any]] | None) -> str:
    recent = (history or [])[-8:]
    return "\n".join(f"{item.get('role', 'user')}：{str(item.get('content', ''))[:600]}" for item in recent)


def _is_small_talk(question: str) -> bool:
    """识别不包含工程检索意图的短消息，避免把随机工程资料当作回答依据。"""
    compact = re.sub(r"[\s，。！？、,.!?：:；;~～]+", "", question).lower()
    greetings = {"你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "你是谁", "谢谢", "感谢", "好的", "ok", "okay"}
    return compact in greetings


def _knowledge_scope_reply() -> EnterpriseAnswer:
    """对问候类消息说明知识库范围，不进行无依据的资料召回。"""
    return EnterpriseAnswer(
        answer="你好！这里是企业机械知识库。你可以询问零件图号、材料、表面处理、BOM、工程图要求或装配资料。",
        evidence=(),
        generated=False,
    )


def answer_enterprise_question(
    question: str,
    top_k: int = 5,
    history: list[dict[str, Any]] | None = None,
    generate: bool = True,
) -> EnterpriseAnswer:
    """基于企业资料证据回答；无模型服务时返回可核对的本地证据摘要。"""
    if _is_small_talk(question):
        return _knowledge_scope_reply()
    evidence = retrieve_enterprise_knowledge(question, top_k=top_k)
    if not evidence:
        return EnterpriseAnswer(answer="企业知识库中没有可用资料。", evidence=(), generated=False)
    context = "\n\n".join(
        f"[{item.citation}] 原始来源编号：{item.source_id}。{item.document.page_content}"
        for item in evidence
    )
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    warning = next((item.warning for item in evidence if item.warning), None)
    if api_key and generate:
        try:
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "你是企业机械资料知识库助手。只依据提供的企业资料回答。"
                        "每个结论后只引用方括号中的证据编号（例如 [S1]）；"
                        "资料没有明确记载时回答“资料未提供”。"
                        "不要把 STEP 几何、BOM 事实和规则推断混为一谈。",
                    ),
                    ("human", "对话历史（只用于理解指代，不是事实来源）：\n{history}\n\n问题：{question}\n\n企业资料：\n{context}"),
                ]
            )
            response = (prompt | ChatOpenAI(
                model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                temperature=0,
            )).invoke({"question": question, "context": context, "history": _history_text(history)})
            logger.info(
                "企业资料问答生成完成",
                extra={"mode": "strict", "evidence_count": len(evidence), "identifier_hits": sum(1 for i in evidence if i.identifier_match)},
            )
            return EnterpriseAnswer(
                answer=response.content if isinstance(response.content, str) else str(response.content),
                evidence=evidence,
                generated=True,
                warning=warning,
            )
        except Exception as error:  # noqa: BLE001 - 外部服务边界，见下方说明
            # 这里刻意保留宽泛捕获：调用跨越 langchain-openai、openai SDK、HTTP 栈与
            # DeepSeek 服务端，失败模式不可枚举（超时、限流、鉴权、协议变更、响应格式变化）。
            # 收窄类型的代价是漏掉一种就让整页崩溃，而这条链路的正确行为是退回本地证据摘要。
            # 代价由 logger.exception 的完整堆栈补偿，可追溯性不受影响。
            # 注：若要按类型区分处理，需把 openai 重新提升为直接依赖（见 requirements.txt）。
            logger.exception(
                "严谨模式模型生成失败，已退回本地证据摘要",
                extra={"mode": "strict", "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), "evidence_count": len(evidence)},
            )
            warning = "；".join(filter(None, [warning, f"模型生成不可用，已返回资料摘要：{error}"]))

    sources = "\n".join(f"- [{item.source_id}] {item.source_kind}：{item.title}" for item in evidence)
    return EnterpriseAnswer(
        answer=f"已检索到以下企业资料，请依据来源核对：\n{sources}",
        evidence=evidence,
        generated=False,
        warning=warning,
    )


def answer_assistant_question(
    question: str, top_k: int = 5, history: list[dict[str, Any]] | None = None
) -> EnterpriseAnswer:
    """企业 AI 助手模式：资料优先，同时允许通用解释与建议。"""
    if _is_small_talk(question):
        return _knowledge_scope_reply()
    strict_result = answer_enterprise_question(question, top_k=top_k, history=history, generate=False)
    evidence = strict_result.evidence
    context = "\n\n".join(f"[{item.citation}] {item.document.page_content}" for item in evidence)
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return replace(
            strict_result,
            warning="未配置模型服务，AI 助手模式已返回严格知识库结果。",
            assistant_mode=True,
        )
    try:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是企业机械 AI 助手。优先依据企业资料；允许补充通用工程解释、分析思路和下一步建议。"
                    "企业资料事实必须引用 [S#]，未由资料支持的内容必须明确标注“通用建议”或“推测”，不得伪造企业事实。",
                ),
                ("human", "对话历史：\n{history}\n\n问题：{question}\n\n企业资料：\n{context}"),
            ]
        )
        response = (prompt | ChatOpenAI(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), temperature=0.3,
        )).invoke({"question": question, "context": context, "history": _history_text(history)})
        logger.info("AI 助手回答生成完成", extra={"mode": "assistant", "evidence_count": len(evidence)})
        return EnterpriseAnswer(
            answer=response.content if isinstance(response.content, str) else str(response.content),
            evidence=evidence,
            generated=True,
            assistant_mode=True,
            warning=strict_result.warning,
        )
    except Exception as error:  # noqa: BLE001 - 同上，外部服务边界
        logger.exception(
            "AI 助手模型生成失败，已退回严谨知识库结果",
            extra={"mode": "assistant", "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), "evidence_count": len(evidence)},
        )
        return replace(
            strict_result,
            warning=f"AI 助手生成不可用，已返回严格知识库结果：{error}",
            assistant_mode=True,
        )
