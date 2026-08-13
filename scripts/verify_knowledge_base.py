"""验证本地 Chroma 知识库是否能检索到正确的工艺资料。

请从项目根目录运行此脚本。
"""

# 本脚本不调用 DeepSeek；只检查本地向量库是否能按预期检索。
import sys
from pathlib import Path

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config.paths import PROCESS_VECTOR_DIR  # noqa: E402


# 这是一个应稳定命中 CASE-006 的回归测试问题。
QUESTION = "加工一根直径40mm的45钢传动轴，带平键键槽，外圆Ra1.6，推荐工艺。"
EXPECTED_CASE_ID = "CASE-006"


# 查询必须使用与建库相同的嵌入模型，否则两个向量空间无法比较。
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-zh-v1.5",
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

# 打开已持久化到 data/vector_stores/process 的集合；不会修改集合内容。
vector_store = Chroma(
    collection_name="machining_cases",
    persist_directory=str(PROCESS_VECTOR_DIR),
    embedding_function=embeddings,
)

# 同时按材料和零件类型筛选，验证 metadata 也正确写入了数据库。
results = vector_store.similarity_search_with_relevance_scores(
    QUESTION,
    k=4,
    filter={
        "$and": [
            {"material": "45钢"},
            {"part_type": "轴类"},
        ]
    },
)

print(f"测试问题：{QUESTION}\n")
print("检索结果：")

# 使用集合收集来源编号，后续只需判断目标案例是否出现。
source_labels = set()

for document, score in results:
    label = document.metadata.get(
        "case_id",
        document.metadata.get("chunk_id", "未知来源"),
    )
    source_labels.add(label)
    print(f"- {label}｜{document.metadata.get('source_type')}｜相似度：{score:.3f}")

if EXPECTED_CASE_ID not in source_labels:
    raise AssertionError(
        "验证失败：没有检索到 CASE-006。"
    )

# 不依赖会随资料数量变化的 DOC 编号，而是验证至少有一条“文档资料”已入库。
all_metadata = vector_store.get(include=["metadatas"])["metadatas"]
if not any(metadata.get("source_type") == "文档资料" for metadata in all_metadata):
    raise AssertionError("验证失败：知识库中未找到文档资料。")

print("\n验证通过：检索到了 CASE-006，且知识库包含文档资料。")
