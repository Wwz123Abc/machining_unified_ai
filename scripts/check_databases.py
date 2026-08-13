"""Read-only integrity check for catalogs, manifests, and Chroma stores."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import chromadb


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config.paths import (  # noqa: E402
    CAD_CATALOG_PATH,
    CHAT_HISTORY_PATH,
    MULTIMODAL_MANIFEST_PATH,
    PART_MANIFEST_PATH,
    PROJECT_ROOT,
)
from machining_unified.storage.database_registry import VECTOR_STORES  # noqa: E402


def load_json(path: Path, expected_type: type) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"missing JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, expected_type):
        raise TypeError(f"unexpected JSON type in {path}: {type(value).__name__}")
    return value


def check_catalog() -> tuple[int, list[str]]:
    records = load_json(CAD_CATALOG_PATH, list)
    errors: list[str] = []
    part_ids = [str(record.get("part_id", "")) for record in records]
    if not all(part_ids):
        errors.append("CAD catalog contains an empty part_id")
    if len(part_ids) != len(set(part_ids)):
        errors.append("CAD catalog contains duplicate part_id values")

    for record in records:
        source_file = record.get("source_file")
        if not source_file or not (PROJECT_ROOT / str(source_file)).is_file():
            errors.append(f"CAD source is missing: {source_file!r}")
    return len(records), errors


def check_manifests(catalog_count: int) -> list[str]:
    errors: list[str] = []
    part_manifest = load_json(PART_MANIFEST_PATH, list)
    manifest_ids = [str(item.get("part_id", "")) for item in part_manifest]
    if len(manifest_ids) != len(set(manifest_ids)):
        errors.append("part manifest contains duplicate part_id values")
    for item in part_manifest:
        cad_file = item.get("cad_file")
        if cad_file and not (PROJECT_ROOT / str(cad_file)).is_file():
            errors.append(f"part manifest CAD source is missing: {cad_file!r}")

    multimodal = load_json(MULTIMODAL_MANIFEST_PATH, dict)
    indexed = multimodal.get("searchable_model_count")
    if indexed is not None and int(indexed) != catalog_count:
        errors.append(
            f"multimodal manifest count ({indexed}) differs from CAD catalog ({catalog_count})"
        )

    load_json(CHAT_HISTORY_PATH, list)
    return errors


def check_vector_stores(catalog_count: int) -> tuple[dict[str, int], list[str]]:
    counts: dict[str, int] = {}
    errors: list[str] = []
    for spec in VECTOR_STORES:
        if not spec.directory.is_dir():
            errors.append(f"vector store directory is missing: {spec.directory}")
            continue
        try:
            client = chromadb.PersistentClient(path=str(spec.directory))
            collection = client.get_collection(spec.collection)
            count = collection.count()
            counts[spec.key] = count
            if count <= 0:
                errors.append(f"vector collection is empty: {spec.collection}")
            if spec.key in {"cad_semantic", "multimodal"} and count != catalog_count:
                errors.append(
                    f"{spec.collection} count ({count}) differs from CAD catalog ({catalog_count})"
                )
        except Exception as error:  # Keep checking the other independent stores.
            errors.append(f"cannot open {spec.collection}: {error}")
    return counts, errors


def main() -> int:
    catalog_count, errors = check_catalog()
    errors.extend(check_manifests(catalog_count))
    counts, vector_errors = check_vector_stores(catalog_count)
    errors.extend(vector_errors)

    print(f"CAD catalog: {catalog_count}")
    for spec in VECTOR_STORES:
        count = counts.get(spec.key)
        print(f"{spec.key}: {count if count is not None else 'ERROR'}")

    if errors:
        print("\nIntegrity check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("\nDatabase integrity check: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
