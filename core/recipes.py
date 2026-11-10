# -*- coding: utf-8 -*-
"""菜谱检索层：加载 + 营养预计算 + 条件过滤"""
import json
import os

from . import nutrition

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "recipes.json")
_recipes = None
_index = None


def load():
    """加载并一次性算好营养（185 道，毫秒级），避免每次请求重复算"""
    global _recipes, _index
    if _recipes is None:
        with open(_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        for r in raw:
            r["nutrition"] = nutrition.compute(r)
            r["health"] = nutrition.health_score(r["nutrition"])
        _recipes = raw
        _index = {r["id"]: r for r in raw}
    return _recipes


def get(recipe_id, servings=None):
    load()
    r = _index.get(recipe_id)
    if not r:
        return None
    if servings and servings != r.get("servings", 2):
        return nutrition.scale(r, servings)
    return r


def summary(r):
    """列表用摘要，不返回完整步骤"""
    nut = r.get("nutrition", {}).get("per_serving", {})
    return {
        "id": r["id"], "name": r["name"], "category": r.get("category"),
        "tags": r.get("tags", []), "time_min": r.get("time_min"),
        "difficulty": r.get("difficulty"), "servings": r.get("servings", 2),
        "allergens": r.get("allergens", []),
        "kcal": nut.get("kcal"), "protein_g": nut.get("protein_g"),
        "health": r.get("health", {}).get("grade"), "score": r.get("health", {}).get("score"),
        "confidence": (r.get("nutrition") or {}).get("confidence"),
        "verified": r.get("verified", False),
        "ingredients_preview": [i["name"] for i in r.get("ingredients", [])][:5],
    }


def has_allergen(r, avoid):
    if not avoid:
        return False
    return any(a in r.get("allergens", []) for a in avoid)


def search(q=None, tags=None, max_time=None, max_difficulty=None, avoid=None,
           cuisine=None, limit=50, goal=None):
    """条件检索。返回 (results, meta)。q 为菜名/食材模糊匹配。"""
    load()
    out = []
    for r in _recipes:
        if avoid and has_allergen(r, avoid):
            continue
        if max_time and (r.get("time_min") or 99) > max_time:
            continue
        if max_difficulty and (r.get("difficulty") or 9) > max_difficulty:
            continue
        if cuisine and cuisine not in (r.get("cuisine") or ""):
            continue
        if tags and not all(t in r.get("tags", []) or t == r.get("category") for t in tags):
            continue
        if q:
            hay = r["name"] + " " + " ".join(i["name"] for i in r.get("ingredients", []))
            if not all(part in hay for part in q.split()):
                continue
        if goal:
            nut = r["nutrition"]["per_serving"]
            if goal == "减脂" and (nut.get("kcal", 999) > 550 or (r["nutrition"].get("per_serving", {}).get("fat_g", 0) * 9 / max(1, nut.get("kcal", 1))) > 0.42):
                continue
            if goal == "增肌" and nut.get("protein_g", 0) < 22:
                continue
            if goal == "控糖" and nut.get("carb_g", 999) > 65:
                continue
        out.append(r)

    out.sort(key=lambda r: (-(r.get("health", {}).get("score") or 0), r.get("time_min") or 99))
    return [summary(r) for r in out[:limit]], {"total": len(out), "returned": len(out[:limit])}


def full(r):
    """详情：完整食材/步骤/营养/评分"""
    return {
        **{k: r.get(k) for k in ("id", "name", "category", "cuisine", "tags", "servings",
                                 "time_min", "difficulty", "verified", "source", "license_note")},
        "ingredients": r.get("ingredients", []),
        "steps": r.get("steps", []),
        "tips": r.get("tips", []),
        "allergens": r.get("allergens", []),
        "nutrition": r.get("nutrition"),
        "health": r.get("health"),
    }
