"""把装配 STEP 按 XCAF 产品树拆成独立零件 STEP，并写出来源台账。

用途：企业给的资料常常只有整装配文件，而"以模型搜模型"需要的是零件级样本。
本脚本不改动源文件，只在 ``data/enterprise/cad_samples/assemblies/<装配号>/``
下生成零件文件，随后由 ``build_cad_catalog.py`` 正常入库。

    python scripts/decompose_assembly_step.py <装配目录或 STEP 文件> [--dry-run]

``--dry-run`` 只统计不落盘，用于先看清成分再决定是否导入。

拆解完成后必须按 CLAUDE.md 第 8 节的顺序重建目录与三套索引。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from OCP.Bnd import Bnd_Box  # noqa: E402
from OCP.BRepBndLib import BRepBndLib  # noqa: E402
from OCP.IFSelect import IFSelect_RetDone  # noqa: E402
from OCP.STEPCAFControl import STEPCAFControl_Reader  # noqa: E402
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer  # noqa: E402
from OCP.Standard import Standard_Failure  # noqa: E402
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString  # noqa: E402
from OCP.TDataStd import TDataStd_Name  # noqa: E402
from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool  # noqa: E402
from OCP.TDocStd import TDocStd_Document  # noqa: E402
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.XCAFApp import XCAFApp_Application  # noqa: E402
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool  # noqa: E402

from machining_unified.config.logging_setup import configure_logging  # noqa: E402
from machining_unified.config.paths import CAD_SAMPLES_DIR, DECOMPOSED_PARTS_PATH  # noqa: E402
from machining_unified.knowledge.part_ids import extract_part_ids  # noqa: E402

logger = configure_logging()

OUTPUT_ROOT = CAD_SAMPLES_DIR / "assemblies"
STEP_SUFFIXES = {".step", ".stp"}

# Part-21 字符串字面量：单引号包裹，内部 '' 表示一个字面单引号。
STEP_STRING = re.compile(rb"'((?:[^']|'')*)'")

# 外购标准件的名称特征。这些零件仍会导出（它们是真实几何），
# 但在台账中标记出来，便于后续判断是否要从检索里剔除。
VENDOR_PATTERN = re.compile(
    r"^GB[-/ ]?T|^GB\d|^JIS[-/ ]?B|^ISO\s?\d|^DIN\s?\d"
    r"|MISUMI|HABASIT|ELATECH|HIWIN|NSK|THK|SMC|FESTO|SKF|NBK|IKO|NEWSPORT"
    r"|上银|米思米|怡合达|同工",
    re.IGNORECASE,
)

# 文件名里不允许出现的字符。中文可以保留（项目其它样本目录已有中文名），
# 但路径分隔符与 Windows 保留字符必须去掉。
UNSAFE_IN_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# CAD 把"某零件在某装配中的副本"导出成 "复件 <零件名>^<父装配号>"。
# ^ 之后是父装配的上下文，不是这个零件的身份。
CONTEXT_SEPARATOR = "^"
COPY_PREFIX = re.compile(r"^(?:复件|副本|copy\s+of)\s+", re.IGNORECASE)


@contextlib.contextmanager
def suppressed_native_stdout() -> Iterator[None]:
    """屏蔽 OCCT 写 STEP 时直接打到 fd 1 的统计信息。

    这些信息来自 C++ 层的 std::cout，Python 级别的重定向拦不住，
    必须在文件描述符上做；否则 650 个零件会刷出几千行噪音，
    把真正的结构化日志淹没。
    """

    sys.stdout.flush()
    saved = os.dup(1)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        yield
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def _escape_non_ascii(text: str) -> bytes:
    """把非 ASCII 字符写成 Part-21 的 \\X2\\....\\X0\\ 转义段。"""

    out = bytearray()
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        out.extend(b"\\X2\\")
        for char in pending:
            for unit in char.encode("utf-16-be"):
                out.extend(f"{unit:02X}".encode("ascii"))
        out.extend(b"\\X0\\")
        pending.clear()

    for char in text:
        if ord(char) < 128:
            flush()
            out.append(ord(char))
        else:
            pending.append(char)
    flush()
    return bytes(out)


def normalize_step_strings(raw: bytes, encoding: str = "gbk") -> tuple[bytes, int]:
    """把 STEP 里的裸多字节字符改写成 Part-21 转义形式。

    中文 CAD 导出的 STEP 经常直接塞入 GBK 字节而不做转义。OCCT 对这类字符串
    的处理是有损的（实测 6 个 GBK 字节被塌成 3 个码点，且不可逆），
    产品名会变成乱码，图号与供应商都认不出来。

    这里只改写字符串字面量的内容，实体结构和数值一律不动；实测改写前后
    solids / faces / edges / bounding_box 完全一致。
    """

    changed = 0

    def replace(match: re.Match[bytes]) -> bytes:
        nonlocal changed
        body = match.group(1)
        if not any(byte >= 0x80 for byte in body):
            return match.group(0)
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            # 解不出来就原样保留：宁可维持现状，也不要写入猜测的内容。
            return match.group(0)
        changed += 1
        return b"'" + _escape_non_ascii(text) + b"'"

    return STEP_STRING.sub(replace, raw), changed


def label_entry(label: TDF_Label) -> str:
    entry = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, entry)
    return entry.ToCString()


def label_name(label: TDF_Label) -> str:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return attr.Get().ToExtString().strip()
    return ""


@dataclass
class Leaf:
    """产品树上的一个叶子零件。"""

    entry: str
    name: str
    shape: Any
    instance_count: int = 1


@dataclass
class PartRecord:
    """已导出零件的台账条目。"""

    part_id: str
    name: str
    identified_part_id: str | None
    source_file: str
    origin_assembly: str
    fingerprint: dict[str, Any]
    is_vendor_part: bool
    instance_count: int
    also_used_in: list[str] = field(default_factory=list)


def shape_fingerprint(shape: Any) -> dict[str, Any]:
    """拓扑计数加外包络。用于发现"同图号但几何不同"的异常，不用于相似度计算。"""

    counts: dict[str, int] = {}
    for key, kind in (("faces", TopAbs_FACE), ("edges", TopAbs_EDGE), ("vertices", TopAbs_VERTEX)):
        explorer = TopExp_Explorer(shape, kind)
        total = 0
        while explorer.More():
            total += 1
            explorer.Next()
        counts[key] = total
    # 必须用 AddOptimal_s：Add_s 在没有缓存三角网格时改用曲面控制点包络，
    # 会大幅高估。同一零件在不同装配文件里是否带网格并不一致，于是会量出
    # 不同尺寸——实测 IMU180-222-021 在两个装配里分别是 3180×2348×1952
    # 和 852×240×20，面数却都是 208。那会把同一个零件误判成两个。
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box)
    if box.IsVoid():
        counts["bbox_mm"] = None
        return counts
    x0, y0, z0, x1, y1, z1 = box.Get()
    counts["bbox_mm"] = [round(x1 - x0, 3), round(y1 - y0, 3), round(z1 - z0, 3)]
    return counts


def read_assembly(step_path: Path) -> tuple[list[Leaf], int]:
    """读入装配并收集唯一叶子零件。返回 (叶子列表, 字符串改写数)。"""

    raw = step_path.read_bytes()
    converted, changed = normalize_step_strings(raw)

    # 只有确实改写过才落临时文件，避免为纯 ASCII 的装配白写一份大文件。
    handle = None
    read_path = step_path
    if changed:
        descriptor, temporary = tempfile.mkstemp(suffix=".step", prefix="decompose_")
        os.close(descriptor)
        handle = Path(temporary)
        handle.write_bytes(converted)
        read_path = handle

    try:
        application = XCAFApp_Application.GetApplication_s()
        document = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
        application.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), document)
        reader = STEPCAFControl_Reader()
        with suppressed_native_stdout():
            transferred = reader.ReadFile(str(read_path)) and reader.Transfer(document)
        if not transferred:
            raise ValueError(f"XCAF 无法读取装配：{step_path.name}")

        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        roots = TDF_LabelSequence()
        tool.GetFreeShapes(roots)

        leaves: dict[str, Leaf] = {}

        def walk(label: TDF_Label) -> None:
            if XCAFDoc_ShapeTool.IsAssembly_s(label):
                children = TDF_LabelSequence()
                XCAFDoc_ShapeTool.GetComponents_s(label, children)
                for index in range(1, children.Length() + 1):
                    child = children.Value(index)
                    referred = TDF_Label()
                    walk(referred if XCAFDoc_ShapeTool.GetReferredShape_s(child, referred) else child)
                return
            referred = TDF_Label()
            target = referred if XCAFDoc_ShapeTool.GetReferredShape_s(label, referred) else label
            entry = label_entry(target)
            existing = leaves.get(entry)
            if existing is not None:
                # 同一零件被装配多次引用；导出一份即可，实例数单独记录。
                existing.instance_count += 1
                return
            shape = XCAFDoc_ShapeTool.GetShape_s(target)
            if shape.IsNull():
                logger.warning(
                    "跳过空形状的叶子节点",
                    extra={
                        "assembly": step_path.name,
                        "label_entry": entry,
                        "product_name": label_name(target),
                    },
                )
                return
            leaves[entry] = Leaf(entry=entry, name=label_name(target), shape=shape)

        for index in range(1, roots.Length() + 1):
            walk(roots.Value(index))
        return list(leaves.values()), changed
    finally:
        if handle is not None:
            handle.unlink(missing_ok=True)


def own_name(raw_name: str) -> str:
    """剥掉父装配上下文和"复件"前缀，得到零件自己的名字。

    不能直接拿整串去匹配图号：``复件 XC11-KJ^IMUC40-700-001`` 里的
    ``IMUC40-700-001`` 是父装配号。实测 IMU108-300-000 的 40 个叶子中有 18 个
    共享同一个父装配，整串匹配会把它们全判成同一个零件。
    """

    name = raw_name.split(CONTEXT_SEPARATOR, 1)[0].strip()
    while True:
        stripped = COPY_PREFIX.sub("", name).strip()
        if stripped == name:
            return name
        name = stripped


def derive_part_id(leaf: Leaf, assembly_id: str) -> tuple[str, str | None]:
    """推导零件编号。返回 (part_id, 识别出的图号或 None)。

    part_id 一律取零件自己的名字，而不是从中截出的图号片段——截取会丢掉
    ``-0528`` 这类版本后缀，把源数据本来区分开的零件合并掉。
    图号只作为单独的可检索字段记录，不冒充身份。
    """

    name = own_name(leaf.name)
    identifiers = extract_part_ids(name)
    # extract_part_ids 返回集合，排序后取第一个保证结果可复现。
    identified = sorted(identifiers)[0] if identifiers else None
    cleaned = UNSAFE_IN_FILENAME.sub("-", name).strip().strip(".")
    # 下划线必须换掉：build_cad_catalog 用 stem.split("_", 1)[0] 推导 part_id
    # （为了 TEACH-CAD-001_shaft_keyway.step 这类教学文件名），
    # 名字里带下划线会被就地截断，实测 39 个零件受影响、其中 3 个会截成同一个。
    cleaned = cleaned.replace("_", "-")
    if cleaned:
        return cleaned[:80], identified
    return f"{assembly_id}-NONAME-{leaf.entry.replace(':', '-')}", identified


def unique_part_id(base: str, taken: set[str], assembly_id: str) -> str:
    """为同名但几何不同的零件生成可区分且可追溯的编号。

    分隔符用 @ 而不是下划线，理由同 derive_part_id：下划线会被目录构建脚本截断，
    后缀失效后两条记录又会撞成同一个 part_id。
    """

    candidate = f"{base}@{assembly_id}"
    if candidate not in taken:
        return candidate
    index = 2
    while f"{candidate}-{index}" in taken:
        index += 1
    return f"{candidate}-{index}"


def export_leaf(shape: Any, target: Path) -> None:
    """把单个零件形状写成独立 STEP 文件。"""

    writer = STEPControl_Writer()
    # Transfer 与 Write 各自都会往 fd 1 打统计信息，两者都要包进来。
    with suppressed_native_stdout():
        writer.Transfer(shape, STEPControl_AsIs)
        status = writer.Write(str(target))
    if status != IFSelect_RetDone or not target.is_file():
        raise ValueError(f"STEP 写出失败：{target.name}")


def file_md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def discover_assemblies(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(path for path in target.rglob("*") if path.suffix.lower() in STEP_SUFFIXES)


def decompose(sources: list[Path], *, dry_run: bool) -> dict[str, Any]:
    """拆解全部装配，返回台账内容。"""

    registry: dict[str, PartRecord] = {}
    per_assembly: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    skipped_empty = 0

    for step_path in sources:
        assembly_id = step_path.stem
        leaves, changed = read_assembly(step_path)
        target_dir = OUTPUT_ROOT / assembly_id
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)

        exported = reused = renamed = 0
        for leaf in leaves:
            part_id, identified = derive_part_id(leaf, assembly_id)
            fingerprint = shape_fingerprint(leaf.shape)
            if not fingerprint["faces"]:
                # 没有面的叶子（空的定位基准、参考点等）导出后没有可转换的几何根节点，
                # 入库只会得到一条低置信度的文本摘要记录，污染检索。
                # extra 里不能用 name：那是 LogRecord 的保留字段，stdlib 会抛 KeyError。
                logger.warning(
                    "跳过没有面的叶子节点",
                    extra={"assembly": assembly_id, "part_id": part_id, "product_name": leaf.name},
                )
                skipped_empty += 1
                continue
            existing = registry.get(part_id)
            if existing is not None:
                if existing.fingerprint == fingerprint:
                    # 同名同几何：同一零件被多个装配共用，只保留一份。
                    if assembly_id not in existing.also_used_in:
                        existing.also_used_in.append(assembly_id)
                    existing.instance_count += leaf.instance_count
                    reused += 1
                    continue
                # 同名但几何不同：这是两个不同的零件，都必须保留。
                # 合并会永久丢失其中一个，而目录唯一性校验又不允许重名。
                conflicts.append(
                    {
                        "part_id": part_id,
                        "held_by": existing.origin_assembly,
                        "renamed_for": assembly_id,
                        "held_topology": existing.fingerprint,
                        "renamed_topology": fingerprint,
                    }
                )
                part_id = unique_part_id(part_id, set(registry), assembly_id)
                logger.warning(
                    "同名零件的几何不一致，改用带装配后缀的编号另存",
                    extra={
                        "original_name": leaf.name,
                        "held_by": existing.origin_assembly,
                        "assigned_part_id": part_id,
                    },
                )
                renamed += 1

            target = target_dir / f"{part_id}.step"
            if not dry_run:
                export_leaf(leaf.shape, target)
            registry[part_id] = PartRecord(
                part_id=part_id,
                name=leaf.name,
                identified_part_id=identified,
                source_file=str(target.relative_to(ROOT)).replace("\\", "/"),
                origin_assembly=assembly_id,
                fingerprint=fingerprint,
                is_vendor_part=bool(VENDOR_PATTERN.search(leaf.name)),
                instance_count=leaf.instance_count,
            )
            exported += 1

        per_assembly.append(
            {
                "assembly_id": assembly_id,
                "source_path": str(step_path),
                "source_md5": file_md5(step_path),
                "unique_leaves": len(leaves),
                "exported_parts": exported,
                "shared_with_earlier_assembly": reused,
                "renamed_for_conflict": renamed,
                "rewritten_strings": changed,
                "identified_part_ids": sum(
                    1 for leaf in leaves if extract_part_ids(own_name(leaf.name))
                ),
            }
        )
        logger.info(
            "装配拆解完成",
            extra={
                "assembly": assembly_id,
                "unique_leaves": len(leaves),
                "exported": exported,
                "reused": reused,
                "renamed": renamed,
            },
        )
        print(
            f"  {assembly_id:<20} 叶子 {len(leaves):>4}  新导出 {exported:>4}  "
            f"共用复用 {reused:>4}  重名另存 {renamed:>4}  字符串改写 {changed:>4}"
        )

    if not dry_run:
        # 子装配的零件可能全部与更早处理的装配共用，去重后目录会是空的。
        # 留着空目录会让 build_cad_catalog 的资料组统计出现没有成员的组。
        for step_path in sources:
            target_dir = OUTPUT_ROOT / step_path.stem
            if target_dir.is_dir() and not any(target_dir.iterdir()):
                target_dir.rmdir()
                logger.info(
                    "移除空的装配目录（零件全部与其它装配共用）",
                    extra={"assembly": step_path.stem},
                )

    records = sorted(registry.values(), key=lambda item: item.part_id)
    return {
        "source_count": len(sources),
        "unique_parts": len(records),
        "skipped_empty_leaves": skipped_empty,
        "vendor_parts": sum(1 for item in records if item.is_vendor_part),
        "without_identified_part_id": sum(1 for item in records if item.identified_part_id is None),
        "name_conflicts": conflicts,
        "assemblies": per_assembly,
        "parts": [
            {
                "part_id": item.part_id,
                "product_name": item.name,
                "identified_part_id": item.identified_part_id,
                "source_file": item.source_file,
                "origin_assembly": item.origin_assembly,
                "also_used_in": item.also_used_in,
                "instance_count": item.instance_count,
                "is_vendor_part": item.is_vendor_part,
                "topology": item.fingerprint,
            }
            for item in records
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="把装配 STEP 拆成零件级 STEP")
    parser.add_argument("source", type=Path, help="装配 STEP 文件或包含装配的目录")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写出任何文件")
    args = parser.parse_args()

    source: Path = args.source
    if not source.exists():
        print(f"输入不存在：{source}", file=sys.stderr)
        return 1

    sources = discover_assemblies(source)
    if not sources:
        print(f"没有找到 STEP 文件：{source}", file=sys.stderr)
        return 1

    logger.info(
        "开始拆解装配",
        extra={"source": str(source), "assembly_count": len(sources), "dry_run": args.dry_run},
    )
    print(f"{'[试运行] ' if args.dry_run else ''}准备拆解 {len(sources)} 个装配\n")

    try:
        summary = decompose(sources, dry_run=args.dry_run)
    except (OSError, ValueError, RuntimeError, Standard_Failure):
        # 故障模式可枚举：文件读写、STEP 结构、OCCT 内部错误。
        logger.exception("装配拆解失败")
        return 1

    print(
        f"\n唯一零件 {summary['unique_parts']} 个"
        f"（外购标准件 {summary['vendor_parts']}，未识别出图号 {summary['without_identified_part_id']}）"
    )
    if summary["name_conflicts"]:
        print(f"注意：{len(summary['name_conflicts'])} 处同名但几何不同，已分别另存，详见台账。")

    if args.dry_run:
        print("\n试运行结束，未写出任何文件。")
        return 0

    DECOMPOSED_PARTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECOMPOSED_PARTS_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"台账已写入 {DECOMPOSED_PARTS_PATH.relative_to(ROOT)}")
    print("下一步：按 CLAUDE.md 第 8 节顺序重建 CAD 目录与三套索引。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
