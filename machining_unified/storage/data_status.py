"""数据状态与目录浏览的只读采集。不写任何 data/ 文件。"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb

from machining_unified.cad.retrieval import load_cad_catalog
from machining_unified.config.paths import (
    CAD_CATALOG_PATH,
    DECOMPOSED_PARTS_PATH,
    MULTIMODAL_MANIFEST_PATH,
)
from machining_unified.knowledge.manifests import assemblies_using_part, load_decomposed_parts
from machining_unified.storage.database_registry import VECTOR_STORES

FAMILY_LABELS = {
    "shaft": "轴类",
    "sleeve": "套筒",
    "plate": "板件",
    "housing": "箱体",
    "complex": "复杂",
    "general": "通用",
}


@dataclass(frozen=True)
class StoreStatus:
    key: str
    purpose: str
    count: int | None  # None = 打开失败
    mtime: str | None
    error: str | None = None


def _fmt(path: Path) -> str:
    if not path.exists():
        return "缺失"
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _store_status(spec) -> StoreStatus:
    # 与 scripts/check_databases.py 相同的只读打开方式；任何失败都降级为
    # "无法打开"状态，不能让面板本身的探测把整页拖崩。
    try:
        client = chromadb.PersistentClient(path=str(spec.directory))
        count = client.get_collection(spec.collection).count()
        return StoreStatus(spec.key, spec.purpose, count, _fmt(spec.directory / "chroma.sqlite3"))
    except Exception as error:  # noqa: BLE001 - 面板只读探测，向用户呈现失败原因而非让页面崩溃
        return StoreStatus(spec.key, spec.purpose, None, None, str(error))


def _ledger_summary() -> dict[str, Any]:
    ledger = load_decomposed_parts()
    if not ledger:
        return {"exists": False}
    shared = sum(1 for item in ledger.values() if item.get("also_used_in"))
    return {
        "exists": True,
        "parts": len(ledger),
        "shared": shared,
        "mtime": _fmt(DECOMPOSED_PARTS_PATH),
        # 拆解规则版本号目前没有随台账落盘（CLAUDE.md 的"必须随台账落盘"是二期
        # 待补的写入期改动），如实显示"未记录"而不是编造一个假值。
        "rule_version": None,
    }


def _multimodal_summary() -> dict[str, Any]:
    if not MULTIMODAL_MANIFEST_PATH.exists():
        return {"exists": False}
    manifest = json.loads(MULTIMODAL_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        "exists": True,
        "status": manifest.get("status"),
        "model": manifest.get("model"),
        "mtime": _fmt(MULTIMODAL_MANIFEST_PATH),
    }


def collect_data_status() -> dict[str, Any]:
    """目录、两库、台账、构建时间、一致性。每次调用约几十毫秒（Chroma 只读 count）。"""

    catalog = load_cad_catalog()
    stores = [_store_status(spec) for spec in VECTOR_STORES]
    status: dict[str, Any] = {
        "catalog_count": len(catalog),
        "catalog_mtime": _fmt(CAD_CATALOG_PATH),
        "attributed_count": sum(
            1
            for record in catalog
            if any(value not in (None, "", []) for value in record.get("design_metadata", {}).values())
        ),
        "stores": stores,
        "ledger": _ledger_summary(),
        "multimodal_manifest": _multimodal_summary(),
        "healthy": True,
        "issues": [],
    }
    # 一行数量对齐，不是完整一致性核对——那仍以 scripts/check_databases.py 为准。
    for store in stores:
        if store.count is None:
            status["healthy"] = False
            status["issues"].append(f"{store.key} 无法打开：{store.error}")
        elif store.count != len(catalog):
            status["healthy"] = False
            status["issues"].append(f"{store.key} 数量 {store.count} ≠ 目录 {len(catalog)}")
    return status


def catalog_rows() -> list[dict[str, Any]]:
    """供目录浏览表格：一行一个模型，字段全部来自目录 JSON + 台账（只读）。"""

    rows = []
    for record in load_cad_catalog():
        features = record.get("features", {})
        bbox = features.get("bounding_box") or {}
        dims = " × ".join(
            str(bbox.get(key))
            for key in ("length_x_mm", "length_y_mm", "length_z_mm")
            if bbox.get(key) is not None
        )
        part_id = str(record.get("part_id", ""))
        rows.append(
            {
                "part_id": part_id,
                "file_name": record.get("file_name", ""),
                "family": FAMILY_LABELS.get(record.get("part_family"), record.get("part_family", "")),
                "source_type": record.get("source_type", ""),
                "group": str(record.get("model_group_id", "")),
                "dims": dims or "未提取",
                "faces": features.get("face_count"),
                "has_design": any(
                    value not in (None, "", []) for value in record.get("design_metadata", {}).values()
                ),
                "source_file": record.get("source_file", ""),
                "shared": assemblies_using_part(part_id),
            }
        )
    return rows
