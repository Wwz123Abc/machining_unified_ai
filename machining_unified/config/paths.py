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

KNOWLEDGE_DIR = DATA_ROOT / "knowledge"
PROCESS_CASES_PATH = KNOWLEDGE_DIR / "process_cases.json"
PROCESS_EXTENSIONS_PATH = KNOWLEDGE_DIR / "process_extensions.json"
PROCESS_RULES_PATH = KNOWLEDGE_DIR / "process_rules.json"
EQUIPMENT_PATH = KNOWLEDGE_DIR / "equipment.json"
TOOLS_PATH = KNOWLEDGE_DIR / "tools.json"
PUBLIC_SOURCE_CATALOG_PATH = KNOWLEDGE_DIR / "public_source_catalog.md"
SOURCE_DOCUMENTS_DIR = KNOWLEDGE_DIR / "source_documents"
TEMPLATES_DIR = KNOWLEDGE_DIR / "templates"

ENTERPRISE_DIR = DATA_ROOT / "enterprise"
CAD_SAMPLES_DIR = ENTERPRISE_DIR / "cad_samples"
ASSEMBLY_PACKAGES_DIR = ENTERPRISE_DIR / "assembly_packages"

RUNTIME_DIR = DATA_ROOT / "runtime"
CHAT_HISTORY_PATH = RUNTIME_DIR / "chat_history.json"

LOCAL_MODELS_DIR = DATA_ROOT / "models"
TESSDATA_DIR = LOCAL_MODELS_DIR / "tessdata"

VECTOR_STORES_DIR = DATA_ROOT / "vector_stores"
PROCESS_VECTOR_DIR = VECTOR_STORES_DIR / "process"
CAD_VECTOR_DIR = VECTOR_STORES_DIR / "cad_semantic"
ENTERPRISE_VECTOR_DIR = VECTOR_STORES_DIR / "enterprise"
MULTIMODAL_VECTOR_DIR = VECTOR_STORES_DIR / "multimodal"


def ensure_runtime_directories() -> None:
    """仅创建允许在应用运行时写入的本地目录。"""

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
