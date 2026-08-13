# 从项目根目录运行本脚本：它会删除旧向量库并按当前资料完整重建。

import json
import shutil
import sys
from pathlib import Path

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


# 支持从任意工作目录运行脚本，并复用项目根目录的元数据规则。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from machining_unified.config.paths import (
    CAD_CATALOG_PATH,
    PROCESS_CASES_PATH,
    PROCESS_EXTENSIONS_PATH,
    PROCESS_VECTOR_DIR,
    SOURCE_DOCUMENTS_DIR,
)
from machining_unified.knowledge.manifests import load_part_manifest
from machining_unified.knowledge.metadata import derive_case_metadata


# 集中定义数据目录，后续改项目路径时只需要修改这里。
data_directory = PROJECT_ROOT / "data"
cases_file = PROCESS_CASES_PATH
extension_cases_file = PROCESS_EXTENSIONS_PATH
source_directory = SOURCE_DOCUMENTS_DIR
db_directory = PROCESS_VECTOR_DIR


def infer_metadata_from_filename(file_path: Path) -> dict:
    """从文件名识别材料、零件类型，供 Chroma 筛选使用。"""

    # stem 是不带扩展名的文件名；本项目约定把材料和零件类型写进文件名。
    filename = file_path.stem
    metadata = {
        "source_file": file_path.name,
        "source_type": "文档资料",
    }

    material_keywords = ["45钢", "304不锈钢", "HT250", "6061铝合金"]
    part_type_keywords = ["轴类", "套筒类", "箱体类", "盘类"]

    # 找到第一个匹配关键词后立即停止，写入供 Chroma 过滤使用的 metadata。
    for material in material_keywords:
        if material in filename:
            metadata["material"] = material
            break

    for part_type in part_type_keywords:
        if part_type in filename:
            metadata["part_type"] = part_type
            break

    return metadata


def load_json_case_documents() -> tuple[list[Document], list[str]]:
    """读取 JSON 案例，转换为 LangChain Document。"""

    # 案例 JSON 是最基础的数据源，缺失时不能继续建库。
    if not cases_file.exists():
        raise FileNotFoundError(f"未找到案例文件：{cases_file.resolve()}")

    with cases_file.open("r", encoding="utf-8") as file:
        cases = json.load(file)
    if extension_cases_file.exists():
        with extension_cases_file.open("r", encoding="utf-8") as file:
            cases.extend(json.load(file))

    documents = []
    document_ids = []

    # 每一条 JSON 案例转换成 LangChain Document，才能和普通文档统一入库。
    for case in cases:
        # 只提取正文中明确出现的字段；缺失信息保持“未标注”，不进行猜测。
        process_metadata = derive_case_metadata(case["content"])
        page_content = (
            f"案例名称：{case['title']}\n"
            f"材料：{case['material']}\n"
            f"零件类型：{case['part_type']}\n"
            f"粗糙度：{process_metadata['roughness_ra']}\n"
            f"精度要求：{process_metadata['precision_requirement']}\n"
            f"热处理：{process_metadata['heat_treatment']}\n"
            f"特殊特征：{process_metadata['special_features']}\n"
            f"{case['content']}"
        )

        # page_content 参与向量化；metadata 参与精确过滤和来源展示。
        documents.append(
            Document(
                page_content=page_content,
                metadata={
                    "case_id": case["case_id"],
                    "title": case["title"],
                    "material": case["material"],
                    "part_type": case["part_type"],
                    "source_file": cases_file.name,
                    "source_type": case.get("source_type", "结构化案例"),
                    "review_status": case.get("review_status", "approved"),
                    **process_metadata,
                },
            )
        )

        document_ids.append(case["case_id"])

    return documents, document_ids


def load_document_chunks() -> list[Document]:
    """读取 source_documents 里的 PDF/TXT，并切分为文本块。"""

    # 允许第一次运行时目录尚不存在；自动创建空资料目录。
    source_directory.mkdir(exist_ok=True)

    raw_documents = []

    # 排序保证每次切块编号 DOC-001、DOC-002 的生成顺序稳定。
    for file_path in sorted(source_directory.iterdir()):
        if not file_path.is_file():
            continue

        try:
            # 根据扩展名选择对应加载器；未知格式跳过而不是让全流程失败。
            if file_path.suffix.lower() == ".pdf":
                loaded_documents = PyPDFLoader(str(file_path)).load()

            elif file_path.suffix.lower() == ".txt":
                loaded_documents = TextLoader(
                    str(file_path),
                    encoding="utf-8",
                ).load()

            else:
                print(f"跳过不支持的文件：{file_path.name}")
                continue

            extra_metadata = infer_metadata_from_filename(file_path)

            # 将从文件名推断出的材料、类型补充到加载器产生的每一页文档。
            for document in loaded_documents:
                document.metadata.update(extra_metadata)

            raw_documents.extend(loaded_documents)
            print(f"已读取资料：{file_path.name}")

        except Exception as error:
            print(f"读取失败，已跳过：{file_path.name}，原因：{error}")

    if not raw_documents:
        return []

    # 长文分块后检索更精确；少量重叠避免关键信息被截在块边界。
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=80,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )

    chunks = splitter.split_documents(raw_documents)

    # 给每个块可读、可追溯的编号，方便结果页展示来源。
    for index, chunk in enumerate(chunks, start=1):
        chunk.metadata["chunk_id"] = f"DOC-{index:03d}"

    return chunks


def load_cad_documents() -> list[Document]:
    """将 CAD 特征文本纳入同一个向量库，保留 part_id 和原模型路径。"""

    catalog_file = CAD_CATALOG_PATH
    if not catalog_file.exists():
        return []
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    manifest_by_id = {item["part_id"]: item for item in load_part_manifest()}
    documents = []
    for record in catalog:
        manifest = manifest_by_id.get(record["part_id"], {})
        features = record["features"]
        documents.append(
            Document(
                page_content=record["search_text"],
                metadata={
                    "cad_id": record["part_id"],
                    "part_id": record["part_id"],
                    "model_group_id": record.get("model_group_id", record["part_id"]),
                    "model_group_type": record.get("model_group_type", "单模型"),
                    "group_component_count": str(record.get("group_component_count", 1)),
                    "source_file": record["file_name"],
                    "source_type": "CAD/3D 模型特征",
                    "material": manifest.get("material", "未标注"),
                    "part_type": manifest.get("part_type", "未标注"),
                    "review_status": record.get("review_status", "pending_manual_review"),
                    "geometry_confidence": features.get("geometry_confidence", "low"),
                    "solid_count": str(features.get("solid_count", "未标注")),
                    "feature_candidates": "、".join(features.get("machining_feature_candidates", [])),
                },
            )
        )
    return documents


# 1. 读取两类知识来源
# 第 1 步：读取结构化案例和非结构化工艺资料。
case_documents, case_ids = load_json_case_documents()
document_chunks = load_document_chunks()
cad_documents = load_cad_documents()

# 合并为同一个待向量化列表，案例 ID 与文档块 ID 必须保持一一对应。
all_documents = case_documents + document_chunks + cad_documents
all_ids = case_ids + [
    chunk.metadata["chunk_id"]
    for chunk in document_chunks
] + [f"CAD-{document.metadata['cad_id']}" for document in cad_documents]

if not all_documents:
    raise ValueError("没有可写入知识库的内容。")


# 2. 加载中文向量模型
# 第 2 步：加载 BGE 中文嵌入模型，并将向量归一化以支持余弦相似度。
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-zh-v1.5",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)


# 3. 重建 Chroma 知识库
# 第 3 步：删除旧库后重新创建。网页运行时会占用文件，因此先停止 Streamlit。
if db_directory.exists():
    try:
        shutil.rmtree(db_directory)
        print(f"已清空旧知识库：{db_directory.resolve()}")
    except PermissionError as error:
        raise RuntimeError(
            "知识库正在被网页程序占用。请先停止 Streamlit 网页服务，再重新运行。"
        ) from error

# 将文本、向量模型、稳定 ID 与余弦距离配置一次性写入本地持久化数据库。
Chroma.from_documents(
    documents=all_documents,
    embedding=embeddings,
    ids=all_ids,
    collection_name="machining_cases",
    persist_directory=str(db_directory),
    collection_metadata={"hnsw:space": "cosine"},
)


print("\n知识库重建完成。")
print(f"结构化案例数量：{len(case_documents)}")
print(f"文档切分数量：{len(document_chunks)}")
print(f"CAD 特征数量：{len(cad_documents)}")
print(f"知识块总数：{len(all_documents)}")
print(f"知识库位置：{db_directory.resolve()}")
