"""扫描 STEP/STP 文件，生成带 MD5 去重信息的 CAD 检索目录。"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.cad.extraction import (  # noqa: E402
    describe_step_format,
    extract_step_features,
    looks_like_part21_step,
    textify_cad_features,
)
from machining_unified.config.logging_setup import configure_logging  # noqa: E402
from machining_unified.config.paths import (  # noqa: E402
    CAD_CATALOG_PATH,
    CAD_DUPLICATES_PATH,
    CAD_SAMPLES_DIR,
)
from machining_unified.knowledge.manifests import resolve_design_metadata  # noqa: E402

logger = configure_logging()


SAMPLE_DIR = CAD_SAMPLES_DIR
OUTPUT = CAD_CATALOG_PATH
DUPLICATE_OUTPUT = CAD_DUPLICATES_PATH
STEP_SUFFIXES = {".step", ".stp"}


def file_md5(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """分块计算文件 MD5，避免大 STEP 文件一次性占用过多内存。"""
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path) -> str:
    """统一保存项目相对路径，保证目录可迁移且可追溯。"""
    return str(path.relative_to(ROOT)).replace("\\", "/")


def source_metadata(step_path: Path, sample_dir: Path) -> dict[str, str | None]:
    """提取不依赖模型几何的来源信息。"""
    relative_parts = step_path.relative_to(sample_dir).parts
    # 资料组取文件所在的直接目录。装配包按 assemblies/<装配号>/ 存放，
    # 若取首层目录，所有装配都会被并进一个名为 assemblies 的伪资料组，
    # group_component_count 也会跨装配累加。
    group_folder = relative_parts[-2] if len(relative_parts) > 1 else None
    part_id = step_path.stem.split("_", 1)[0]
    return {
        "part_id": part_id,
        "file_name": step_path.name,
        "source_file": relative_path(step_path),
        "source_type": "企业 CAD" if group_folder else "教学 CAD",
        "group_folder": group_folder,
    }


def _duplicate_source(candidate: dict[str, Any], primary: dict[str, Any]) -> dict[str, str]:
    """为未入索引的重复文件保留最小但完整的追溯信息。"""
    return {
        "part_id": str(candidate["part_id"]),
        "file_name": str(candidate["file_name"]),
        "source_file": str(candidate["source_file"]),
        "content_md5": str(candidate["content_md5"]),
        "duplicate_of_part_id": str(primary["part_id"]),
        "duplicate_of_source_file": str(primary["source_file"]),
    }


def build_catalog(sample_dir: Path = SAMPLE_DIR) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """构建可检索主记录和重复文件清单。

    MD5 相同表示文件字节完全一致：仅第一个按路径排序的文件作为主记录解析、入库；
    其余文件不会进入向量库，但会被记录在重复文件清单及主记录的 duplicate_sources 中。
    """
    candidates: list[dict[str, Any]] = []
    skipped: list[str] = []
    for step_path in sorted(path for path in sample_dir.rglob("*") if path.suffix.lower() in STEP_SUFFIXES):
        # 扩展名是 .step 不代表内容就是 STEP：二进制 CAD 文件被改名的情况很常见。
        # 放进来只会得到一条低置信度的文本摘要记录，污染检索结果，因此直接跳过。
        with step_path.open("rb") as handle:
            head = handle.read(512)
        if not looks_like_part21_step(head):
            skipped.append(relative_path(step_path))
            logger.warning(
                "跳过非 ISO-10303-21 文件",
                extra={"source_file": relative_path(step_path), "detail": describe_step_format(head)},
            )
            continue
        candidate = source_metadata(step_path, sample_dir)
        candidate["path"] = step_path
        candidate["content_md5"] = file_md5(step_path)
        candidates.append(candidate)

    if skipped:
        logger.warning(
            "本次扫描跳过了非 STEP 内容的文件",
            extra={"skipped_count": len(skipped), "skipped_files": skipped},
        )

    by_md5: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_md5[str(candidate["content_md5"])].append(candidate)

    primary_candidates: list[dict[str, Any]] = []
    duplicate_records: list[dict[str, str]] = []
    for same_content in by_md5.values():
        # 扫描顺序已按路径固定，主记录选择可复现，不依赖文件系统枚举顺序。
        primary = same_content[0]
        primary["duplicate_sources"] = [_duplicate_source(item, primary) for item in same_content[1:]]
        primary_candidates.append(primary)
        duplicate_records.extend(primary["duplicate_sources"])

    active_group_counts: dict[str | None, int] = defaultdict(int)
    raw_group_counts: dict[str | None, int] = defaultdict(int)
    for candidate in candidates:
        raw_group_counts[candidate["group_folder"]] += 1
    for candidate in primary_candidates:
        active_group_counts[candidate["group_folder"]] += 1

    records: list[dict[str, Any]] = []
    for candidate in primary_candidates:
        path = candidate["path"]
        group_folder = candidate["group_folder"]
        record = extract_step_features(path, part_id=str(candidate["part_id"]))
        # 从 BOM 与人工标注清单回填可确认的设计属性；没有来源的字段保持空值。
        # 必须在回填之后重算检索文本，否则这些属性进不了向量库和 BM25。
        record["design_metadata"].update(resolve_design_metadata(str(candidate["part_id"])))
        record["search_text"] = textify_cad_features(record)
        record.update(
            {
                "source_file": candidate["source_file"],
                "source_type": candidate["source_type"],
                "model_group_id": f"GROUP-{group_folder}" if group_folder else candidate["part_id"],
                "model_group_type": "企业资料组" if group_folder else "单模型",
                # 没有资料组的散装模型各自独立：它们共用 None 这个分组键，
                # 直接取计数会让每个单模型都显示成“资料组内有 N 个 STEP”。
                "group_component_count": active_group_counts[group_folder] if group_folder else 1,
                "group_source_file_count": raw_group_counts[group_folder] if group_folder else 1,
                "content_md5": candidate["content_md5"],
                "is_duplicate": False,
                "is_searchable": True,
                "duplicate_sources": candidate["duplicate_sources"],
            }
        )
        records.append(record)
    return records, duplicate_records


def validate_records(records: list[dict[str, Any]], duplicate_records: list[dict[str, str]]) -> None:
    """在写入前校验主记录唯一性与重复文件关联关系。"""
    part_ids = [record.get("part_id") for record in records]
    if len(part_ids) != len(set(part_ids)):
        raise ValueError("Catalog validation failed: duplicate primary part_id")
    hashes = [record.get("content_md5") for record in records]
    if len(hashes) != len(set(hashes)):
        raise ValueError("Catalog validation failed: duplicate MD5 remained in searchable records")

    primary_by_md5 = {str(record["content_md5"]): record for record in records}
    for record in records:
        source = ROOT / str(record["source_file"])
        if not source.exists():
            raise FileNotFoundError(f"Catalog validation failed: source file not found: {source}")
        if not record.get("features"):
            raise ValueError(f"Catalog validation failed: missing CAD features for {record.get('part_id')}")
        if record.get("is_duplicate") or not record.get("is_searchable"):
            raise ValueError(f"Catalog validation failed: primary record flags are invalid for {record.get('part_id')}")

    for duplicate in duplicate_records:
        source = ROOT / duplicate["source_file"]
        primary = primary_by_md5.get(duplicate["content_md5"])
        if not source.exists() or primary is None:
            raise ValueError(f"Catalog validation failed: invalid duplicate record {duplicate['source_file']}")
        if duplicate["duplicate_of_part_id"] != primary["part_id"]:
            raise ValueError(f"Catalog validation failed: wrong primary link for {duplicate['source_file']}")


def main() -> None:
    logger.info("开始构建 CAD 目录", extra={"sample_dir": str(SAMPLE_DIR)})
    records, duplicate_records = build_catalog()
    validate_records(records, duplicate_records)
    degraded = [r["part_id"] for r in records if r["features"].get("geometry_confidence") != "high"]
    backfilled = [r["part_id"] for r in records if any(v not in (None, [], "") for v in r["design_metadata"].values())]
    logger.info(
        "CAD 目录构建完成",
        extra={
            "searchable_models": len(records),
            "duplicate_files": len(duplicate_records),
            "degraded_geometry": degraded,
            "design_metadata_backfilled": backfilled,
        },
    )
    OUTPUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    DUPLICATE_OUTPUT.write_text(
        json.dumps(
            {
                "algorithm": "MD5",
                "searchable_model_count": len(records),
                "duplicate_file_count": len(duplicate_records),
                "duplicates": duplicate_records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"CAD 目录构建完成：{len(records)} 个可检索主模型，{len(duplicate_records)} 个重复文件。")


if __name__ == "__main__":
    main()
