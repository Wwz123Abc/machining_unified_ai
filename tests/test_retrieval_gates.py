"""检索质量门禁：可复现的基准，替代一次性探针脚本。

分两层，语义不同，不要混用：

**回归层**（失败必须阻断合并）——测的是"管道是否断裂"，而不是"排得好不好"。
它的灵敏度来自一个结构性不变量：``semantic_document_text`` 是 STEP 文件内容的
纯函数，凡是只存在于目录记录、无法由现场解析重建的字段（part_id、BOM、
回填的设计属性）都已排除，因此"查询文本 == 自身文档文本"恒成立，
自检索候选覆盖率必须是 100%。任何低于 100% 都意味着文本构造漂移、索引陈旧、
part_id 映射错误或候选窗口漏召回——四者都是缺陷，没有"数据不好"这种借口。

**质量层**（趋势监控，不阻断）——测排序好坏。这类指标受数据分布影响，
不该用硬阈值卡住合并，但回退必须可见。

用法::

    python tests/test_retrieval_gates.py           # 快速子集，适合 pre-commit
    python tests/test_retrieval_gates.py --full    # 全量，适合 nightly

退出码 0 表示回归层全过。质量层只打印，不影响退出码。

**ANN 守门**：目录规模 < 5000 时禁止引入 faiss/hnswlib。508 条全库线性扫描是
毫秒级，而第四套索引会立刻带来与 CAD 目录的一致性维护成本。见 CLAUDE.md。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from machining_unified.cad.extraction import extract_step_features  # noqa: E402
from machining_unified.config.paths import CAD_CATALOG_PATH  # noqa: E402
from machining_unified.knowledge.engineering import (  # noqa: E402
    build_knowledge_graph,
    expand_part_relations,
)
from machining_unified.knowledge.enterprise import (  # noqa: E402
    enterprise_documents,
    retrieve_enterprise_knowledge,
)
from machining_unified.knowledge.manifests import assemblies_using_part, load_decomposed_parts  # noqa: E402
from machining_unified.retrieval.cad_rag import retrieve_cad_rag, semantic_document_text  # noqa: E402
from machining_unified.services.model_search import (  # noqa: E402
    SEMANTIC_CANDIDATE_FACTOR,
    search_by_step,
    search_by_text,
)

# 采样规模。快速档按装配分层抽样，保证 13 个装配都有代表。
FAST_SAMPLE = 26
FAST_ENTERPRISE_SAMPLE = 60

# 阈值来自 2026-08-14 的实测值加余量，改动阈值必须同时更新这里的实测记录。
# 实测：508 条目录构图 <见下>；文字检索稳态 <见下>。
KG_BUILD_SECONDS = 25.0
TEXT_SEARCH_SECONDS = 3.0
# 质量层参考线：几何重排 top-1 实测 469/508 = 92.3%（近重复零件会合理地压过自身）。
GEOMETRY_TOP1_REFERENCE = 0.90
# 同装配共现的随机基线由每个装配的规模现算，不写死。

_regression_failures: list[str] = []
_quality_notes: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not condition:
        _regression_failures.append(label)
    return condition


def note(label: str, detail: str) -> None:
    print(f"  [INFO] {label}  {detail}")
    _quality_notes.append(f"{label}: {detail}")


def load_catalog() -> list[dict[str, Any]]:
    return json.loads(CAD_CATALOG_PATH.read_text(encoding="utf-8"))


def stratified_sample(catalog: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """按资料组分层抽样，保证每个装配至少有一个代表。

    随机全局抽样会让 182 个零件的 IMU180-200-000 淹没只有 1~2 个零件的小装配，
    而小装配恰恰是共用去重最集中的地方。
    """

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in catalog:
        by_group[str(record.get("model_group_id", record["part_id"]))].append(record)
    rng = random.Random(20260814)
    picked: list[dict[str, Any]] = []
    groups = sorted(by_group)
    for group in groups:
        picked.append(rng.choice(by_group[group]))
    remaining = [r for r in catalog if r not in picked]
    rng.shuffle(remaining)
    picked.extend(remaining[: max(0, size - len(picked))])
    return picked[:size]


# --------------------------------------------------------------------- 回归层


def gate_self_retrieval(catalog: list[dict[str, Any]], full: bool) -> None:
    """R1 自检索候选覆盖率。文本恒等 => 必须 100%。"""

    print("\n== R1 自检索候选覆盖率（回归层）==")
    sample = catalog if full else stratified_sample(catalog, FAST_SAMPLE)
    print(f"  样本 {len(sample)} / 目录 {len(catalog)}（{'全量' if full else '分层快速档'}）")

    identical = asymmetric = 0
    uncovered: list[str] = []
    not_top1: list[str] = []
    geometry_top1 = 0
    evaluated = 0
    started = time.perf_counter()
    for record in sample:
        source = ROOT / str(record["source_file"])
        if not source.is_file():
            check(f"源文件存在：{record['part_id']}", False, str(source))
            continue
        result = search_by_step(source, top_k=5)
        evaluated += 1
        if semantic_document_text(record) == semantic_document_text(result.query):
            identical += 1
        else:
            asymmetric += 1
        candidates = [
            item["part_id"]
            for item in retrieve_cad_rag(result.query, top_k=5 * SEMANTIC_CANDIDATE_FACTOR)
        ]
        if record["part_id"] not in candidates:
            uncovered.append(str(record["part_id"]))
        if [hit.part_id for hit in result.semantic][:1] != [record["part_id"]]:
            not_top1.append(str(record["part_id"]))
        if [hit.part_id for hit in result.geometry][:1] == [record["part_id"]]:
            geometry_top1 += 1
    elapsed = time.perf_counter() - started

    # 这是本门禁最关键的一条：它只在管道断裂时才会红。
    check(
        "文本对称性是结构性不变量（不对称样本数为 0）",
        asymmetric == 0,
        f"恒等 {identical} / 不对称 {asymmetric}",
    )
    check(
        "自检索候选覆盖率 100%",
        not uncovered,
        f"{evaluated - len(uncovered)}/{evaluated}" + (f"  漏：{uncovered[:8]}" if uncovered else ""),
    )
    if evaluated:
        note(
            "语义重排后 top-1（质量层）",
            f"{evaluated - len(not_top1)}/{evaluated} = {(evaluated-len(not_top1))/evaluated:.1%}"
            + (f"  未居首：{not_top1[:6]}" if not_top1 else ""),
        )
        note(
            "几何分支 top-1（质量层，参考线 %.0f%%）" % (GEOMETRY_TOP1_REFERENCE * 100),
            f"{geometry_top1}/{evaluated} = {geometry_top1/evaluated:.1%}",
        )
        note("单例耗时", f"{elapsed/evaluated:.1f}s/例，全量 {len(catalog)} 例约 "
                         f"{elapsed/evaluated*len(catalog)/60:.0f} 分钟")


def gate_identifier_match(full: bool) -> None:
    """R2 图号精确命中：排序零违规 + 自召回 + top_k 前缀契约。"""

    print("\n== R2 企业库图号精确命中（回归层）==")
    documents = enterprise_documents()
    sample = list(documents) if full else random.Random(20260814).sample(
        list(documents), min(FAST_ENTERPRISE_SAMPLE, len(documents))
    )
    print(f"  样本 {len(sample)} / 证据 {len(documents)}")

    violations: list[str] = []
    missed: list[str] = []
    for document in sample:
        metadata = document.metadata
        question = str(metadata.get("normalized_part_id") or metadata.get("part_id") or metadata["title"])
        evidence = retrieve_enterprise_knowledge(question, top_k=5)
        matched = [item.identifier_match for item in evidence]
        # 精确命中必须全部排在非命中之前，与 retrieve_enterprise_knowledge 的排序约定一致。
        if matched != sorted(matched, reverse=True):
            violations.append(str(metadata["source_id"]))
        if metadata["source_id"] not in [item.source_id for item in evidence]:
            missed.append(str(metadata["source_id"]))

    check("图号精确命中排序零违规", not violations, f"违规 {violations[:6]}" if violations else "")
    check(
        "全库自查询召回 100%",
        not missed,
        f"{len(sample)-len(missed)}/{len(sample)}" + (f"  漏：{missed[:6]}" if missed else ""),
    )

    # 结构性契约：向量分与 BM25 同为全库计算，调整返回数量只应改变截断长度。
    prefix_ok = True
    for question in ("110008089491", "DTXT806-300-012 的材料和表面处理", "IMU180-221-001"):
        orders = {
            top_k: [item.source_id for item in retrieve_enterprise_knowledge(question, top_k=top_k)]
            for top_k in (3, 5, 8)
        }
        prefix_ok &= all(orders[k] == orders[8][:k] for k in (3, 5))
    check("小 top_k 结果是大 top_k 的前缀", prefix_ok)


def gate_shared_parts() -> None:
    """R3 跨装配复用件：知识图谱的事实边必须与拆解台账一致。"""

    print("\n== R3 跨装配复用件的图谱事实边（回归层）==")
    ledger = load_decomposed_parts()
    if not ledger:
        print("  [SKIP] 未发现拆解台账，跳过（只有成套资料包的部署属于正常状态）")
        return
    shared = [part_id for part_id, item in ledger.items() if item.get("also_used_in")]
    print(f"  台账 {len(ledger)} 个零件，其中跨装配复用 {len(shared)} 个")

    mismatched: list[str] = []
    for part_id in shared:
        expected = set(assemblies_using_part(part_id))
        relations = expand_part_relations(part_id)
        actual = {
            item["node"].removesuffix(" 装配")
            for item in relations["facts"]
            if item["kind"] == "assembly"
        }
        if actual != expected:
            mismatched.append(f"{part_id}: 期望 {sorted(expected)} 实得 {sorted(actual)}")
    check(
        "复用件的装配事实边完整",
        not mismatched,
        f"{len(shared)-len(mismatched)}/{len(shared)}" + (f"  不符：{mismatched[:3]}" if mismatched else ""),
    )

    # 事实边与候选边必须分开：装配归属来自产品树遍历，不能混进几何规则候选。
    if shared:
        sample_id = sorted(shared)[0]
        relations = expand_part_relations(sample_id)
        check(
            "装配归属只出现在事实边，不出现在候选边",
            not any(item["kind"] == "assembly" for item in relations["candidates"]),
            sample_id,
        )


def gate_latency(catalog: list[dict[str, Any]]) -> None:
    """R4 延迟断言。阈值来自实测加余量，回退必须可见。"""

    print("\n== R4 延迟（回归层）==")
    started = time.perf_counter()
    graph = build_knowledge_graph(catalog)
    kg_seconds = time.perf_counter() - started
    pairs = len(catalog) * (len(catalog) - 1) // 2
    check(
        f"知识图谱构图 < {KG_BUILD_SECONDS}s",
        kg_seconds < KG_BUILD_SECONDS,
        f"{kg_seconds:.2f}s（{len(catalog)} 条 / {pairs:,} 对，边 {len(graph['edges'])}）",
    )

    timings = []
    for question in ("带键槽的轴类零件", "薄壁套筒", "带轴承孔的箱体"):
        search_by_text(question, top_k=5)  # 预热，排除首次加载
        started = time.perf_counter()
        search_by_text(question, top_k=5)
        timings.append(time.perf_counter() - started)
    worst = max(timings)
    check(
        f"文字检索稳态 < {TEXT_SEARCH_SECONDS}s",
        worst < TEXT_SEARCH_SECONDS,
        f"最慢 {worst:.2f}s（{[f'{t:.2f}' for t in timings]}）",
    )


# --------------------------------------------------------------------- 质量层


def quality_relatedness_lift(catalog: list[dict[str, Any]]) -> None:
    """Q1 同装配共现提升度。

    共现衡量的是"装在一起"，不是"形状相似"——同一装配里完全可能既有支架又有轴。
    因此这里不测 top-N 纯度（那等于奖励"搜支架时返回轴"），只测提升度：
    同装配零件在结果中的占比是否显著高于随机期望。期望值按每个装配的规模现算。
    """

    print("\n== Q1 同装配共现提升度（质量层）==")
    ledger = load_decomposed_parts()
    if not ledger:
        print("  [SKIP] 未发现拆解台账")
        return

    membership: dict[str, set[str]] = {
        part_id: set(assemblies_using_part(part_id)) for part_id in ledger
    }
    total = len(catalog)
    by_assembly: dict[str, set[str]] = defaultdict(set)
    for part_id, assemblies in membership.items():
        for assembly in assemblies:
            by_assembly[assembly].add(part_id)

    rng = random.Random(20260814)
    lifts: list[float] = []
    top_n = 10
    for assembly, members in sorted(by_assembly.items()):
        if len(members) < 3:
            continue
        probe = rng.choice(sorted(members))
        record = next((r for r in catalog if str(r["part_id"]) == probe), None)
        source = ROOT / str(record["source_file"]) if record else None
        if source is None or not source.is_file():
            continue
        result = search_by_step(source, top_k=top_n)
        returned = [hit.part_id for hit in result.geometry][:top_n]
        if not returned:
            continue
        observed = sum(1 for pid in returned if assembly in membership.get(pid, set())) / len(returned)
        expected = (len(members) - 1) / (total - 1)
        if expected <= 0:
            continue
        lifts.append(observed / expected)
        note(
            f"{assembly[:22]:<24}",
            f"成员 {len(members):>3}  同装配占比 {observed:.1%}  随机期望 {expected:.1%}  "
            f"lift {observed/expected:.1f}x",
        )
    if lifts:
        mean_lift = sum(lifts) / len(lifts)
        note("平均提升度", f"{mean_lift:.2f}x（{len(lifts)} 个装配；1.0 = 与随机无异）")


def quality_attribute_terms(catalog: list[dict[str, Any]]) -> None:
    """Q2 属性术语查询。

    现基线应接近零命中：``enriched_text`` 里根本没有设计属性行，而它就是混合排序的
    BM25 语料；语义索引经阶段 D 也已移除属性。补齐材料数据不会改变这一点——
    这条缺口要由结构化属性通道（术语解析 -> design_metadata 精确匹配）来补。
    **第 4 步上线后本组必须转正**，它是"结构化属性通道是否生效"的直接探针。
    """

    print("\n== Q2 属性术语查询（质量层，现基线应为零命中）==")
    attributed = {
        str(record["part_id"])
        for record in catalog
        if record.get("design_metadata")
        and any(value not in (None, "", []) for value in record["design_metadata"].values())
    }
    note("全库带设计属性的零件", f"{len(attributed)}/{len(catalog)} = {len(attributed)/len(catalog):.1%}")
    for question in ("45钢的轴类零件", "304不锈钢套筒", "HT250 铸铁箱体", "发黑处理的零件"):
        result = search_by_text(question, top_k=5)
        semantic_hits = [h.part_id for h in result.semantic][:5]
        hybrid_hits = [h.part_id for h in result.hybrid][:5]
        note(
            question,
            f"语义命中带属性件 {[p for p in semantic_hits if p in attributed] or '无'}；"
            f"混合命中带属性件 {[p for p in hybrid_hits if p in attributed] or '无'}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="检索质量门禁")
    parser.add_argument("--full", action="store_true", help="全量评测（约 10~20 分钟）")
    args = parser.parse_args()

    catalog = load_catalog()
    print(f"CAD 目录 {len(catalog)} 条 ｜ 模式：{'全量' if args.full else '快速'}")
    if len(catalog) >= 5000:
        print("\n!! 目录已达 5000 条：可以重新评估引入 ANN 索引，并同步修订本门禁的延迟阈值。")

    gate_self_retrieval(catalog, args.full)
    gate_identifier_match(args.full)
    gate_shared_parts()
    gate_latency(catalog)
    quality_relatedness_lift(catalog)
    quality_attribute_terms(catalog)

    print("\n" + "=" * 68)
    if _regression_failures:
        print(f"回归层失败 {len(_regression_failures)} 项：")
        for item in _regression_failures:
            print(f"  - {item}")
        return 1
    print(f"回归层全部通过；质量层记录 {len(_quality_notes)} 条指标。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
