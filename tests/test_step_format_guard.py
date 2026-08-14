"""非 STEP 文件的识别与降级行为测试。

背景：实践中常见把二进制 CAD 文件改成 .step 后缀。此前这种文件会让
底层解析器返回 IFSelect_RetFail，界面只显示"无法读取 STEP 网格"，
既看不出原因，还会连带把已经成功的检索结果一并丢弃。

运行：

    .\\.venv\\Scripts\\python.exe tests\\test_step_format_guard.py
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.cad.extraction import (  # noqa: E402
    describe_step_format,
    extract_step_features,
    looks_like_part21_step,
)
from machining_unified.services.model_search import save_step_upload  # noqa: E402

_failures: list[str] = []

# 实际遇到的伪 STEP 文件头：二进制内容被冠以 .STEP 扩展名。
FAKE_STEP = b"%TSD-Header-###%" + bytes(range(256)) * 4
REAL_STEP = ROOT / "data/enterprise/cad_samples/TEACH-CAD-001_shaft_keyway.step"


class _Upload:
    """最小化模拟 Streamlit 的 UploadedFile。"""

    def __init__(self, name: str, payload: bytes) -> None:
        self.name = name
        self._payload = payload

    def getvalue(self) -> bytes:
        return self._payload


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
    return condition


def test_format_detection() -> None:
    print("\n== Part 21 识别 ==")
    check("真实 STEP 被识别", looks_like_part21_step(REAL_STEP.read_bytes()[:512]))
    check("伪 STEP 被拒绝", not looks_like_part21_step(FAKE_STEP))
    check("空内容被拒绝", not looks_like_part21_step(b""))
    # 标记出现在很靠后的位置不算合法：Part 21 要求它在文件开头。
    check("标记出现在 1KB 之后不算合法", not looks_like_part21_step(b"x" * 1024 + b"ISO-10303-21"))

    description = describe_step_format(FAKE_STEP)
    check("诊断信息包含真实文件头", "%TSD-Header-###" in description, description)


def test_upload_rejected_with_actionable_message() -> None:
    print("\n== 上传入口拦截 ==")
    try:
        save_step_upload(_Upload("IMU180-22G-000.STEP", FAKE_STEP))
    except ValueError as error:
        message = str(error)
        check("抛出 ValueError", True)
        check("说明不是 ISO-10303-21", "ISO-10303-21" in message, message[:60])
        check("给出可执行的下一步", "重新导出" in message)
        check("附带文件头诊断", "TSD" in message)
    else:
        check("抛出 ValueError", False, "伪 STEP 被接受了")

    # 合法文件必须照常通过，守卫不能误伤。
    path = None
    try:
        path = save_step_upload(_Upload("TEACH-CAD-001.step", REAL_STEP.read_bytes()))
        check("真实 STEP 正常通过", path.is_file(), str(path))
    except Exception as error:  # noqa: BLE001
        check("真实 STEP 正常通过", False, f"{type(error).__name__}: {error}")
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def test_extraction_degrades_instead_of_crashing() -> None:
    """即使伪 STEP 绕过入口直接进到提取层，也必须降级而不是抛异常。"""
    print("\n== 提取层降级 ==")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake.step"
        fake.write_bytes(FAKE_STEP)
        try:
            record = extract_step_features(fake, part_id="QUERY")
        except Exception as error:  # noqa: BLE001
            check("提取未抛异常", False, f"{type(error).__name__}: {error}")
            return
        check("提取未抛异常", True)
        features = record["features"]
        check("标记为低置信度", features.get("geometry_confidence") == "low", str(features.get("geometry_confidence")))
        check("使用了文本降级解析器", features.get("parser") == "STEP-text-fallback", str(features.get("parser")))
        check("保留了可审计的警告", bool(features.get("warnings")), str(features.get("warnings"))[:80])


def main() -> int:
    for test in (
        test_format_detection,
        test_upload_rejected_with_actionable_message,
        test_extraction_degrades_instead_of_crashing,
    ):
        try:
            test()
        except Exception:
            _failures.append(f"{test.__name__} 抛出异常")
            print(f"  [FAIL] {test.__name__} 抛出异常")
            traceback.print_exc()

    print("\n" + "=" * 60)
    if _failures:
        print(f"失败 {len(_failures)} 项：")
        for item in _failures:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
