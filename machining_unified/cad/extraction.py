"""STEP/CAD 特征提取适配器。

优先使用 OCP 读取真实几何；当 OCP 不可用或 STEP 无法解析时，保留可审计的
STEP 文本级摘要，不猜测尺寸。输出结构可通过 part_id 与其他模态关联。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# OCP 的具体符号在函数内惰性导入，以便未安装时仍能走文本摘要降级路径。
# 但 except 子句需要在模块级拿到异常类型，因此这里做一次受保护导入；
# 没有 OCP 时用占位类型，使 except 子句语法成立且永远不会命中。
try:  # noqa: SIM105
    from OCP.Standard import Standard_Failure
except ImportError:  # pragma: no cover - 仅在未安装 cadquery-ocp 的环境成立
    class Standard_Failure(Exception):  # type: ignore[no-redef]
        """OCP 缺失时的占位异常类型。"""


# 将 STEP 直接测得的事实与后续推导的工程候选分开保存。
# 本层绝不虚构缺失的工程图元数据。


# ISO 10303-21（STEP 物理文件）要求文件以 ISO-10303-21 标记开头。
# 实践中常见「二进制 CAD 文件被改成 .step 后缀」，此时 OCP 只会给出
# IFSelect_RetFail 这类无从下手的状态码，必须在更早的位置识别出来。
_PART21_MAGIC = b"ISO-10303-21"
_PART21_PROBE_BYTES = 512


def looks_like_part21_step(data: bytes) -> bool:
    """判断字节内容是否为 ISO-10303-21 文本 STEP。

    只看开头一小段：合法文件的标记必定在首行附近，
    而错误格式往往整体是二进制，读全文没有意义。
    """

    return _PART21_MAGIC in data[:_PART21_PROBE_BYTES]


def describe_step_format(data: bytes) -> str:
    """为非 Part 21 内容生成可读的诊断信息，供界面直接展示给用户。"""

    head = data[:16]
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    return f"文件头为 {printable!r}，不含 ISO-10303-21 标记"


def _count_subshapes(shape: Any, shape_type: Any) -> int:
    from OCP.TopExp import TopExp_Explorer

    explorer = TopExp_Explorer(shape, shape_type)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _extract_with_ocp(step_path: Path) -> dict[str, Any]:
    """用 OCP 读取 STEP 并提取包围盒、拓扑数量和基础曲面类型。"""

    from OCP.Bnd import Bnd_Box
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBndLib import BRepBndLib
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    # OneShape 将 B-Rep 根节点展平后用于测量；下方 XCAF 函数会单独保留
    # 装配文件的产品树事实。
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(step_path))
    if status != IFSelect_RetDone:
        raise ValueError(f"STEP 文件读取失败，状态：{status}")
    if reader.TransferRoots() == 0:
        raise ValueError("STEP 文件没有可转换的几何根节点")
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("STEP 文件转换后为空几何体")

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    bbox = {
        "x_min_mm": round(xmin, 4),
        "y_min_mm": round(ymin, 4),
        "z_min_mm": round(zmin, 4),
        "x_max_mm": round(xmax, 4),
        "y_max_mm": round(ymax, 4),
        "z_max_mm": round(zmax, 4),
        "length_x_mm": round(xmax - xmin, 4),
        "length_y_mm": round(ymax - ymin, 4),
        "length_z_mm": round(zmax - zmin, 4),
    }

    face_count = _count_subshapes(shape, TopAbs_FACE)
    surface_types = {"cylinder": 0, "plane": 0, "other": 0}
    cylindrical_radii_mm: list[float] = []
    cylindrical_interfaces: list[dict[str, float | str]] = []
    # 曲面方向可保留“内孔/外圆柱”的几何事实；它能作为接口证据，
    # 但不能单独证明两个零件已经形成装配配合。
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = explorer.Current()
        surface_type = BRepAdaptor_Surface(TopoDS.Face_s(face)).GetType()
        if surface_type == GeomAbs_Cylinder:
            surface_types["cylinder"] += 1
            radius = round(BRepAdaptor_Surface(TopoDS.Face_s(face)).Cylinder().Radius(), 4)
            cylindrical_radii_mm.append(radius)
            orientation = TopoDS.Face_s(face).Orientation()
            role = "outer" if orientation == TopAbs_FORWARD else "inner" if orientation == TopAbs_REVERSED else "unknown"
            cylindrical_interfaces.append({"radius_mm": radius, "role": role})
        elif surface_type == GeomAbs_Plane:
            surface_types["plane"] += 1
        else:
            surface_types["other"] += 1
        explorer.Next()

    return {
        "parser": "OCP",
        "units": "mm (STEP project convention)",
        "solid_count": _count_subshapes(shape, TopAbs_SOLID),
        "face_count": face_count,
        "edge_count": _count_subshapes(shape, TopAbs_EDGE),
        "vertex_count": _count_subshapes(shape, TopAbs_VERTEX),
        "bounding_box": bbox,
        "surface_types": surface_types,
        "cylindrical_radii_mm": sorted(set(cylindrical_radii_mm)),
        "cylindrical_interfaces": cylindrical_interfaces,
        "geometry_confidence": "high",
    }


def _extract_step_text_summary(step_path: Path) -> dict[str, Any]:
    """无 OCP 时读取 STEP 文本实体数量；不把文本数量当作几何尺寸。"""

    # 实体计数仅用于诊断：该降级路径始终为低置信度，不能视为实测几何。
    text = step_path.read_text(encoding="ascii", errors="ignore")
    entity_counts = {
        entity: len(re.findall(rf"\b{entity}\s*\(", text, flags=re.IGNORECASE))
        for entity in ("MANIFOLD_SOLID_BREP", "ADVANCED_FACE", "CIRCLE", "CYLINDRICAL_SURFACE", "PLANE")
    }
    return {"parser": "STEP-text-fallback", "units": None, "solid_count": entity_counts["MANIFOLD_SOLID_BREP"], "face_count": entity_counts["ADVANCED_FACE"], "edge_count": None, "vertex_count": None, "bounding_box": None, "surface_types": {"cylinder": entity_counts["CYLINDRICAL_SURFACE"], "plane": entity_counts["PLANE"], "other": None}, "cylindrical_radii_mm": [], "step_entity_counts": entity_counts, "geometry_confidence": "low", "warnings": ["OCP unavailable or parsing failed; this is a text-only STEP summary."]}


def _extract_xcaf_assembly_metadata(step_path: Path) -> dict[str, Any]:
    """Read STEP product structure without flattening it into a single B-Rep shape."""
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDF import TDF_LabelSequence
        from OCP.TDocStd import TDocStd_Document
        from OCP.XCAFApp import XCAFApp_Application
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool

        # B-Rep 提取会丢失层级；XCAF 提供自由形状与装配根数量，
        # 不会把展平后的几何体错误当成装配树。
        application = XCAFApp_Application.GetApplication_s()
        document = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
        application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), document)
        reader = STEPCAFControl_Reader()
        if not reader.ReadFile(str(step_path)) or not reader.Transfer(document):
            return {"available": False, "reason": "STEP XCAF transfer failed"}
        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        roots = TDF_LabelSequence()
        tool.GetFreeShapes(roots)
        assembly_roots = sum(1 for index in range(1, roots.Length() + 1) if XCAFDoc_ShapeTool.IsAssembly_s(roots.Value(index)))
        return {"available": True, "free_shape_count": roots.Length(), "assembly_root_count": assembly_roots}
    except (ImportError, OSError, ValueError, RuntimeError, Standard_Failure) as error:
        # 产品树读取失败不影响 B-Rep 几何事实，记录原因即可继续。
        logger.debug("STEP 产品树读取失败", extra={"source_file": str(step_path), "reason": str(error)})
        return {"available": False, "reason": str(error)}


def _infer_part_type(features: dict[str, Any], file_name: str = "") -> str | None:
    labels = {
        "shaft": "轴类候选",
        "sleeve": "套筒/衬套类候选",
        "plate": "板件/法兰类候选",
        "housing": "箱体/支架类候选",
        "complex": "复杂机械零件候选",
        "general": "通用机械零件候选",
    }
    return labels[classify_part_family(features, file_name)]


def _filename_family_hint(file_name: str) -> str | None:
    """仅在几何无法分类时使用的弱文件名提示。"""
    name = file_name.lower()
    if any(term in name for term in ("shaft", "axis")):
        return "shaft"
    if any(term in name for term in ("sleeve", "bushing")):
        return "sleeve"
    if any(term in name for term in ("housing", "bearing", "box", "bracket")):
        return "housing"
    return None


def classify_part_family(features: dict[str, Any], file_name: str = "") -> str:
    """优先按 STEP 几何分类；文件名只可作为几何不足时的弱提示。"""
    filename_hint = _filename_family_hint(file_name)
    bbox = features.get("bounding_box") or {}
    dimensions = sorted(
        value
        for value in (bbox.get("length_x_mm"), bbox.get("length_y_mm"), bbox.get("length_z_mm"))
        if value is not None
    )
    if len(dimensions) < 3 or min(dimensions) <= 0:
        return filename_hint or "general"
    radii = features.get("cylindrical_radii_mm", [])
    surfaces = features.get("surface_types", {})
    face_count = max(1, int(features.get("face_count") or 0))
    cylinder_ratio = float(surfaces.get("cylinder") or 0) / face_count
    plane_ratio = float(surfaces.get("plane") or 0) / face_count
    long_ratio = dimensions[-1] / dimensions[1]
    thin_ratio = dimensions[0] / dimensions[1]
    if long_ratio >= 2.4 and cylinder_ratio >= 0.15:
        return "shaft"
    if thin_ratio <= 0.35 and dimensions[1] / dimensions[-1] >= 0.45:
        return "plate"
    if cylinder_ratio >= 0.55 and len(radii) >= 2 and long_ratio <= 1.8:
        return "sleeve"
    if face_count >= 140 or (plane_ratio >= 0.55 and len(radii) >= 4):
        return "complex"
    if plane_ratio >= 0.45:
        return "housing"
    return filename_hint or "general"


def geometry_semantics(features: dict[str, Any], file_name: str = "") -> dict[str, Any]:
    """Convert numeric CAD facts into differentiated, embedding-friendly engineering terms."""
    bbox = features.get("bounding_box") or {}
    dims = sorted(float(bbox.get(key) or 0) for key in ("length_x_mm", "length_y_mm", "length_z_mm"))
    if not dims or min(dims) <= 0:
        return {"shape": "尺寸未提取", "complexity": "未知", "radius_profile": "未知"}
    long_ratio = dims[-1] / dims[1]
    thin_ratio = dims[0] / dims[1]
    if long_ratio >= 2.4:
        shape = "细长构件"
    elif thin_ratio <= 0.35:
        shape = "薄板或法兰状构件"
    else:
        shape = "紧凑块状构件"
    faces = int(features.get("face_count") or 0)
    complexity = "高复杂度多特征" if faces >= 150 else "中等复杂度" if faces >= 50 else "基础几何"
    radii = features.get("cylindrical_radii_mm", [])
    if not radii:
        radius_profile = "无可确认圆柱半径"
    elif len(radii) == 1:
        radius_profile = "单一圆柱尺度"
    else:
        radius_profile = f"{len(radii)} 级圆柱尺度，半径范围 {min(radii):.2f} 到 {max(radii):.2f} mm"
    return {
        "shape": shape,
        "complexity": complexity,
        "radius_profile": radius_profile,
        "aspect_ratio": round(long_ratio, 3),
        "thin_ratio": round(thin_ratio, 3),
        "family": classify_part_family(features, file_name),
    }


def _derive_machining_candidates(features: dict[str, Any]) -> list[str]:
    """把几何事实转换为需人工确认的加工特征候选。"""

    candidates: list[str] = []
    surfaces = features.get("surface_types", {})
    bbox = features.get("bounding_box") or {}
    dimensions = [bbox.get(key) for key in ("length_x_mm", "length_y_mm", "length_z_mm")]
    dimensions = [value for value in dimensions if value is not None]
    radii = features.get("cylindrical_radii_mm", [])
    if surfaces.get("cylinder", 0) >= 1:
        candidates.append("圆柱外圆/圆柱面")
    if len(radii) >= 2 and max(radii) / max(min(radii), 0.001) >= 1.2:
        candidates.append("同轴内外圆或阶梯特征候选")
    if len(dimensions) == 3 and min(dimensions) / max(dimensions) < 0.25:
        candidates.append("薄壁/薄板特征候选")
    if surfaces.get("plane", 0) >= 2:
        candidates.append("端面/基准平面")
    if not candidates:
        candidates.append("未识别出明确加工特征")
    return candidates


def textify_cad_features(record: dict[str, Any], *, include_identity: bool = True) -> str:
    """将 CAD JSON 转为可用于混合检索的短文本。

    ``include_identity=False`` 时省略 part_id。语义向量用于"以模型搜模型"，
    而上传的查询模型没有图号（part_id 恒为 QUERY）。把图号写进文本会让
    查询与文档产生固定偏移：实测 TEACH-CAD-001 的查询/文档余弦为 0.9589，
    仅把 part_id 对齐后升至 0.9955，该模型因此被挤出自身查询的候选集。
    按图号检索由 BM25 与企业问答的图号精确匹配承担，不依赖本文本。
    """

    features = record.get("features", {})
    bbox = features.get("bounding_box") or {}
    dims = [bbox.get(key) for key in ("length_x_mm", "length_y_mm", "length_z_mm")]
    dimensions = " × ".join(str(value) for value in dims if value is not None) or "未提取"
    candidates = "、".join(features.get("machining_feature_candidates", [])) or "未识别"
    design = record.get("design_metadata", {})
    design_text = "；".join(
        f"{key}={value}"
        for key, value in design.items()
        if value not in (None, [], "")
    )
    # 没有已确认属性时整句省略，不写"未标注"占位。
    # 该占位句出现在 21/24 的目录记录和每一次上传查询里，不携带任何信息，
    # 却让少数带真实属性的模型在向量空间中被系统性推远——实测导致这些模型
    # 在自身查询的语义候选集中被完全漏掉。
    design_clause = f"；设计属性={design_text}" if design_text else ""
    identity = f"part_id={record.get('part_id')}；" if include_identity else ""
    return (
        f"{identity}CAD/3D 模型；"
        f"外包络尺寸（mm）={dimensions}；"
        f"实体数={features.get('solid_count')}；面数={features.get('face_count')}；"
        f"圆柱面数={features.get('surface_types', {}).get('cylinder')}；"
        f"加工特征候选={candidates}{design_clause}。"
    )


def extract_step_features(
    step_path: str | Path,
    part_id: str,
    use_filename_hint: bool = True,
) -> dict[str, Any]:
    """提取 STEP 特征并返回统一的跨模态记录。"""

    path = Path(step_path).resolve()
    if path.suffix.lower() not in {".step", ".stp"}:
        raise ValueError("仅支持 .step 或 .stp 文件")
    if not path.exists():
        raise FileNotFoundError(path)

    # 优先使用完整 B-Rep 解析。单个异常文件不能中断目录构建；
    # 降级记录会保留警告与低置信度标记，便于审计。
    try:
        geometry = _extract_with_ocp(path)
    except ImportError:
        # OCP 缺失是部署问题而非数据问题：整个目录都会退化为低置信度文本摘要。
        logger.error("OCP 不可用，STEP 解析退化为文本摘要", extra={"source_file": str(path), "part_id": part_id})
        geometry = _extract_step_text_summary(path)
    except (OSError, ValueError, RuntimeError, Standard_Failure) as error:
        # 已枚举的失败模式：文件读不了、STEP 内容非法（_extract_with_ocp 抛 ValueError）、
        # OCCT 内核报错（Standard_Failure）。单条失败只影响该记录，
        # 记录里保留 warnings 供审计，同时写日志，避免整批导入时被正常输出淹没。
        # 此处刻意不捕获其余异常：那属于代码缺陷，应当中断构建而不是产出一条降级记录。
        logger.exception("OCP 几何解析失败，已退化为文本摘要", extra={"source_file": str(path), "part_id": part_id})
        geometry = _extract_step_text_summary(path)
        geometry.setdefault("warnings", []).append(f"OCP 几何解析失败：{error}")

    # 后续字段仅用于丰富检索，均标记为候选或语义描述；
    # 绝不当作已确认的材料、公差或工艺信息。
    geometry["machining_feature_candidates"] = _derive_machining_candidates(geometry)
    # 上传查询会关闭文件名提示，保证同一 STEP 改名后得到相同的几何分类与检索文本。
    family_file_name = path.name if use_filename_hint else ""
    geometry["geometry_semantics"] = geometry_semantics(geometry, family_file_name)
    geometry["assembly_structure"] = _extract_xcaf_assembly_metadata(path)
    record = {
        "part_id": part_id,
        "modality": "cad_3d",
        "source_file": str(path),
        "file_name": path.name,
        "file_format": path.suffix.lower().lstrip("."),
        "features": geometry,
        "part_type_candidate": _infer_part_type(geometry, family_file_name),
        "part_family": classify_part_family(geometry, family_file_name),
        "review_status": "pending_manual_review",
        "design_metadata": {
            "material": None,
            "surface_treatment": None,
            "roughness_ra": None,
            "precision_requirement": None,
            "heat_treatment": None,
            "hole_types": [],
            "keyways": [],
            "threads": [],
            "chamfers": [],
            "fillets": [],
            "assembly_relations": [],
        },
    }
    record["search_text"] = textify_cad_features(record)
    return record


def build_cad_context(cad_record: dict[str, Any]) -> str:
    """将增强版 CAD 特征压缩成工艺 RAG 可使用的受控上下文。"""

    if not cad_record:
        return ""
    semantics = cad_record.get("features", {}).get("geometry_semantics", {})
    return (
        f"\n[上传 CAD 模型：{cad_record['file_name']}，part_id={cad_record['part_id']}]\n"
        f"CAD 初步特征：{cad_record['features']}\n"
        f"CAD 检索文本：{cad_record.get('search_text', textify_cad_features(cad_record))}\n"
        f"零件类型候选：{cad_record.get('part_type_candidate')}\n"
        f"几何语义候选：{semantics}\n"
        "CAD 派生类别和加工特征均为候选，必须结合图纸与人工审核确认。\n"
    )
