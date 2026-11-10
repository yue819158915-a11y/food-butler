# -*- coding: utf-8 -*-
"""多约束推荐：把「时间 / 预算 / 人数 / 忌口 / 健康目标 / 天气 / 口味」合成一次排序。

硬约束（永不放宽）：过敏原、忌口
软约束（打分，可让渡）：时间、预算、健康目标、天气契合、口味偏好、新鲜度
"""
from . import nutrition, recipes

# 粗粒度成本估算（元/100g），用于预算约束；仅作数量级参考
PRICE_PER_100G = {
    "主食": 0.8, "蔬菜": 1.2, "荤": 4.0, "水产": 6.0, "蛋": 1.4, "豆制品": 1.2,
    "调料": 2.0, "坚果": 6.0, "干货": 8.0, "奶": 1.5, "水果": 2.5, "其他": 1.5,
}

# 天气/时段 → 菜谱偏好
WARM_CATS = ("汤羹", "砂锅", "炖菜", "煮锅", "蒸菜")
COOL_CATS = ("凉菜", "饮品")


def _price_of(name):
    entry = nutrition.match(name) or {}
    tags = entry.get("tags") or []
    for t in tags:
        if t in PRICE_PER_100G:
            return PRICE_PER_100G[t]
    for kw, p in (("肉", 4.0), ("鸡", 3.5), ("鱼", 6.0), ("虾", 7.0), ("菜", 1.2), ("豆腐", 1.2)):
        if kw in name:
            return p
    return 1.5


def cost_per_serving(recipe, servings=2):
    total = 0.0
    for ing in recipe.get("ingredients", []):
        grams, _, _ = nutrition.to_grams(ing, people=servings)
        if not grams:
            continue
        total += grams / 100.0 * _price_of(ing.get("name", ""))
    return round(total / max(1, recipe.get("servings", servings)), 1)


def _weather_score(recipe, mood):
    cat = recipe.get("category") or ""
    if mood in ("cold", "rainy"):
        return 1.0 if cat in WARM_CATS else 0.2
    if mood in ("hot", "muggy"):
        return 1.0 if cat in COOL_CATS else (0.6 if cat in ("蒸菜", "凉菜") else 0.3)
    return 0.6


def _goal_score(recipe, goal):
    nut = recipe["nutrition"]["per_serving"]
    kcal, protein, carb = nut.get("kcal", 0), nut.get("protein_g", 0), nut.get("carb_g", 0)
    if goal == "减脂":
        return 1.0 if kcal <= 450 else (0.6 if kcal <= 600 else 0.2)
    if goal == "增肌":
        return 1.0 if protein >= 28 else (0.6 if protein >= 20 else 0.2)
    if goal == "控糖":
        return 1.0 if carb <= 45 else (0.6 if carb <= 65 else 0.2)
    return 0.7


def plan(ctx=None, n=3, meal=None):
    """ctx: {people, max_time, budget, avoid[], goal, taste{}}；meal: breakfast/lunch/... 覆盖时段

    返回 (cards, meta)。cards 里每项含 recipe 摘要 + reason。
    """
    ctx = ctx or {}
    people = ctx.get("people") or 2
    max_time = ctx.get("max_time")
    budget = ctx.get("budget")
    avoid = [a for a in (ctx.get("avoid") or []) if a]
    goal = ctx.get("goal")
    taste = ctx.get("taste") or {}

    env = ctx.get("env") or {}
    mood = env.get("mood") or "mild"
    period_key = (meal or (env.get("period") or {}).get("key") or "dinner")

    pool = []
    dropped = {"allergen": 0, "time": 0, "budget": 0}
    for r in recipes.load():
        if avoid and recipes.has_allergen(r, avoid):
            dropped["allergen"] += 1
            continue
        if max_time and (r.get("time_min") or 99) > max_time:
            dropped["time"] += 1
            continue
        cost = cost_per_serving(r, people)
        if budget and cost > budget:
            dropped["budget"] += 1
            continue
        r["_cost"] = cost
        pool.append(r)

    # 池子被清空时，只放宽时间与预算（过敏原绝不放宽）
    relaxed_note = None
    if not pool:
        pool = [r for r in recipes.load() if not (avoid and recipes.has_allergen(r, avoid))]
        for r in pool:
            r["_cost"] = cost_per_serving(r, people)
        relaxed_note = "时间或预算太紧，已放宽这两项（过敏原与忌口未放宽）"

    scored = []
    for r in pool:
        health = (r.get("health") or {}).get("score") or 60
        s = 0.35 * (health / 100)
        s += 0.20 * _weather_score(r, mood)
        s += 0.15 * _goal_score(r, goal)
        if max_time:
            s += 0.10 * max(0.0, 1 - (r.get("time_min") or 30) / max(1, max_time))
        if budget:
            s += 0.10 * max(0.0, 1 - r["_cost"] / max(1, budget))
        hit = sum(w for tag, w in (taste or {}).items() if tag in (r.get("tags") or []) or tag == r.get("category"))
        s += 0.10 * min(1.0, hit)
        # 营养可信度：估算项越多越降权，避免"炸鸡面 123kcal"这类误导读分
        conf = (r.get("nutrition") or {}).get("confidence")
        if conf == "low":
            s -= 0.18
        elif conf == "medium":
            s -= 0.08
        # 时段偏好
        if period_key in ("breakfast",) and r.get("category") in ("早餐", "蒸菜", "主食"):
            s += 0.08
        if period_key == "supper" and r.get("category") in ("炒菜", "炸物", "凉菜"):
            s += 0.05
        scored.append((s, r))

    scored.sort(key=lambda x: -x[0])

    # 品类轮转：前三家尽量不同类目
    picks, used_cat = [], set()
    for s, r in scored:
        if len(picks) >= n:
            break
        cat = r.get("category")
        if cat in used_cat and len(picks) < min(n, 3):
            continue
        picks.append((s, r))
        used_cat.add(cat)
    if len(picks) < n:
        for s, r in scored:
            if len(picks) >= n:
                break
            if r not in [p[1] for p in picks]:
                picks.append((s, r))

    cards = []
    for s, r in picks[:n]:
        nut = r["nutrition"]["per_serving"]
        reason = _reason(r, mood, goal, max_time, budget, r["_cost"])
        cards.append({
            "recipe": recipes.summary(r),
            "cost_per_serving": r["_cost"],
            "kcal": nut.get("kcal"), "protein_g": nut.get("protein_g"),
            "reason": reason,
            "score": round(s, 3),
        })
    return cards, {
        "relaxed_note": relaxed_note,
        "dropped": dropped,
        "pool": len(pool),
        "assumptions": _assumptions(ctx, people, max_time, budget, goal),
    }


def _reason(r, mood, goal, max_time, budget, cost):
    bits = []
    if mood in ("cold", "rainy") and r.get("category") in WARM_CATS:
        bits.append("热乎暖身")
    if mood in ("hot", "muggy") and r.get("category") in COOL_CATS:
        bits.append("清爽解暑")
    if goal == "减脂" and (r["nutrition"]["per_serving"].get("kcal") or 999) <= 450:
        bits.append("低卡")
    if goal == "增肌" and (r["nutrition"]["per_serving"].get("protein_g") or 0) >= 28:
        bits.append("高蛋白")
    if max_time and (r.get("time_min") or 99) <= max_time * 0.7:
        bits.append(f"{r.get('time_min')}分钟快手")
    if budget and cost <= budget * 0.7:
        bits.append("省钱")
    grade = (r.get("health") or {}).get("grade")
    if grade == "A":
        bits.append("营养均衡")
    return "、".join(bits[:3]) or "综合评分靠前"


def _assumptions(ctx, people, max_time, budget, goal):
    out = []
    if not ctx.get("people"):
        out.append(f"按 {people} 人份")
    if not max_time:
        out.append("不限时间")
    if not budget:
        out.append("不限预算（成本为估算）")
    if not goal:
        out.append("按均衡饮食")
    if not ctx.get("avoid"):
        out.append("未设置忌口/过敏原，如有请告知")
    return out
