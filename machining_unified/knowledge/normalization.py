"""规范化大模型提取的零件特征，消除明确同义词与格式差异。"""

import re


PART_TYPE_KEYWORDS = {
    "轴类": ("传动轴", "阶梯轴", "轴"),
    "套筒类": ("薄壁套筒", "衬套", "套筒"),
    "箱体类": ("减速箱箱体", "箱体", "阀体", "轴承座"),
    "盘类": ("安装板", "法兰盘", "法兰", "盖板", "盘"),
}
SPECIAL_FEATURE_KEYWORDS = ("键槽", "薄壁", "轴承孔", "内孔", "螺纹", "孔系", "定位孔")


def normalize_extracted_features(features: dict, question: str) -> dict:
    """仅按输入原文中明确出现的词规范化特征，绝不推测缺失信息。"""

    normalized = dict(features)

    # DeepSeek 有时仅返回“1.6”；统一为后续案例使用的 Ra1.6 格式。
    roughness = normalized.get("roughness_ra")
    if roughness is not None:
        roughness_text = str(roughness).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", roughness_text):
            normalized["roughness_ra"] = f"Ra{roughness_text}"

    # 当模型标为“其他”或未识别时，只用问题中的直接关键词映射到已支持类型。
    if normalized.get("part_type") in (None, "其他"):
        for part_type, keywords in PART_TYPE_KEYWORDS.items():
            if any(keyword in question for keyword in keywords):
                normalized["part_type"] = part_type
                break

    # 将模型输出的细粒度词和原文中的明确特征归一化为知识库检索标签。
    raw_special_features = normalized.get("special_features") or []
    if not isinstance(raw_special_features, list):
        raw_special_features = [str(raw_special_features)]
    canonical_features = [
        keyword
        for keyword in SPECIAL_FEATURE_KEYWORDS
        if any(keyword in str(value) for value in raw_special_features) or keyword in question
    ]
    normalized["special_features"] = canonical_features
    return normalized
