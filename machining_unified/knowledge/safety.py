"""机械加工 RAG 的输入完整性与安全输出规则。

这些规则独立于大模型，避免模型在知识库覆盖范围之外自行编造工艺。
"""


REQUIRED_FEATURE_FIELDS = {
    "material": "材料",
    "part_type": "零件类型",
}


def get_missing_required_features(features: dict) -> list[str]:
    """返回当前教学知识库推荐前必须补充的字段中文名称。"""

    return [label for key, label in REQUIRED_FEATURE_FIELDS.items() if not features.get(key)]


def get_uncovered_features(
    features: dict,
    supported_materials: set[str],
    supported_part_types: set[str],
) -> list[str]:
    """返回已识别、但当前教学知识库尚未覆盖的关键特征。"""

    uncovered = []
    material = features.get("material")
    part_type = features.get("part_type")
    if material and material not in supported_materials:
        uncovered.append(f"材料“{material}”")
    if part_type and part_type not in supported_part_types:
        uncovered.append(f"零件类型“{part_type}”")
    return uncovered


def build_incomplete_requirement_answer(missing_features: list[str]) -> str:
    """构造不生成工艺路线的固定说明，防止“依据不足”时出现臆测。"""

    missing_text = "、".join(missing_features)
    return (
        "当前信息不足，暂不生成加工工艺路线。"
        f"请至少补充：{missing_text}。"
        "建议同时提供关键尺寸、表面粗糙度、尺寸公差、热处理和特殊结构要求。"
    )


def build_uncovered_requirement_answer(uncovered_features: list[str]) -> str:
    """说明知识库覆盖边界，避免把不相干案例伪装成可靠依据。"""

    uncovered_text = "、".join(uncovered_features)
    return (
        f"当前教学知识库尚未覆盖：{uncovered_text}，暂不生成加工工艺路线。"
        "请导入对应的已审核工艺资料，或由工艺工程师确认后再推荐。"
    )
