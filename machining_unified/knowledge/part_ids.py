"""企业零件图号的统一识别与标准化规则。

BOM 导入、企业问答的图号精确匹配和设计属性回填必须共用同一套规则，
否则写入期与查询期得到的标准编号会对不上，精确命中会静默失效。
"""

from __future__ import annotations

import re


# 企业图号形如 <字母前缀><可选数字>-<三位>-<三位><可选版本字母>，例如
# DTXT806-300-000、DTXT214-870-003、CGT1-210-006、KBT9-210-008、RGK01-200-004、SWDL01-301-004。
# BOM 会在最前面加三位产品前缀（201DTXT706-200-005），
# 工程图文件名会带版本尾缀（DTXT806-300-004B）。
PART_ID_PATTERN = re.compile(r"(?:[0-9]{3})?[A-Z]{2,6}[0-9]{0,3}-[0-9]{3}-[0-9]{3}[A-Z]?")


def normalized_part_id(value: str) -> str:
    """消除扩展名、三位产品前缀与版本尾缀，得到可跨资料关联的标准图号。"""

    text = re.sub(r"\.[^.]+$", "", value.upper())
    text = re.sub(r"^[0-9]{3}(?=[A-Z])", "", text)
    return re.sub(r"([0-9]{3})[A-Z]$", r"\1", text)


def extract_part_ids(text: str) -> set[str]:
    """从任意文本中提取零件图号，并转为标准编号。"""

    return {normalized_part_id(value) for value in PART_ID_PATTERN.findall(text.upper())}
