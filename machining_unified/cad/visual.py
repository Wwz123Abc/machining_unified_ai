"""Geometry-derived CAD previews and visual retrieval."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from machining_unified.config.paths import PROJECT_ROOT

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


@lru_cache(maxsize=128)
def _mesh_triangles(source_file: str) -> np.ndarray:
    """读取一次 STEP 三角网格，供缩略图和多视角统一嵌入共同复用。"""
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
    return np.stack(triangles)


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
    """返回八个真实网格视角，用于将 STEP 几何投影到图文共享空间。"""
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


def retrieve_by_image(query_image: Image.Image, records: list[dict[str, Any]], top_k: int = 5) -> list[dict[str, Any]]:
    # 优先使用 CLIP 视觉嵌入；不可用时对同一份几何渲染图使用离线指纹，
    # 保证无网络环境下图像模态仍然可用。
    previews = [model_preview(record) for record in records]
    method = "CLIP geometry-render embedding"
    try:
        model = _clip_model()
        query = model.encode([query_image.convert("RGB")], normalize_embeddings=True)[0]
        vectors = model.encode(previews, normalize_embeddings=True)
    except (ImportError, OSError, RuntimeError, ValueError):
        method = "offline geometry-render fingerprint"
        query = _shape_fingerprint(query_image)
        vectors = [_shape_fingerprint(preview) for preview in previews]
    scored = [{"record": record, "score": round((float(query @ vector) + 1) / 2, 4), "preview": preview, "method": method} for record, vector, preview in zip(records, vectors, previews)]
    return sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
