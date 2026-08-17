"""Geometry-derived CAD previews and visual retrieval."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from chromadb.errors import ChromaError
from PIL import Image, ImageDraw

from machining_unified.config.paths import PROJECT_ROOT, VISUAL_CACHE_DIR

logger = logging.getLogger(__name__)

# 全库渲染+CLIP编码在 508 条目录上是 30 秒级；先用统一库的粗召回把候选圈定
# 到这个规模，再只对候选做渲染与精排。目录规模不超过该值时跳过粗召回，
# 直接精排全量——一次 Chroma 往返换不来收益。
_COARSE_RECALL_THRESHOLD = 60

# cadquery-ocp（requirements.txt 中的项目依赖）提供 OCP 命名空间。
# cadquery-ocp 的 OCP 模块由二进制绑定动态导出，缺少 PyCharm 可读取的存根。
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.BRep import BRep_Tool
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.BRepMesh import BRepMesh_IncrementalMesh
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.IFSelect import IFSelect_RetDone
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.STEPControl import STEPControl_Reader
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.Standard import Standard_Failure
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopAbs import TopAbs_FACE
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopExp import TopExp_Explorer
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopLoc import TopLoc_Location
# noinspection PyUnresolvedReferences,PyPackageRequirements
from OCP.TopoDS import TopoDS


# 视觉检索比较渲染后的 STEP 几何，不比较类别图标或零件编号文字。
# 仅当网格渲染确实失败时，才使用紧凑的特征预览图降级。


def _fallback_preview(record: dict[str, Any], size: int) -> Image.Image:
    # 这是透明的降级路径：它展示已存尺寸和半径，但不能视作忠实的 3D 模型渲染。
    """Feature-derived fallback when a STEP mesh cannot be triangulated."""
    features = record.get("features", {})
    bbox = features.get("bounding_box") or {}
    dims = [float(bbox.get(key) or 1) for key in ("length_x_mm", "length_y_mm", "length_z_mm")]
    image = Image.new("RGB", (size, size), "#f8fafc")
    draw = ImageDraw.Draw(image)
    scale = (size * 0.65) / max(dims)
    width, height = max(20, dims[0] * scale), max(20, dims[2] * scale)
    left, top = (size - width) / 2, (size - height) / 2
    draw.rectangle((left, top, left + width, top + height), outline="#0f4c81", width=3, fill="#cde9fb")
    radii = features.get("cylindrical_radii_mm", [])
    for index, radius in enumerate(sorted(radii)[-4:]):
        diameter = max(6, min(size * 0.55, radius * 2 * scale))
        offset = (index - 1.5) * min(16, size / 14)
        center_x, center_y = size / 2 + offset, size / 2
        draw.ellipse((center_x - diameter / 2, center_y - diameter / 2, center_x + diameter / 2, center_y + diameter / 2), outline="#0f4c81", width=2)
    return image


# 改变网格化参数（线性偏差、是否应用 TopLoc 变换）时必须递增，
# 否则会读到旧口径的网格。
_VISUAL_MESH_CACHE_VERSION = 1


def _visual_mesh_cache_key(source: Path) -> str:
    """按文件内容而非路径做键：同一零件被移动或重命名后仍应命中。"""
    digest = hashlib.md5(usedforsecurity=False)
    with source.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"{digest.hexdigest()}-v{_VISUAL_MESH_CACHE_VERSION}"


def _read_visual_mesh_cache(key: str) -> np.ndarray | None:
    path = VISUAL_CACHE_DIR / f"{key}.npz"
    if not path.is_file():
        return None
    try:
        with np.load(path) as archive:
            return archive["triangles"]
    except (OSError, ValueError, KeyError, EOFError):
        logger.warning("图片检索网格缓存不可读，将重新计算", extra={"cache_key": key}, exc_info=True)
        return None


def _write_visual_mesh_cache(key: str, triangles: np.ndarray) -> None:
    """临时文件 + replace 原子落盘，避免并发写出半个文件后被读到。"""
    try:
        VISUAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(suffix=".npz", dir=VISUAL_CACHE_DIR)
        os.close(descriptor)
        temporary_path = Path(temporary)
        np.savez_compressed(temporary_path, triangles=triangles)
        temporary_path.replace(VISUAL_CACHE_DIR / f"{key}.npz")
    except OSError:
        logger.warning("图片检索网格缓存写入失败，本次不缓存", extra={"cache_key": key}, exc_info=True)


@lru_cache(maxsize=128)
def _mesh_triangles(source_file: str) -> np.ndarray:
    """读取一次 STEP 三角网格，供缩略图和多视角统一嵌入共同复用。

    三级缓存：进程内 lru_cache -> 落盘 npz -> 真正的 OCCT 网格化。
    落盘这一级是必需的——lru_cache 重启即失效，而网格化是几十秒量级
    （见 CLAUDE.md 记录的图片检索冷缓存延迟问题）。一旦网格进了缓存，
    任意角度的预览图都只是内存里的旋转投影，不需要再按角度单独缓存。
    """
    source = Path(source_file)
    cache_key: str | None = None
    if source.is_file():
        try:
            cache_key = _visual_mesh_cache_key(source)
        except OSError:
            logger.warning("无法读取源文件计算缓存键", extra={"source_file": source_file}, exc_info=True)
        if cache_key:
            cached = _read_visual_mesh_cache(cache_key)
            if cached is not None:
                return cached

    reader = STEPControl_Reader()
    if reader.ReadFile(source_file) != IFSelect_RetDone or reader.TransferRoots() == 0:
        raise ValueError("STEP mesh read failed")
    shape = reader.OneShape()
    BRepMesh_IncrementalMesh(shape, 0.6)
    triangles: list[np.ndarray] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        # 对每个 B-Rep 面网格化，再使用一致的等轴视投影，
        # 使查询图与目录缩略图处于同一个图像比较空间。
        triangulation = BRep_Tool.Triangulation_s(TopoDS.Face_s(explorer.Current()), TopLoc_Location())
        if triangulation is not None:
            for index in range(1, int(triangulation.NbTriangles()) + 1):
                indices = triangulation.Triangle(index).Get()
                points = np.array([[triangulation.Node(node).X(), triangulation.Node(node).Y(), triangulation.Node(node).Z()] for node in indices], dtype=float)
                triangles.append(points)
        explorer.Next()
    if not triangles:
        raise ValueError("STEP contains no triangulated faces")
    result = np.stack(triangles)
    if cache_key:
        _write_visual_mesh_cache(cache_key, result)
    return result


@lru_cache(maxsize=512)
def _mesh_preview(source_file: str, size: int, angle_deg: int = 38, elevation_deg: int = 28) -> Image.Image:
    """从真实 STEP 网格生成指定方位的工程预览图。"""
    triangles = _mesh_triangles(source_file)
    all_points = triangles.reshape(-1, 3)
    center = all_points.mean(axis=0)
    angle = np.deg2rad(angle_deg)
    elevation = np.deg2rad(elevation_deg)
    rotation_z = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    rotation_x = np.array([[1, 0, 0], [0, np.cos(elevation), -np.sin(elevation)], [0, np.sin(elevation), np.cos(elevation)]])
    rotated = (triangles - center) @ rotation_z.T @ rotation_x.T
    coordinates = rotated.reshape(-1, 3)
    span = max(float(np.ptp(coordinates[:, 0])), float(np.ptp(coordinates[:, 1])), 1e-6)
    scale = size * 0.78 / span
    image = Image.new("RGB", (size, size), "#f8fafc")
    draw = ImageDraw.Draw(image)
    draw_data = []
    for points in rotated[:: max(1, len(rotated) // 4500)]:
        xy = [(size / 2 + point[0] * scale, size / 2 - point[1] * scale) for point in points]
        normal = np.cross(points[1] - points[0], points[2] - points[0])
        normal_length = max(float(np.linalg.norm(normal)), 1e-8)
        intensity = max(0.25, min(0.95, 0.55 + (float(normal[2]) / normal_length) * 0.35))
        color = (int(30 * intensity), int(118 * intensity + 70), int(175 * intensity + 55))
        draw_data.append((float(points[:, 2].mean()), xy, color))
    for _, xy, color in sorted(draw_data):
        draw.polygon(xy, fill=color, outline="#0f4c81")
    return image


def model_preview(record: dict[str, Any], size: int = 384) -> Image.Image:
    # 网格损坏不能导致图像检索不可用；调用方会得到确定性的降级图，
    # 而不是与零件无关的生成图标。
    source = PROJECT_ROOT / str(record.get("source_file", ""))
    try:
        return _mesh_preview(str(source.resolve()), size).copy()
    except (OSError, RuntimeError, Standard_Failure, ValueError):
        return _fallback_preview(record, size)


def model_previews(record: dict[str, Any], size: int = 256) -> list[Image.Image]:
    """返回八个真实网格视角，用于将 STEP 几何投影到 CLIP 图像空间。"""
    source = PROJECT_ROOT / str(record.get("source_file", ""))
    try:
        return [
            _mesh_preview(str(source.resolve()), size, angle, 25).copy()
            for angle in range(0, 360, 45)
        ]
    except (OSError, RuntimeError, Standard_Failure, ValueError):
        return [_fallback_preview(record, size)]


@lru_cache(maxsize=1)
def _clip_model():
    # 正常运行不会隐式下载模型；仅在本地没有 CLIP 权重时由部署人员显式开启下载。
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("clip-ViT-B-32", local_files_only=os.getenv("ALLOW_CLIP_DOWNLOAD", "0") != "1")


def get_clip_model():
    """返回图文共享 CLIP 编码器，供统一多模态索引复用。"""
    return _clip_model()


def _shape_fingerprint(image: Image.Image) -> np.ndarray:
    pixels = np.asarray(image.convert("L").resize((64, 64)), dtype=np.float32) / 255.0
    horizontal = np.abs(np.diff(pixels, axis=1)).mean(axis=1)
    vertical = np.abs(np.diff(pixels, axis=0)).mean(axis=0)
    histogram, _ = np.histogram(pixels, bins=16, range=(0.0, 1.0), density=True)
    vector = np.concatenate([horizontal, vertical, histogram.astype(np.float32)])
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


def _coarse_recall(query_image: Image.Image, records: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """用统一库的预计算几何向量圈定候选，失败或不可用时返回 None 触发全量降级。

    延迟导入：``retrieval/multimodal`` 反向依赖本模块的 ``get_clip_model``/
    ``model_previews``，模块级导入会成环。
    """
    from machining_unified.retrieval.multimodal import coarse_visual_candidates

    try:
        candidates = coarse_visual_candidates(query_image, limit=_COARSE_RECALL_THRESHOLD)
    except (FileNotFoundError, ChromaError, ImportError, OSError, RuntimeError, ValueError):
        # 统一库缺失、损坏或 CLIP 不可加载——粗召回只是加速手段，
        # 失败时退化为全量精排，不能让图片检索本身跟着失败。
        logger.warning("统一库粗召回不可用，本次图片检索退化为全量精排", exc_info=True)
        return None
    by_id = {str(record["part_id"]): record for record in records}
    narrowed = [by_id[item["part_id"]] for item in candidates if item["part_id"] in by_id]
    # 统一库可能滞后于当前目录（新导入的零件还没重建 multimodal 索引）；
    # 圈不出候选时同样退化为全量，而不是返回空结果。
    return narrowed or None


def retrieve_by_image(query_image: Image.Image, records: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    """逐模型视觉比对：渲染候选图并与查询图片做 CLIP/指纹相似度排序。

    全量渲染+编码在几百条目录上是几十秒级，因此候选集优先来自统一库的粗召回
    （见 ``_coarse_recall``），只对圈定出的候选渲染、编码——这是最终排序的一致性
    与检索延迟之间的权衡：粗召回本身的相似度不用于排序，只用于圈定范围，
    真正的名次仍由这里对候选重新渲染、重新比对给出。
    """
    candidate_records = records
    if len(records) > _COARSE_RECALL_THRESHOLD:
        narrowed = _coarse_recall(query_image, records)
        if narrowed is not None:
            candidate_records = narrowed

    previews = [model_preview(record) for record in candidate_records]
    method = "CLIP geometry-render embedding"
    try:
        model = _clip_model()
        query = model.encode([query_image.convert("RGB")], normalize_embeddings=True)[0]
        vectors = model.encode(previews, normalize_embeddings=True)
    except (ImportError, OSError, RuntimeError, ValueError):
        # CLIP 完全不可用时，粗召回若圈过候选也已经不可信（它用的是同一个失效的
        # 模型），必须退回全量离线指纹比较。若候选本就是全量（未缩小或缩小失败），
        # 这里是 no-op，不会重复渲染——model_preview 本身也有网格缓存兜底。
        method = "offline geometry-render fingerprint"
        if candidate_records is not records:
            candidate_records = records
            previews = [model_preview(record) for record in candidate_records]
        query = _shape_fingerprint(query_image)
        vectors = [_shape_fingerprint(preview) for preview in previews]
    scored = [
        {"record": record, "score": round((float(query @ vector) + 1) / 2, 4), "preview": preview, "method": method}
        for record, vector, preview in zip(candidate_records, vectors, previews)
    ]
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
