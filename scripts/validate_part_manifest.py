"""验证 part_id 跨模态关联清单。"""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config.paths import PART_MANIFEST_PATH, PROCESS_CASES_PATH  # noqa: E402

manifest = json.loads(PART_MANIFEST_PATH.read_text(encoding="utf-8"))
cases = {item["case_id"] for item in json.loads(PROCESS_CASES_PATH.read_text(encoding="utf-8"))}
ids = [item["part_id"] for item in manifest]
assert len(ids) == len(set(ids)), "part_id 重复"
for item in manifest:
    assert item.get("text_case_ids"), f"缺少文字案例：{item['part_id']}"
    assert set(item["text_case_ids"]) <= cases, f"文字案例不存在：{item['part_id']}"
    cad_path = ROOT / item["cad_file"]
    assert cad_path.exists(), f"CAD 文件不存在：{cad_path}"

print(f"part_id 跨模态清单验证通过：{len(manifest)}/{len(manifest)}")
