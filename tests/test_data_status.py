"""数据状态面板的采集层测试：纯函数，不进 Streamlit，不写任何 data/ 文件。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.cad.retrieval import load_cad_catalog  # noqa: E402
from machining_unified.config.paths import CAD_CATALOG_PATH, VECTOR_STORES_DIR  # noqa: E402
from machining_unified.knowledge.manifests import load_decomposed_parts  # noqa: E402
from machining_unified.storage.data_status import catalog_rows, collect_data_status  # noqa: E402

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)


def group_1_collect_data_status() -> None:
    print("\n== 1. collect_data_status() ==")
    catalog = load_cad_catalog()
    status = collect_data_status()
    check("catalog_count 与 load_cad_catalog 一致", status["catalog_count"] == len(catalog), str(status["catalog_count"]))
    for store in status["stores"]:
        check(f"{store.key} 数量与目录一致", store.count == len(catalog), f"{store.count} vs {len(catalog)}")
    check("healthy 为 True", status["healthy"] is True, str(status["issues"]))
    check("issues 为空", status["issues"] == [], str(status["issues"]))


def group_2_catalog_rows() -> None:
    print("\n== 2. catalog_rows() ==")
    catalog = load_cad_catalog()
    rows = catalog_rows()
    check("行数与目录一致", len(rows) == len(catalog), f"{len(rows)} vs {len(catalog)}")
    missing = [row for row in rows if not row["part_id"] or not row["file_name"]]
    check("part_id/file_name 均非空", not missing, f"缺失 {len(missing)} 行")
    non_list_shared = [row for row in rows if not isinstance(row["shared"], list)]
    check("shared 字段均为 list", not non_list_shared, f"{len(non_list_shared)} 行非 list")


def group_3_ledger_summary() -> None:
    print("\n== 3. 台账摘要 ==")
    ledger = load_decomposed_parts()
    status = collect_data_status()
    if not ledger:
        check("台账不存在时 ledger.exists 为 False", status["ledger"] == {"exists": False})
        return
    check("ledger.exists 为 True", status["ledger"].get("exists") is True)
    check("parts 数与台账键数一致", status["ledger"].get("parts") == len(ledger), f"{status['ledger'].get('parts')} vs {len(ledger)}")


def group_4_no_write_side_effects() -> None:
    print("\n== 4. 无写入副作用 ==")
    watched = [CAD_CATALOG_PATH, *sorted(VECTOR_STORES_DIR.glob("*/chroma.sqlite3"))]
    before = {path: path.stat().st_mtime_ns for path in watched if path.exists()}
    collect_data_status()
    catalog_rows()
    after = {path: path.stat().st_mtime_ns for path in watched if path.exists()}
    check("采集前后所有关注文件 mtime 不变", before == after, str({str(k): (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}))


def main() -> int:
    group_1_collect_data_status()
    group_2_catalog_rows()
    group_3_ledger_summary()
    group_4_no_write_side_effects()

    print("\n" + "=" * 60)
    if _failures:
        print(f"失败 {len(_failures)} 项：{_failures}")
        return 1
    print("数据状态面板采集层验收全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
