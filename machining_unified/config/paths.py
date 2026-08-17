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
# 三维预览的三角网格落盘缓存。网格化（BRepMesh）发生在三角面截断之前，
# 是预览耗时的真正来源，且进程内 lru_cache 重启即失效。
MESH_CACHE_DIR = RUNTIME_DIR / "meshes"
# 图片检索用的三角网格落盘缓存，与 MESH_CACHE_DIR 是两套独立缓存：
# 两者都缓存三角网格而非渲染图（一旦网格在缓存里，旋转到任意角度只是内存里的
# 投影计算，不必再缓存每个角度的图片），但坐标口径不同——MESH_CACHE_DIR 里的
# 网格经过了 TopLoc 变换与居中缩放归一化（服务于 3D 查看器画布），这里的网格是
# cad/visual.py 用于渲染预览图和 CLIP 编码的原始坐标，两者不能混用同一份缓存。
VISUAL_CACHE_DIR = RUNTIME_DIR / "visual_previews"
# 知识图谱构图结果的磁盘缓存。508 条目录构图是 O(n^2) 的圆柱接口两两比较，
# 实测 55~90 秒；跨进程/重启复用，避免每次冷启动都重新付出这个代价。
KNOWLEDGE_GRAPH_CACHE_PATH = RUNTIME_DIR / "knowledge_graph.json"

VECTOR_STORES_DIR = DATA_ROOT / "vector_stores"
CAD_VECTOR_DIR = VECTOR_STORES_DIR / "cad_semantic"
MULTIMODAL_VECTOR_DIR = VECTOR_STORES_DIR / "multimodal"


def ensure_runtime_directories() -> None:
    """仅创建允许在应用运行时写入的本地目录。"""

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MESH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    VISUAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
