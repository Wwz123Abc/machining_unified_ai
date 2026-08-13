"""从已审核案例文本中提取可用于检索和审计的标准工艺元数据。"""

import re


SPECIAL_FEATURE_KEYWORDS = (
    "薄壁",
    "键槽",
    "螺纹",
    "退刀槽",
    "孔系",
    "轴承孔",
    "内孔",
    "密封端面",
    "定位孔",
)
HEAT_TREATMENT_KEYWORDS = ("调质", "时效处理", "热处理")


def derive_case_metadata(content: str) -> dict[str, str]:
    """从案例正文抽取粗糙度、精度、热处理和特殊特征。

    只返回文本中明确出现的信息；缺失字段使用“未标注”，避免把推测写入知识库。
    Chroma metadata 使用字符串，多个值以中文顿号连接。
    """

    roughness_values = sorted(set(re.findall(r"Ra\s*\d+(?:\.\d+)?", content)))
    precision_values = sorted(set(re.findall(r"IT\s*\d+", content, flags=re.IGNORECASE)))
    heat_treatment_values = [item for item in HEAT_TREATMENT_KEYWORDS if item in content]
    special_features = [item for item in SPECIAL_FEATURE_KEYWORDS if item in content]

    return {
        "roughness_ra": "、".join(roughness_values) or "未标注",
        "precision_requirement": "、".join(precision_values).upper() or "未标注",
        "heat_treatment": "、".join(heat_treatment_values) or "未标注",
        "special_features": "、".join(special_features) or "未标注",
    }
