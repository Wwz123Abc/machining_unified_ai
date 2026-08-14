"""跨模态关联清单的读取，以及可确认设计属性的汇总。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from machining_unified.config.paths import (
    ASSEMBLY_PACKAGES_DIR,
    DECOMPOSED_PARTS_PATH,
    PART_MANIFEST_PATH,
)
from machining_unified.knowledge.part_ids import normalized_part_id


# BOM 用这些占位符表示“本栏不适用”，不能当作已确认的材料或表面处理。
_BOM_PLACEHOLDERS = {"NA", "N/A", "-", "无"}


@lru_cache(maxsize=1)
def load_part_manifest() -> list[dict]:
    if not PART_MANIFEST_PATH.exists():
        return []
    return json.loads(PART_MANIFEST_PATH.read_text(encoding="utf-8"))


def find_part_manifest(part_id: str) -> dict | None:
    return next((item for item in load_part_manifest() if item["part_id"] == part_id), None)


@lru_cache(maxsize=1)
def load_assembly_manifests() -> list[dict[str, Any]]:
    """读取已导入的装配资料包清单。

    装配资料包是可选的：没有导入资料包属于正常的单零件部署状态，不能因此停止检索。
    按进程缓存；导入新资料包后需要重启应用才能生效。
    """
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(ASSEMBLY_PACKAGES_DIR.glob("*/assembly_manifest.json"))
    ]


def assembly_manifest_for(part_id: str) -> dict[str, Any] | None:
    return next(
        (manifest for manifest in load_assembly_manifests() if manifest["assembly_id"] == part_id),
        None,
    )


@lru_cache(maxsize=1)
def load_decomposed_parts() -> dict[str, dict[str, Any]]:
    """读取装配拆解台账，按 part_id 索引。

    台账里的装配归属来自 XCAF 产品树遍历，是**事实**而非几何推断——
    与 BOM 同级，因此可以作为知识图谱的事实边。
    没有拆解过资料属于正常状态（只有单零件或成套资料包的部署），返回空字典。
    """

    if not DECOMPOSED_PARTS_PATH.exists():
        return {}
    payload = json.loads(DECOMPOSED_PARTS_PATH.read_text(encoding="utf-8"))
    return {str(item["part_id"]): item for item in payload.get("parts", [])}


def assemblies_using_part(part_id: str) -> list[str]:
    """该零件出现在哪些装配里（拆出它的那个，加上共用它的其它装配）。"""

    entry = load_decomposed_parts().get(str(part_id))
    if not entry:
        return []
    origin = str(entry.get("origin_assembly", ""))
    shared = [str(name) for name in entry.get("also_used_in", [])]
    return [name for name in dict.fromkeys([origin, *shared]) if name]


def _usable(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.upper() not in _BOM_PLACEHOLDERS


def resolve_design_metadata(part_id: str) -> dict[str, Any]:
    """汇总已导入资料中可确认的设计属性，供 CAD 目录回填。

    只写入资料明确记载的字段；没有来源的字段保持缺省空值，绝不由几何或文件名猜测。
    人工标注的跨模态清单优先级低于 BOM，因为 BOM 是企业系统导出的原始记录。
    """
    resolved: dict[str, Any] = {}

    manifest = find_part_manifest(part_id)
    if manifest and _usable(manifest.get("material")):
        resolved["material"] = str(manifest["material"]).strip()

    normalized = normalized_part_id(part_id)
    for assembly in load_assembly_manifests():
        for item in assembly.get("bom_items", []):
            if str(item.get("normalized_part_id") or "") != normalized:
                continue
            if _usable(item.get("material")):
                resolved["material"] = str(item["material"]).strip()
            if _usable(item.get("surface_treatment")):
                resolved["surface_treatment"] = str(item["surface_treatment"]).strip()
    return resolved
