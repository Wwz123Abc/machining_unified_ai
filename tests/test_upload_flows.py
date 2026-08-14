"""模型检索三条查询链路的端到端回归测试。

覆盖此前只验证到服务层、UI 装配线未被验证的部分：
``st.file_uploader`` -> ``save_step_upload`` -> ``session_state`` -> ``render_step_payload``。

使用 Streamlit 官方的 ``AppTest`` 在进程内执行真实 ``app.py``，
因此不需要浏览器、不需要 Playwright，也不引入任何新依赖。

运行：

    .\\.venv\\Scripts\\python.exe tests\\test_upload_flows.py

退出码 0 表示全部通过，非 0 表示存在失败用例。
"""

from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from machining_unified.cad.retrieval import load_cad_catalog  # noqa: E402
from machining_unified.cad.visual import model_preview  # noqa: E402


APP = ROOT / "app.py"
# 首次查询需要加载 BGE / CLIP 权重，实测冷启动约 10~30 秒；留足余量避免误判为失败。
TIMEOUT = 600.0

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
    return condition


def _is_frozen(obj: object) -> bool:
    """结果对象会进入 session_state 跨重跑复用，必须禁止就地改写。"""
    try:
        object.__setattr__  # noqa: B018 - 仅为可读性
        setattr(obj, "score", 0.0)
    except AttributeError:
        return True
    except Exception:
        return True
    return False


def _assert_no_exception(at: AppTest, stage: str) -> bool:
    """AppTest 会把脚本内未捕获的异常收集到 at.exception。"""
    messages = [element.value for element in at.exception]
    return check(f"{stage}：页面无未捕获异常", not messages, "; ".join(messages)[:200])


def _set_query_mode(at: AppTest, mode: str) -> AppTest:
    """查询方式是表单外的 segmented_control，设置后需要重跑才会切换表单内容。"""
    control = next(item for item in at.segmented_control if item.key == "model_query_mode")
    control.set_value(mode)
    return at.run(timeout=TIMEOUT)


def _submit(at: AppTest) -> AppTest:
    """点击“开始模型检索”表单提交按钮。"""
    submit = next(item for item in at.button if "开始模型检索" in (item.label or ""))
    submit.click()
    return at.run(timeout=TIMEOUT)


def _new_app() -> AppTest:
    """启动一个干净的 AppTest 会话。

    ``cad/viewer.py`` 在模块级用 ``st.components.v2.component`` 注册三维查看器，
    而每个 AppTest 会话有独立的组件注册表。模块被 import 缓存后不会重新注册，
    第二个用例起就会抛 ``Component 'step_mesh_viewer' is not registered``。
    真实部署是单进程单注册表，不存在该问题；这里清理模块缓存以复现干净的首次导入。
    副作用是嵌入模型的 lru_cache 一并失效，每个用例会重新加载权重。
    """
    for name in [n for n in sys.modules if n.startswith("machining_unified")]:
        del sys.modules[name]
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    return at.run(timeout=TIMEOUT)


def test_step_upload_flow() -> None:
    """STEP 上传 -> 几何/语义结果 -> 查询模型网格进入会话状态。"""
    print("\n== STEP 上传链路 ==")
    step_file = ROOT / "data/enterprise/cad_samples/TEACH-CAD-001_shaft_keyway.step"
    if not check("查询用 STEP 文件存在", step_file.is_file(), str(step_file)):
        return

    at = _new_app()
    _assert_no_exception(at, "初始渲染")
    at = _set_query_mode(at, "STEP 模型")

    uploader = next(item for item in at.file_uploader if item.key == "model_step_upload")
    uploader.upload(step_file.name, step_file.read_bytes(), "application/step")
    at = _submit(at)

    _assert_no_exception(at, "STEP 提交")
    state = at.session_state["model_search"]
    if not check("session_state.model_search 已写入", state is not None):
        return
    check("模式标记为 STEP 模型", state["mode"] == "STEP 模型", repr(state["mode"]))

    results = state["results"]
    check("几何相似结果非空", len(results.geometry) > 0, f"{len(results.geometry)} 条")
    check("语义召回结果非空", len(results.semantic) > 0, f"{len(results.semantic)} 条")
    check("查询记录 part_id 为 QUERY", results.query["part_id"] == "QUERY")
    check("几何解析器为 OCP", results.query["features"]["parser"] == "OCP")
    # DTO 契约：字段改名会在这里直接失败，而不是页面上静默少一块内容。
    first = results.geometry[0]
    check("几何结果为 GeometryHit 且字段可用",
          isinstance(first.part_id, str) and isinstance(first.score, float) and isinstance(first.reasons, tuple),
          f"{type(first).__name__}")
    check("几何结果不可变", _is_frozen(first))

    # 这是 W6 的核心：临时文件在 finally 中被删除，网格必须已经取出并随结果留存，
    # 否则重跑时 render_step_payload 会拿不到数据。
    mesh = state["query_mesh"]
    check("查询模型网格已随结果保存", isinstance(mesh, dict) and mesh.get("triangle_count", 0) > 0,
          f"triangle_count={mesh.get('triangle_count') if isinstance(mesh, dict) else mesh}")
    check("网格三角面数据完整", len(mesh["triangles"]) == mesh["triangle_count"])
    check("每个三角面为 9 个坐标", all(len(t) == 9 for t in mesh["triangles"][:20]))

    # 非提交重跑：结果必须由会话状态重建，且不得重新执行检索。
    at = at.run(timeout=TIMEOUT)
    _assert_no_exception(at, "STEP 非提交重跑")
    check("重跑后结果仍在会话状态中", at.session_state["model_search"] is not None)
    check("重跑后网格仍可用", at.session_state["model_search"]["query_mesh"]["triangle_count"] > 0)
    rendered = " ".join(element.value for element in at.markdown)
    # 查询模型现在是几何结果网格里的首个卡片，与候选同尺寸并排。
    check("重跑后仍渲染出查询模型卡片", "查询模型" in rendered)
    check("查询模型与几何候选同处一个结果区", "结构化几何相似结果" in rendered)


def test_image_upload_flow() -> None:
    """图片上传 -> 视觉逐模型比对 -> 查询图进入会话状态。"""
    print("\n== 图片上传链路 ==")
    catalog = load_cad_catalog()
    if not check("CAD 目录非空", len(catalog) > 0, f"{len(catalog)} 条"):
        return

    # 用目录中真实模型的渲染图作为查询图，避免引入外部素材。
    buffer = io.BytesIO()
    model_preview(catalog[0]).save(buffer, format="PNG")
    png_bytes = buffer.getvalue()
    check("生成的查询图为合法 PNG", png_bytes[:8] == b"\x89PNG\r\n\x1a\n", f"{len(png_bytes)} 字节")

    at = _new_app()
    at = _set_query_mode(at, "零件图片")

    uploader = next(item for item in at.file_uploader if item.key == "model_image_upload")
    uploader.upload("query.png", png_bytes, "image/png")
    at = _submit(at)

    _assert_no_exception(at, "图片提交")
    state = at.session_state["model_search"]
    if not check("session_state.model_search 已写入", state is not None):
        return
    check("模式标记为零件图片", state["mode"] == "零件图片", repr(state["mode"]))
    check("视觉结果非空", len(state["results"].visual) > 0, f"{len(state['results'].visual)} 条")
    check("查询图已随结果保存", state["query_image"] is not None)
    check("查询图尺寸可读", tuple(state["query_image"].size) > (0, 0), str(state["query_image"].size))

    at = at.run(timeout=TIMEOUT)
    _assert_no_exception(at, "图片非提交重跑")
    check("重跑后视觉结果仍在", bool(at.session_state["model_search"]["results"].visual))


def test_rejects_missing_upload() -> None:
    """未选择文件直接提交时必须给出明确提示，且不写入会话状态。"""
    print("\n== 空提交防御 ==")
    at = _new_app()
    at = _set_query_mode(at, "STEP 模型")
    at = _submit(at)

    _assert_no_exception(at, "空提交")
    errors = " ".join(element.value for element in at.error)
    check("提示需要先上传文件", "请先上传查询文件" in errors, repr(errors[:80]))
    check("未写入检索结果", at.session_state["model_search"] is None)


def test_mode_switch_hides_stale_results() -> None:
    """切换查询方式后不得展示上一种方式遗留的结果。"""
    print("\n== 查询方式切换隔离 ==")
    at = _new_app()
    at = _set_query_mode(at, "文字描述")

    text_area = next(item for item in at.text_area if item.key == "model_text_query")
    text_area.set_value("薄壁套筒，内外圆柱面，用于导向定位")
    at = _submit(at)
    _assert_no_exception(at, "文字提交")

    if not check("文字检索已写入会话状态", at.session_state["model_search"] is not None):
        return
    rendered = " ".join(element.value for element in at.markdown)
    check("已渲染中文工程语义结果", "中文工程语义结果" in rendered)

    at = _set_query_mode(at, "零件图片")
    _assert_no_exception(at, "切换到零件图片")
    rendered_after = " ".join(element.value for element in at.markdown)
    check("切换后不再展示文字检索结果", "中文工程语义结果" not in rendered_after)
    check("会话状态本身仍保留结果", at.session_state["model_search"] is not None)

    at = _set_query_mode(at, "文字描述")
    rendered_back = " ".join(element.value for element in at.markdown)
    check("切回后结果由会话状态重建", "中文工程语义结果" in rendered_back)


def main() -> int:
    tests = (
        test_step_upload_flow,
        test_image_upload_flow,
        test_rejects_missing_upload,
        test_mode_switch_hides_stale_results,
    )
    for test in tests:
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
