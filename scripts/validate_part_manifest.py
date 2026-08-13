"""验证 part_id 跨模态关联清单。"""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config.paths import CAD_CATALOG_PATH, PART_MANIFEST_PATH  # noqa: E402

manifest = json.loads(PART_MANIFEST_PATH.read_text(encoding="utf-8"))
catalog_ids = {str(item["part_id"]) for item in json.loads(CAD_CATALOG_PATH.read_text(encoding="utf-8"))}
ids = [item["part_id"] for item in manifest]
assert len(ids) == len(set(ids)), "part_id 重复"
for item in manifest:
    cad_path = ROOT / item["cad_file"]
    assert cad_path.exists(), f"CAD 文件不存在：{cad_path}"
    # 清单条目必须对应 CAD 目录中的可检索主记录，否则标注的材料无法回填。
    assert item["part_id"] in catalog_ids, f"CAD 目录中没有该 part_id：{item['part_id']}"
    for drawing in item.get("drawing_files", []):
        assert (ROOT / drawing).exists(), f"图纸文件不存在：{drawing}"

print(f"part_id 跨模态清单验证通过：{len(manifest)}/{len(manifest)}")
