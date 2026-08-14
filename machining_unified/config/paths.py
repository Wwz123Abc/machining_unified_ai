"""项目内所有数据路径的唯一注册表。"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"
DATA_ROOT = PROJECT_ROOT / "data"

CATALOG_DIR = DATA_ROOT / "catalogs"
CAD_CATALOG_PATH = CATALOG_DIR / "cad_models.json"
CAD_DUPLICATES_PATH = CATALOG_DIR / "cad_duplicates.json"
PART_MANIFEST_PATH = CATALOG_DIR / "part_manifest.json"
MULTIMODAL_MANIFEST_PATH = CATALOG_DIR / "unified_multimodal_manifest.json"
# 装配拆解的来源台账：记录每个零件出自哪个装配、被哪些装配共用。
DECOMPOSED_PARTS_PATH = CATALOG_DIR / "decomposed_parts.json"

KNOWLEDGE_DIR = DATA_ROOT / "knowledge"
TEMPLATES_DIR = KNOWLEDGE_DIR / "templates"

# 检索打分权重的外置配置；文件不存在时使用代码内的默认值。
CONFIG_DIR = DATA_ROOT / "config"
RETRIEVAL_PARAMS_PATH = CONFIG_DIR / "retrieval_params.json"

ENTERPRISE_DIR = DATA_ROOT / "enterprise"
CAD_SAMPLES_DIR = ENTERPRISE_DIR / "cad_samples"
ASSEMBLY_PACKAGES_DIR = ENTERPRISE_DIR / "assembly_packages"

RUNTIME_DIR = DATA_ROOT / "runtime"
CHAT_HISTORY_PATH = RUNTIME_DIR / "chat_history.json"
# 三维预览的三角网格落盘缓存。网格化（BRepMesh）发生在三角面截断之前，
# 是预览耗时的真正来源，且进程内 lru_cache 重启即失效。
MESH_CACHE_DIR = RUNTIME_DIR / "meshes"

VECTOR_STORES_DIR = DATA_ROOT / "vector_stores"
CAD_VECTOR_DIR = VECTOR_STORES_DIR / "cad_semantic"
ENTERPRISE_VECTOR_DIR = VECTOR_STORES_DIR / "enterprise"
MULTIMODAL_VECTOR_DIR = VECTOR_STORES_DIR / "multimodal"


def ensure_runtime_directories() -> None:
    """仅创建允许在应用运行时写入的本地目录。"""

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MESH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
