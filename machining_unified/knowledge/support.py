"""设备、刀具和工艺规则的教学型辅助检索。"""

import json
from functools import lru_cache

from machining_unified.config.paths import EQUIPMENT_PATH, PROCESS_RULES_PATH, TOOLS_PATH

SUPPORT_FILES = {
    "equipment": EQUIPMENT_PATH,
    "tools": TOOLS_PATH,
    "rules": PROCESS_RULES_PATH,
}

@lru_cache(maxsize=1)
def load_support_catalog() -> dict:
    """一次性读取设备、刀具和规则目录；文件不存在时返回空目录。"""

    catalog = {}
    for key, path in SUPPORT_FILES.items():
        catalog[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return catalog


def _matches_any(values: list[str], target: str) -> bool:
    """判断列表中是否包含目标字符串。"""

    return target in values


def select_support_data(features: dict) -> dict:
    """按材料、零件类型和特征选择设备/刀具，并触发可解释规则。"""

    catalog = load_support_catalog()
    material = features.get("material")
    part_type = features.get("part_type")
    roughness = features.get("roughness_ra") or ""
    precision = features.get("precision_requirement") or ""
    special_features = features.get("special_features") or []
    if not isinstance(special_features, list):
        special_features = [str(special_features)]

    equipment = [
        item for item in catalog["equipment"]
        if (not material or material in item["materials"])
        and (not part_type or part_type in item["part_types"])
    ][:3]
    tools = [
        item for item in catalog["tools"]
        if (not material or material in item["materials"])
        and any(feature in item["features"] for feature in special_features + [part_type, roughness, precision] if feature)
    ][:5]

    risks = []
    for rule in catalog["rules"]:
        condition = rule["when"]
        matched = True
        for key, expected in condition.items():
            value = special_features if key == "special_features" else features.get(key, "")
            matched = expected in value if isinstance(value, list) else str(value).startswith(expected)
            if not matched:
                break
        if matched:
            risks.append({"name": rule["name"], "risk": rule["risk"], "actions": rule["actions"]})

    return {"equipment": equipment, "tools": tools, "risks": risks}


def format_support_context(support_data: dict) -> str:
    """把辅助数据转为注入回答模型的简短、可追溯上下文。"""

    lines = ["辅助参考（教学样例，仅供工程师审核）："]
    for item in support_data["equipment"]:
        lines.append(f"设备 {item['equipment_id']}：{item['name']}，{item['notes']}")
    for item in support_data["tools"]:
        lines.append(f"刀具 {item['tool_id']}：{item['name']}，{item['notes']}")
    for item in support_data["risks"]:
        lines.append(f"风险规则 {item['name']}：{item['risk']}；措施：{'、'.join(item['actions'])}")
    return "\n".join(lines)
