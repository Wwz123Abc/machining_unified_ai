"""STEP 语义分支的召回质量与几何重排回归测试。

背景：裸 BGE top-k 在本库上几乎无区分度——全部候选压在 0.95~0.97 的窄带内，
top-3 分数极差均值仅 0.005，自检索命中率只有 37.5%，且存在单一"吸引子"模型
垄断半数查询榜首。修复办法不是改索引文本，而是把语义召回降级为候选集、
由可解释几何加权分决定名次。

本测试锁住修复后的行为，防止回退。

运行：

    .\\.venv\\Scripts\\python.exe tests\\test_semantic_rerank.py
"""

from __future__ import annotations

import sys
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.cad.retrieval import load_cad_catalog, score_cad_similarity  # noqa: E402
from machining_unified.retrieval.cad_rag import (  # noqa: E402
    get_vector_store,
    semantic_document_text,
)
from machining_unified.services.model_search import (  # noqa: E402
    SEMANTIC_CANDIDATE_FACTOR,
    _rerank_semantic_by_geometry,
    _semantic_hits,
)

_failures: list[str] = []
TOP_K = 3


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _failures.append(label)
    return condition


def test_query_document_symmetry() -> None:
    print("\n== 查询与文档文本同构 ==")
    record = load_cad_catalog()[0]
    text = semantic_document_text(record)
    check("索引文本非空", bool(text.strip()))
    check("含逐模型区分量：长径比", "长径比" in text)
    check("含逐模型区分量：边与顶点计数", "边 " in text and "顶点 " in text)
    # 族级叙述保留：它是文字检索匹配功能词的唯一信号，
    # 由几何重排而非删文本来消除其对模型检索的干扰。
    check("保留族级功能叙述", "功能候选" in text)


def test_candidate_set_covers_self() -> None:
    """重排只能在候选集内纠正名次，自身必须先进得来。"""
    print("\n== 候选集覆盖率 ==")
    catalog = load_cad_catalog()
    store = get_vector_store()
    covered = 0
    for record in catalog:
        pairs = store.similarity_search_with_score(
            semantic_document_text(record), k=TOP_K * SEMANTIC_CANDIDATE_FACTOR
        )
        ids = [str(document.metadata.get("part_id", "")) for document, _ in pairs]
        if str(record["part_id"]) in ids:
            covered += 1
    check(
        "全部模型都能进入自身查询的候选集",
        covered == len(catalog),
        f"{covered}/{len(catalog)}",
    )


def test_rerank_fixes_ranking() -> None:
    print("\n== 几何重排后的名次质量 ==")
    catalog = load_cad_catalog()
    by_id = {str(r["part_id"]): r for r in catalog}
    store = get_vector_store()

    rank1 = 0
    cross_family = 0
    total = 0
    spreads = []
    heads: Counter[str] = Counter()

    for record in catalog:
        pairs = store.similarity_search_with_score(
            semantic_document_text(record), k=TOP_K * SEMANTIC_CANDIDATE_FACTOR
        )
        hits = _semantic_hits([{"document": d, "score": max(0.0, 1.0 - float(s))} for d, s in pairs])
        top = _rerank_semantic_by_geometry(record, hits, TOP_K)

        ids = [hit.part_id for hit in top]
        scores = [hit.rerank_score for hit in top if hit.rerank_score is not None]
        if ids and ids[0] == str(record["part_id"]):
            rank1 += 1
        if scores:
            spreads.append(max(scores) - min(scores))
        heads[ids[0]] += 1
        own_family = record.get("part_family")
        for hit_id in ids:
            total += 1
            if by_id.get(hit_id, {}).get("part_family") != own_family:
                cross_family += 1

    check("自检索全部排第 1", rank1 == len(catalog), f"{rank1}/{len(catalog)}")
    # 修复前平均 0.005，名次由噪声决定；重排后必须有实质区分度。
    average_spread = sum(spreads) / len(spreads)
    check("top-3 分数极差均值 > 0.05", average_spread > 0.05, f"{average_spread:.4f}")
    ratio = cross_family / total
    # 修复前 64%。留出余量：本库无 sleeve 族样本，跨族命中无法降到零。
    check("top-3 异族混入低于 20%", ratio < 0.20, f"{cross_family}/{total} = {ratio * 100:.0f}%")
    # 修复前单一模型垄断 12/24 榜首。
    top_head, head_count = heads.most_common(1)[0]
    check(
        "不存在垄断榜首的吸引子模型",
        head_count <= len(catalog) // 4,
        f"{top_head} 占 {head_count}/{len(catalog)}",
    )


def test_rerank_preserves_both_scores() -> None:
    """两个分数都必须保留：名次靠几何分，但语义召回分不能被抹掉或冒名顶替。"""
    print("\n== 证据分层 ==")
    catalog = load_cad_catalog()
    record = catalog[0]
    store = get_vector_store()
    pairs = store.similarity_search_with_score(
        semantic_document_text(record), k=TOP_K * SEMANTIC_CANDIDATE_FACTOR
    )
    hits = _semantic_hits([{"document": d, "score": max(0.0, 1.0 - float(s))} for d, s in pairs])
    top = _rerank_semantic_by_geometry(record, hits, TOP_K)

    check("重排结果非空", len(top) > 0)
    first = top[0]
    check("保留语义召回分", isinstance(first.score, float) and first.score > 0, f"{first.score}")
    check("写入几何重排分", first.rerank_score is not None, f"{first.rerank_score}")
    check("两个分数确实不同", first.score != first.rerank_score)
    check("给出可解释的重排依据", len(first.rerank_reasons) > 0, "；".join(first.rerank_reasons)[:60])
    check(
        "结果按几何重排分降序",
        all(
            (top[i].rerank_score or 0) >= (top[i + 1].rerank_score or 0)
            for i in range(len(top) - 1)
        ),
        str([h.rerank_score for h in top]),
    )


def main() -> int:
    for test in (
        test_query_document_symmetry,
        test_candidate_set_covers_self,
        test_rerank_fixes_ranking,
        test_rerank_preserves_both_scores,
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
