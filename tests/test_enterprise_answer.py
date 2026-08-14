"""企业资料问答的证据契约测试。

全部用 ``generate=False`` 或问候语分支，不触发 DeepSeek 调用，
因此不消耗额度、不依赖网络。

运行：

    .\\.venv\\Scripts\\python.exe tests\\test_enterprise_answer.py
"""

from __future__ import annotations

import dataclasses
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.dto import EnterpriseAnswer, EnterpriseEvidence  # noqa: E402
from machining_unified.knowledge.enterprise import (  # noqa: E402
    answer_enterprise_question,
    retrieve_enterprise_knowledge,
)

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
    return condition


def test_evidence_contract() -> None:
    print("\n== 证据对象契约 ==")
    evidence = retrieve_enterprise_knowledge("DTXT806-300-012 的材料和表面处理是什么？", top_k=3)
    if not check("返回非空证据", len(evidence) > 0, f"{len(evidence)} 条"):
        return
    check("返回类型为 tuple（不可变）", isinstance(evidence, tuple))
    check("每条都是 EnterpriseEvidence", all(isinstance(item, EnterpriseEvidence) for item in evidence))

    first = evidence[0]
    check("引用编号按排名从 S1 开始", first.citation == "S1", first.citation)
    check("引用编号连续", [item.citation for item in evidence] == [f"S{i}" for i in range(1, len(evidence) + 1)])

    # 证据会随聊天记录留在 session_state 中跨重跑复用，必须禁止就地改写。
    try:
        object.__setattr__  # noqa: B018
        first.citation = "S99"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        check("证据对象不可变", True)
    except Exception as error:  # noqa: BLE001
        check("证据对象不可变", False, f"抛出 {type(error).__name__}")
    else:
        check("证据对象不可变", False, "允许了就地改写")

    for name in ("source_id", "title", "source_kind", "source_file"):
        value = getattr(first, name)
        check(f"派生属性 {name} 可读且非空", isinstance(value, str) and bool(value), repr(value)[:60])


def test_identifier_match_priority() -> None:
    print("\n== 图号精确匹配优先 ==")
    evidence = retrieve_enterprise_knowledge("DTXT806-300-012 的材料和表面处理是什么？", top_k=5)
    if not check("返回非空证据", len(evidence) > 0):
        return
    matched = [item.identifier_match for item in evidence]
    check("至少命中一条精确图号", any(matched), str(matched))
    # 精确命中必须排在所有非命中之前，与 retrieve_enterprise_knowledge 的排序约定一致。
    check("精确命中全部排在前面", matched == sorted(matched, reverse=True), str(matched))


def test_answer_without_model() -> None:
    print("\n== 无模型生成时返回本地证据摘要 ==")
    result = answer_enterprise_question("630DTXT806-300-000 装配包含哪些 BOM 零件？", top_k=3, generate=False)
    check("返回类型为 EnterpriseAnswer", isinstance(result, EnterpriseAnswer))
    check("标记为未生成", result.generated is False)
    check("回答非空", bool(result.answer.strip()))
    check("证据非空", len(result.evidence) > 0, f"{len(result.evidence)} 条")
    check("摘要列出了来源编号", all(item.source_id in result.answer for item in result.evidence))


def test_small_talk_returns_no_evidence() -> None:
    print("\n== 问候语不做无依据召回 ==")
    result = answer_enterprise_question("你好", top_k=3, generate=False)
    check("返回类型为 EnterpriseAnswer", isinstance(result, EnterpriseAnswer))
    check("证据为空", result.evidence == ())
    check("标记为未生成", result.generated is False)
    check("说明了知识库范围", "企业机械知识库" in result.answer)


def main() -> int:
    for test in (
        test_evidence_contract,
        test_identifier_match_priority,
        test_answer_without_model,
        test_small_talk_returns_no_evidence,
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
