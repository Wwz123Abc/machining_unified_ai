"""跨模态 part_id 关联清单的读取。"""

from __future__ import annotations

import json
from functools import lru_cache

from machining_unified.config.paths import PART_MANIFEST_PATH


@lru_cache(maxsize=1)
def load_part_manifest() -> list[dict]:
    if not PART_MANIFEST_PATH.exists():
        return []
    return json.loads(PART_MANIFEST_PATH.read_text(encoding="utf-8"))


def find_part_manifest(part_id: str) -> dict | None:
    return next((item for item in load_part_manifest() if item["part_id"] == part_id), None)
