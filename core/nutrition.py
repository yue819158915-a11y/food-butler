# -*- coding: utf-8 -*-
"""营养计算：用量折算 → 按份数缩放 → 热量/三大营养素 → 健康评分。

设计原则：
1. 数值一律由食材表累加得出，绝不采信菜谱原文或 LLM 给的热量
2. "适量/少许"必须折算成克，并标记 estimated，评分按保守（上限）处理
3. 匹配不到食材的，不计入合计，但要在 missing 里如实返回
"""
import json
import os
import re

_TABLE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "ingredients_nutrition.json")
_table = None

# 单位 → 克
UNIT_G = {"g": 1, "克": 1, "kg": 1000, "千克": 1000, "ml": 1, "毫升": 1, "l": 1000, "升": 1000}
# 密度修正（ml → g）
DENSITY = {"食用油": 0.92, "猪油": 0.9, "香油": 0.92}

# "适量/少许" 的每人均值（克），按食材名关键字匹配
FUZZY_DEFAULT = [
    (("油",), 8), (("盐",), 1.5), (("糖",), 3), (("生抽", "老抽", "酱油", "蚝油"), 5),
    (("葱", "姜", "蒜", "香菜"), 5), (("淀粉", "生粉"), 5), (("料酒", "黄酒"), 5),
    (("胡椒", "花椒", "五香", "咖喱", "辣椒"), 1), (("水",), 30),
]
FUZZY_FALLBACK = 5  # 其他"适量"按 5g/人


def load_table():
    global _table
    if _table is None:
        with open(_TABLE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        items = {}
        for it in data["items"]:
            keys = [it["name"]] + it.get("aliases", [])
            items[it["name"]] = it
            for k in keys:
                items.setdefault(k, it)
        _table = items
    return _table


def match(name):
    """食材名 → 营养表条目。先精确、再包含匹配（取最长命中）。"""
    if not name:
        return None
    table = load_table()
    name = name.strip()
    if name in table:
        return table[name]
    best, best_len = None, 0
    for key, it in table.items():
        if len(key) < 2:
            continue
        if key in name or name in key:
            if len(key) > best_len:
                best, best_len = it, len(key)
    return best


def _fuzzy_grams(name, people):
    for keys, g in FUZZY_DEFAULT:
        if any(k in name for k in keys):
            return g * max(1, people), True
    return FUZZY_FALLBACK * max(1, people), True


def to_grams(ing, people=2):
    """返回 (克数, 是否估算, 是否可信)。无法折算时返回 (None, True, False)。"""
    name = ing.get("name", "")
    amount = ing.get("amount")
    unit = (ing.get("unit") or "").strip().lower()

    entry = match(name)
    if amount is None or str(amount) in ("适量", "少许", "若干", ""):
        g, est = _fuzzy_grams(name, people)
        return g, est, True

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        g, est = _fuzzy_grams(name, people)
        return g, est, True

    if unit in UNIT_G:
        g = amount * UNIT_G[unit]
        dens = DENSITY.get(entry["name"]) if entry else None
        if dens and unit in ("ml", "毫升"):
            g *= dens
        return g, False, True

    # 个/只/根/片/碗… → 查每单位克数
    if entry:
        dg = (entry.get("default_unit_g") or {})
        if unit in dg:
            return amount * dg[unit], False, True
        if len(dg) == 1:  # 只有一种单位时兜底
            return amount * list(dg.values())[0], True, True
    # 无单位默认按克
    if unit in ("", "份", "个", "只", "根", "片", "瓣", "朵", "条", "块", "张", "把", "碗", "汤匙", "茶匙"):
        return amount, True, True
    return None, True, False


def compute(recipe, servings=None):
    """按份数计算营养。返回 dict(per_serving, total, missing, confidence)。"""
    base = recipe.get("servings") or 2
    target = servings or base
    ratio = target / base if base else 1

    total = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0, "sodium_mg": 0.0, "fiber_g": 0.0}
    missing, estimated = [], 0
    for ing in recipe.get("ingredients", []):
        entry = match(ing.get("name", ""))
        grams, est, ok = to_grams(ing, people=base)
        if est:
            estimated += 1
        if grams is None or entry is None:
            missing.append(ing.get("name"))
            continue
        ratio_edible = entry.get("edible_ratio", 1.0)
        factor = grams * ratio_edible / 100.0
        for k in total:
            total[k] += entry.get(k, 0) * factor

    per = {k: round(v / max(1, target), 1) for k, v in total.items()}
    conf = "high"
    if missing or estimated > len(recipe.get("ingredients", [])) * 0.4:
        conf = "medium"
    if missing and len(missing) >= max(2, len(recipe.get("ingredients", [])) // 3):
        conf = "low"
    return {
        "servings": target,
        "per_serving": per,
        "total": {k: round(v, 1) for k, v in total.items()},
        "missing": missing,
        "estimated_items": estimated,
        "confidence": conf,
        "scale_ratio": round(ratio, 2),
    }


def health_score(nut):
    """每份评分的启发式口径（满分 100），非医学结论。"""
    p = nut.get("per_serving") or {}
    kcal = p.get("kcal") or 0
    protein = p.get("protein_g") or 0
    fat = p.get("fat_g") or 0
    sodium = p.get("sodium_mg") or 0
    fiber = p.get("fiber_g") or 0

    factors = []
    score = 100

    # 油盐（25 分）：钠 >1200mg 或脂肪供能 >45%
    fat_energy = (fat * 9 / kcal * 100) if kcal else 0
    if sodium > 1500:
        score -= 12; factors.append(f"钠偏高（{round(sodium)}mg/份）")
    elif sodium > 900:
        score -= 6; factors.append(f"钠略高（{round(sodium)}mg/份）")
    if fat_energy > 50:
        score -= 13; factors.append(f"脂肪供能占 {round(fat_energy)}%")
    elif fat_energy > 40:
        score -= 7; factors.append(f"脂肪供能占 {round(fat_energy)}%")

    # 蛋白（20 分）
    if protein < 8:
        score -= 10; factors.append("蛋白质偏少")
    elif protein > 45:
        score -= 5; factors.append("蛋白质偏多")

    # 纤维（15 分）
    if fiber < 1:
        score -= 8; factors.append("膳食纤维很少")
    elif fiber < 2.5:
        score -= 4

    # 热量（20 分）
    if kcal > 900:
        score -= 12; factors.append(f"单份热量偏高（{round(kcal)}kcal）")
    elif kcal > 700:
        score -= 6; factors.append(f"单份热量偏高（{round(kcal)}kcal）")

    score = max(30, min(100, round(score)))
    grade = "A" if score >= 80 else ("B" if score >= 60 else "C")
    if not factors:
        factors.append("油盐与热量较均衡")
    return {"score": score, "grade": grade, "factors": factors,
            "fat_energy_pct": round(fat_energy), "disclaimer": "启发式估算，非医学建议"}


def scale(recipe, servings):
    """按份数等比缩放食材用量（数值型才缩放，文字型保留）。"""
    import copy
    r = copy.deepcopy(recipe)
    base = recipe.get("servings") or 2
    ratio = servings / base if base else 1
    for ing in r.get("ingredients", []):
        amt = ing.get("amount")
        if isinstance(amt, (int, float)):
            ing["amount"] = round(amt * ratio, 1)
    r["servings"] = servings
    r["nutrition"] = compute(recipe, servings)
    r["health"] = health_score(r["nutrition"])
    return r
