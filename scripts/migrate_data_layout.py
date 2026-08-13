"""把融合初版的平铺数据目录迁移到分层结构；可安全重复执行。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

MOVES = {
    "cad_models.json": "catalogs/cad_models.json",
    "cad_duplicates.json": "catalogs/cad_duplicates.json",
    "part_manifest.json": "catalogs/part_manifest.json",
    "unified_multimodal_manifest.json": "catalogs/unified_multimodal_manifest.json",
    "equipment.json": "knowledge/equipment.json",
    "tools.json": "knowledge/tools.json",
    "process_cases.json": "knowledge/process_cases.json",
    "process_extensions.json": "knowledge/process_extensions.json",
    "process_rules.json": "knowledge/process_rules.json",
    "public_source_catalog.md": "knowledge/public_source_catalog.md",
    "source_documents": "knowledge/source_documents",
    "templates": "knowledge/templates",
    "cad_samples": "enterprise/cad_samples",
    "assembly_packages": "enterprise/assembly_packages",
    "chat_history.json": "runtime/chat_history.json",
    "tessdata": "models/tessdata",
    "chroma_db": "vector_stores/process",
    "chroma_cad_db": "vector_stores/cad_semantic",
    "chroma_enterprise_kb": "vector_stores/enterprise",
    "chroma_unified_3d_db": "vector_stores/multimodal",
}

PATH_REPLACEMENTS = {
    "data/cad_samples": "data/enterprise/cad_samples",
    "data/assembly_packages": "data/enterprise/assembly_packages",
}


def _replace_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _replace_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_strings(item) for item in value]
    if isinstance(value, str):
        updated = value.replace("\\", "/")
        for old, new in PATH_REPLACEMENTS.items():
            updated = updated.replace(old, new)
        return updated
    return value


def _rewrite_json(path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    updated = _replace_strings(value)
    if updated == value:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    resolved_root = ROOT.resolve()
    resolved_data = DATA.resolve()
    if resolved_data.parent != resolved_root:
        raise RuntimeError(f"拒绝迁移项目外目录：{resolved_data}")

    for source_name, target_name in MOVES.items():
        source = DATA / source_name
        target = DATA / target_name
        if not source.exists():
            if target.exists():
                continue
            raise FileNotFoundError(f"源和目标均不存在：{source} -> {target}")
        if target.exists():
            raise FileExistsError(f"目标已存在，拒绝覆盖：{target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        print(f"moved: {source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")

    for path in sorted(DATA.rglob("*.json")):
        _rewrite_json(path)

    print("data layout migration: OK")


if __name__ == "__main__":
    main()
