"""企业资料知识库：索引真实 STEP、BOM 和工程图 PDF，并提供可溯源问答。"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pypdf import PdfReader
from rank_bm25 import BM25Plus

from machining_unified.cad.extraction import textify_cad_features
from machining_unified.cad.retrieval import load_cad_catalog
from machining_unified.config.paths import ASSEMBLY_PACKAGES_DIR, ENTERPRISE_VECTOR_DIR, PROJECT_ROOT
from machining_unified.retrieval.cad_rag import get_embeddings


KB_VECTOR_DIR = ENTERPRISE_VECTOR_DIR


def _normalized_part_id(value: str) -> str:
    """消除 BOM 三位前缀与工程图版本尾缀，得到可跨资料关联的标准图号。"""
    text = re.sub(r"\.[^.]+$", "", value.upper())
    text = re.sub(r"^[0-9]{3}(?=DTXT)", "", text)
    return re.sub(r"([0-9]{3})[A-Z]$", r"\1", text)


def _part_ids(text: str) -> set[str]:
    """提取企业资料中常见的 DTXT 图号，并转为标准编号。"""
    return {
        _normalized_part_id(value)
        for value in re.findall(r"(?:[0-9]{3})?DTXT[0-9]{3}-[0-9]{3}-[0-9]{3}[A-Z]?", text.upper())
    }


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
            normalized_id = str(item.get("normalized_part_id") or _normalized_part_id(part_id))
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
        except Exception as error:
            pages = [f"工程图 PDF 无法读取：{error}"]
        for page_index, text in enumerate(pages, start=1):
            body = text.strip() or "该工程图页未提取到可搜索文字，需要 OCR 后才能问答。"
            normalized_id = _normalized_part_id(path.stem)
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


def retrieve_enterprise_knowledge(question: str, top_k: int = 5) -> list[dict[str, Any]]:
    """融合真实资料的向量语义与关键词检索，并保留每条来源。"""
    documents = enterprise_documents()
    bm25_scores = _bm25_scores(question, documents)
    requested_part_ids = _part_ids(question)
    vector_scores: dict[str, float] = {}
    warning: str | None = None
    try:
        for document, distance in enterprise_vector_store().similarity_search_with_score(question, k=min(len(documents), top_k * 4)):
            vector_scores[document.metadata["source_id"]] = max(0.0, min(1.0, 1.0 - float(distance)))
    except Exception as error:
        warning = f"企业知识库向量检索不可用，已降级为关键词检索：{error}"

    results = []
    for document in documents:
        source_id = document.metadata["source_id"]
        vector = vector_scores.get(source_id, 0.0)
        lexical = bm25_scores.get(source_id, 0.0)
        score = 0.72 * vector + 0.28 * lexical if vector_scores else lexical
        document_part_ids = _part_ids(
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
    return sorted(results, key=lambda item: (item["identifier_match"], item["score"]), reverse=True)[:top_k]


def _history_text(history: list[dict[str, Any]] | None) -> str:
    recent = (history or [])[-8:]
    return "\n".join(f"{item.get('role', 'user')}：{str(item.get('content', ''))[:600]}" for item in recent)


def _is_small_talk(question: str) -> bool:
    """识别不包含工程检索意图的短消息，避免把随机工程资料当作回答依据。"""
    compact = re.sub(r"[\s，。！？、,.!?：:；;~～]+", "", question).lower()
    greetings = {"你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "你是谁", "谢谢", "感谢", "好的", "ok", "okay"}
    return compact in greetings


def _knowledge_scope_reply() -> dict[str, Any]:
    """对问候类消息说明知识库范围，不进行无依据的资料召回。"""
    return {
        "answer": "你好！这里是企业机械知识库。你可以询问零件图号、材料、表面处理、BOM、工程图要求或装配资料。",
        "evidence": [],
        "generated": False,
        "warning": None,
    }


def answer_enterprise_question(
    question: str,
    top_k: int = 5,
    history: list[dict[str, Any]] | None = None,
    generate: bool = True,
) -> dict[str, Any]:
    """基于企业资料证据回答；无模型服务时返回可核对的本地证据摘要。"""
    if _is_small_talk(question):
        return _knowledge_scope_reply()
    evidence = retrieve_enterprise_knowledge(question, top_k=top_k)
    if not evidence:
        return {"answer": "企业知识库中没有可用资料。", "evidence": [], "generated": False}
    for index, item in enumerate(evidence, start=1):
        item["citation"] = f"S{index}"
    context = "\n\n".join(
        f"[{item['citation']}] 原始来源编号：{item['document'].metadata['source_id']}。"
        f"{item['document'].page_content}"
        for item in evidence
    )
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    warning = next((item["warning"] for item in evidence if item.get("warning")), None)
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
            return {
                "answer": response.content if isinstance(response.content, str) else str(response.content),
                "evidence": evidence,
                "generated": True,
                "warning": warning,
            }
        except Exception as error:
            warning = "；".join(filter(None, [warning, f"模型生成不可用，已返回资料摘要：{error}"]))

    sources = "\n".join(
        f"- [{item['document'].metadata['source_id']}] {item['document'].metadata['source_kind']}：{item['document'].metadata['title']}"
        for item in evidence
    )
    return {
        "answer": f"已检索到以下企业资料，请依据来源核对：\n{sources}",
        "evidence": evidence,
        "generated": False,
        "warning": warning,
    }


def answer_assistant_question(question: str, top_k: int = 5, history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """企业 AI 助手模式：资料优先，同时允许通用解释与建议。"""
    if _is_small_talk(question):
        return _knowledge_scope_reply()
    strict_result = answer_enterprise_question(question, top_k=top_k, history=history, generate=False)
    evidence = strict_result["evidence"]
    context = "\n\n".join(
        f"[{item.get('citation', 'S?')}] {item['document'].page_content}" for item in evidence
    )
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        strict_result["warning"] = "未配置模型服务，AI 助手模式已返回严格知识库结果。"
        strict_result["assistant_mode"] = True
        return strict_result
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
        return {
            "answer": response.content if isinstance(response.content, str) else str(response.content),
            "evidence": evidence,
            "generated": True,
            "assistant_mode": True,
            "warning": strict_result.get("warning"),
        }
    except Exception as error:
        strict_result["warning"] = f"AI 助手生成不可用，已返回严格知识库结果：{error}"
        strict_result["assistant_mode"] = True
        return strict_result
