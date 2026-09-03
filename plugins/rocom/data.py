"""洛克王国本地资料数据。"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from rapidfuzz import fuzz, process
except Exception:  # pragma: no cover - 仅用于缺少可选加速依赖时的外部环境兼容
    fuzz = None
    process = None

RESOURCE_DIR = Path(__file__).parent / "resources"
DATA_DIR = RESOURCE_DIR / "data"

ATTR_FIELDS = {
    "生命": "attr_hp",
    "HP": "attr_hp",
    "物攻": "attr_atk",
    "魔攻": "attr_spatk",
    "物防": "attr_def",
    "魔防": "attr_spdef",
    "速度": "attr_spd",
}

SKILL_LIST_FIELDS = ("level_skill_list", "machine_skill_list", "blood_skill_list")


@lru_cache(maxsize=16)
def _load_json(name: str) -> Any:
    """读取插件内置 JSON 数据。"""
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def get_pet_list() -> dict[str, dict[str, Any]]:
    """获取精灵列表。"""
    data = _load_json("pet_list.json")
    return data if isinstance(data, dict) else {}


def get_skill_list() -> dict[str, dict[str, Any]]:
    """获取技能列表。"""
    data = _load_json("mini-map.json")
    return data if isinstance(data, dict) else {}


def _pet_display_name(pet: dict[str, Any]) -> str:
    name = str(pet.get("name") or "")
    form = str(pet.get("form") or "")
    return f"{name}{form}" if form else name


@lru_cache(maxsize=1)
def _pet_name_choices() -> dict[str, str]:
    """构建精灵名称到 ID 的索引。"""
    choices: dict[str, str] = {}
    for pet_id, pet in get_pet_list().items():
        name = str(pet.get("name") or "").strip()
        form = str(pet.get("form") or "").strip()
        if not name:
            continue
        for alias in {
            name,
            form,
            f"{name}{form}",
            f"{form}{name}",
            _pet_display_name(pet),
        }:
            alias = alias.strip()
            if alias:
                choices.setdefault(alias, pet_id)
    return choices


def find_pet_id(name: str) -> str | None:
    """按名称查找精灵 ID，支持轻量模糊匹配。"""
    query = name.strip()
    if not query:
        return None
    choices = _pet_name_choices()
    if query in choices:
        return choices[query]
    result = _extract_one(query, choices.keys(), 60)
    if not result:
        return None
    return choices[result]


def find_pet(name: str) -> tuple[str, dict[str, Any]] | None:
    """按名称查找精灵。"""
    pet_id = find_pet_id(name)
    if not pet_id:
        return None
    pet = get_pet_list().get(pet_id)
    return (pet_id, pet) if isinstance(pet, dict) else None


@lru_cache(maxsize=1)
def _skill_name_choices() -> dict[str, dict[str, Any]]:
    """构建技能名称索引。"""
    choices: dict[str, dict[str, Any]] = {}
    for skill in get_skill_list().values():
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "").strip()
        if name:
            choices.setdefault(name, skill)
    for pet in get_pet_list().values():
        for field in SKILL_LIST_FIELDS:
            for skill in pet.get(field) or []:
                if not isinstance(skill, dict):
                    continue
                name = str(skill.get("name") or "").strip()
                if name:
                    choices.setdefault(name, skill)
    return choices


def find_skill(name: str) -> dict[str, Any] | None:
    """按名称查找技能。"""
    query = name.strip()
    if not query:
        return None
    choices = _skill_name_choices()
    if query in choices:
        return choices[query]
    result = _extract_one(query, choices.keys(), 70)
    if not result:
        return None
    return choices[result]


def _extract_one(query: str, choices: Any, score_cutoff: int) -> str | None:
    """从候选项中取最接近的名称。"""
    if process is not None and fuzz is not None:
        result = process.extractOne(
            query, choices, scorer=fuzz.WRatio, score_cutoff=score_cutoff
        )
        return str(result[0]) if result else None
    best_name = ""
    best_score = 0.0
    for choice in choices:
        score = SequenceMatcher(None, query, str(choice)).ratio() * 100
        if query in str(choice) or str(choice) in query:
            score = max(score, 80)
        if score > best_score:
            best_name = str(choice)
            best_score = score
    return best_name if best_score >= score_cutoff else None


def skill_names(pet: dict[str, Any]) -> set[str]:
    """获取精灵拥有的技能名称集合。"""
    names: set[str] = set()
    for field in SKILL_LIST_FIELDS:
        for skill in pet.get(field) or []:
            if isinstance(skill, dict) and skill.get("name"):
                names.add(str(skill["name"]))
    return names


def can_breed(mother: dict[str, Any], father: dict[str, Any]) -> tuple[bool, list[str]]:
    """判断两个精灵蛋组是否有交集。"""
    mother_groups = [str(item) for item in mother.get("egg_group") or [] if item]
    father_groups = [str(item) for item in father.get("egg_group") or [] if item]
    shared = [item for item in mother_groups if item in father_groups]
    return bool(shared), shared


def search_eggs(length_m: float, weight_kg: float, egg_type: str = "") -> list[str]:
    """按蛋尺寸和重量反查可能精灵。"""
    length_cm = int(round(length_m * 100))
    weight_g = int(round(weight_kg * 1000))
    results: list[str] = []
    type_name = egg_type.strip()
    for pet in get_pet_list().values():
        breeding = pet.get("breeding")
        if not isinstance(breeding, dict):
            continue
        if type_name == "炫彩" and not pet.get("talent_random_list"):
            continue
        if type_name == "同乘":
            talents = [str(item) for item in pet.get("talent_random_list") or []]
            if "同乘" not in talents:
                continue
        height_low = int(breeding.get("height_low") or 0)
        height_high = int(breeding.get("height_high") or 0)
        weight_low = int(breeding.get("weight_low") or 0)
        weight_high = int(breeding.get("weight_high") or 0)
        if (
            height_low <= length_cm <= height_high
            and weight_low <= weight_g <= weight_high
        ):
            results.append(_pet_display_name(pet))
    return results


def parse_search_criteria(raw: str) -> dict[str, str]:
    """解析查找精灵条件。"""
    criteria: dict[str, str] = {}
    for key, value in re.findall(
        r"(名字|特性|技能|生命|HP|物攻|物防|魔攻|魔防|速度|属性|蛋组)\s*[:：]\s*([^：:\s]+)",
        raw,
    ):
        criteria[key] = value.strip()
    if not criteria and raw.strip():
        criteria["名字"] = raw.strip()
    return criteria


def search_pets(
    criteria: dict[str, str], limit: int = 80
) -> list[tuple[str, dict[str, Any]]]:
    """按条件筛选精灵。"""
    results: list[tuple[str, dict[str, Any]]] = []
    for pet_id, pet in get_pet_list().items():
        if not _match_pet(pet, criteria):
            continue
        results.append((pet_id, pet))
        if len(results) >= limit:
            break
    return results


def _match_pet(pet: dict[str, Any], criteria: dict[str, str]) -> bool:
    """判断单个精灵是否符合条件。"""
    for key, value in criteria.items():
        if key == "名字":
            if value not in _pet_display_name(pet) and value not in str(
                pet.get("name") or ""
            ):
                return False
        elif key == "特性":
            feature = pet.get("feature") or {}
            if value not in str(feature.get("name") or "") and value not in str(
                feature.get("desc") or ""
            ):
                return False
        elif key == "技能":
            if not any(value in name for name in skill_names(pet)):
                return False
        elif key in ATTR_FIELDS:
            attr = pet.get("attribute") or {}
            actual = attr.get(ATTR_FIELDS[key])
            if actual is None or not _compare_number(actual, value):
                return False
        elif key == "属性":
            unit_types = [str(item) for item in pet.get("unit_type") or []]
            if value not in unit_types:
                return False
        elif key == "蛋组":
            groups = [str(item) for item in pet.get("egg_group") or []]
            if value not in groups:
                return False
    return True


def _compare_number(actual: Any, expression: str) -> bool:
    """比较数值条件，支持 >、>=、<、<= 和直接相等。"""
    try:
        number = int(actual)
    except Exception:
        return False
    expr = expression.strip()
    match = re.fullmatch(r"(>=|<=|>|<|=)?\s*(\d+)", expr)
    if not match:
        return str(number) == expr
    op = match.group(1) or "="
    expected = int(match.group(2))
    if op == ">=":
        return number >= expected
    if op == "<=":
        return number <= expected
    if op == ">":
        return number > expected
    if op == "<":
        return number < expected
    return number == expected
