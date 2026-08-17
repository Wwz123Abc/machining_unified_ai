"""外置检索权重配置的回归测试。

两个核心命题：

1. **保真**——随附的 ``data/config/retrieval_params.json`` 与代码内默认值等价，
   外置化不改变任何既有排序行为；
2. **真接线**——改动配置确实会改变打分结果，配置不是摆设。

外加加载器的防御性校验：拒绝未知字段、越界取值、非凸组合。

运行：

    .\\.venv\\Scripts\\python.exe tests\\test_retrieval_params.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.config import retrieval_params as rp  # noqa: E402
from machining_unified.config.paths import RETRIEVAL_PARAMS_PATH  # noqa: E402

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
    return condition


def _load_from(payload: dict | None) -> rp.RetrievalParams:
    """在临时文件上加载配置，不触碰项目内的真实配置。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "retrieval_params.json"
        if payload is not None:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        modified = path.stat().st_mtime_ns if path.exists() else 0
        return rp._load(str(path), modified)


def _expect_error(label: str, payload: dict, fragment: str) -> None:
    try:
        _load_from(payload)
    except rp.RetrievalParamsError as error:
        check(label, fragment in str(error), f"实际：{error}")
    except Exception as error:  # noqa: BLE001 - 用例要求必须是 RetrievalParamsError
        check(label, False, f"抛出了 {type(error).__name__} 而非 RetrievalParamsError：{error}")
    else:
        check(label, False, "未抛出异常")


def test_shipped_file_matches_code_defaults() -> None:
    print("\n== 随附配置与代码默认值等价 ==")
    if not check("配置文件存在", RETRIEVAL_PARAMS_PATH.is_file(), str(RETRIEVAL_PARAMS_PATH)):
        return
    shipped = json.loads(RETRIEVAL_PARAMS_PATH.read_text(encoding="utf-8"))
    from_file = asdict(_load_from(shipped))
    from_defaults = asdict(rp.RetrievalParams())
    check("外置化未改变任何权重", from_file == from_defaults,
          "" if from_file == from_defaults else f"差异：{from_file} != {from_defaults}")

    # 配置文件必须写全，否则调参者看不到可调项的存在。
    for section, cls in rp._SECTIONS.items():
        expected = set(asdict(cls()))
        actual = set(shipped.get(section, {}))
        check(f"配置节 {section} 字段完整", expected == actual, f"缺少 {sorted(expected - actual)}")


def test_partial_override() -> None:
    print("\n== 部分覆盖 ==")
    # hybrid 有两个独立的凸组（ensemble_* 与 fallback_*），只覆盖前一组，
    # 才能同时验证"同节内未写的另一组保持默认"——单组两字段的节做不到这一点，
    # 覆盖它必然要同时给出两个字段才能维持和为 1。
    params = _load_from({"hybrid": {"ensemble": 0.5, "ensemble_lexical": 0.3, "ensemble_graph": 0.2}})
    check("被覆盖的字段生效", params.hybrid.ensemble == 0.5)
    check("同节未写字段保持默认", params.hybrid.fallback_vector == 0.55)
    check("未提及的节保持默认", params.ensemble.lexical == 0.35)


def test_validation_rejects_bad_config() -> None:
    print("\n== 防御性校验 ==")
    _expect_error("拒绝未知配置节", {"nonexistent": {}}, "未知配置节")
    _expect_error("拒绝未知字段（拼写错误）", {"ensemble": {"lexicall": 0.65, "semantic": 0.35}}, "未知字段")
    _expect_error("拒绝越界取值", {"ensemble": {"lexical": 1.5, "semantic": 0.35}}, "0 到 1")
    _expect_error("拒绝负数", {"ensemble": {"lexical": -0.1, "semantic": 1.1}}, "0 到 1")
    _expect_error("拒绝非数字", {"ensemble": {"lexical": "high", "semantic": 0.3}}, "必须是数字")
    _expect_error("拒绝不和为 1 的凸组合", {"ensemble": {"lexical": 0.5, "semantic": 0.2}}, "必须和为 1")
    _expect_error("拒绝 hybrid 降级组不和为 1",
                  {"hybrid": {"fallback_vector": 0.5, "fallback_lexical": 0.2, "fallback_graph": 0.1}}, "必须和为 1")
    _expect_error("拒绝顶层非对象", {"ensemble": 0.5}, "必须是对象")

    # 几何相似度不是凸组合，其和不为 1 属于正常。
    try:
        params = _load_from({"geometry_similarity": {"material": 0.9}})
        check("几何相似度允许不和为 1", params.geometry_similarity.material == 0.9)
    except rp.RetrievalParamsError as error:
        check("几何相似度允许不和为 1", False, str(error))


def test_weights_actually_affect_scoring() -> None:
    """证明配置真的接进了打分，而不是只被读取。"""
    print("\n== 配置确实影响打分 ==")
    from machining_unified.cad.retrieval import load_cad_catalog, score_cad_similarity

    catalog = load_cad_catalog()
    if not check("CAD 目录至少两条记录", len(catalog) >= 2, f"{len(catalog)} 条"):
        return

    # 挑一对零件族相同的记录，使 part_family 权重确实参与打分。
    pair = None
    for index, left in enumerate(catalog):
        for right in catalog[index + 1:]:
            if left.get("part_family") and left.get("part_family") == right.get("part_family"):
                pair = (left, right)
                break
        if pair:
            break
    if not check("找到同族零件对", pair is not None):
        return

    baseline, _ = score_cad_similarity(*pair)

    original = rp.get_retrieval_params
    try:
        boosted = rp.RetrievalParams(
            geometry_similarity=rp.GeometrySimilarityWeights(part_family=0.9, dimensions=0.02)
        )
        rp.get_retrieval_params = lambda: boosted
        # cad.retrieval 直接引用了本模块的函数名，需同步替换。
        import machining_unified.cad.retrieval as cad_retrieval

        cad_retrieval.get_retrieval_params = lambda: boosted
        changed, _ = score_cad_similarity(*pair)
    finally:
        rp.get_retrieval_params = original
        import machining_unified.cad.retrieval as cad_retrieval

        cad_retrieval.get_retrieval_params = original

    check("调高零件族权重后分数发生变化", baseline != changed, f"{baseline} -> {changed}")
    restored, _ = score_cad_similarity(*pair)
    check("恢复配置后分数回到基线", restored == baseline, f"{restored} vs {baseline}")


def main() -> int:
    for test in (
        test_shipped_file_matches_code_defaults,
        test_partial_override,
        test_validation_rejects_bad_config,
        test_weights_actually_affect_scoring,
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
