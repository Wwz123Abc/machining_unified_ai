"""跨模态 part_id 清单读取与 RAG 上下文构建。"""

from __future__ import annotations

import json
from functools import lru_cache

from machining_unified.config.paths import PART_MANIFEST_PATH

@lru_cache(maxsize=1)
def load_part_manifest() -> list[dict]:
    return json.loads(PART_MANIFEST_PATH.read_text(encoding="utf-8"))


def find_part_manifest(part_id: str) -> dict | None:
    return next((item for item in load_part_manifest() if item["part_id"] == part_id), None)


def build_part_manifest_context(part_id: str) -> str:
    """将已审核/待审核的跨模态关联清单转成 RAG 上下文。"""

    record = find_part_manifest(part_id)
    if not record:
        return ""
    return (
        f"\n[跨模态零件清单：part_id={part_id}]\n"
        f"材料：{record.get('material')}；零件类型：{record.get('part_type')}；"
        f"关联文字案例：{', '.join(record.get('text_case_ids', [])) or '无'}；"
        f"关联图纸：{', '.join(record.get('drawing_files', [])) or '无'}；"
        f"关联 CAD：{record.get('cad_file') or '无'}。\n"
        "以上只是资料关联索引，具体尺寸和工艺仍以各模态原文及人工审核为准。\n"
    )
