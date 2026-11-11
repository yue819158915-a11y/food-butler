# -*- coding: utf-8 -*-
"""本地检索层：结构化过滤 + 维度分级放宽。

设计要点（对应方案 R1 / R2 / R3a）：
1. 价格判定用 price_mid（典型人均），不再用"区间是否相交"
2. 关键词由 AND 改为 OR，并按命中词数参与排序
3. 无结果时按维度的语义代价逐级放宽，而不是把所有条件一起丢掉：
   - 类目：意图标识，永不放宽
   - 关键词：偏好，最先放弃
   - 价格：可让渡，阶梯 +15% / +30% / +50%，上限 50%
   - 地点：意图约束，最后放宽一级（路段 → 区域）
4. 放宽结果以结构化对象返回，供前端如实告知；确实无解则给"最接近的 3 家"
"""
import json
import os

from .rules import RELAX_LADDER, mid_of

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "restaurants.json")
_restaurants = None

CATEGORIES = ["粤菜", "早茶", "火锅", "海鲜", "甜品", "小吃", "烧烤宵夜", "日料", "异国风味", "茶餐厅", "其他"]

MAX_PRICE_RELAX = max(RELAX_LADDER)  # 0.50

# 品类关键词：决定"想吃什么类型"，放宽时不能静默丢弃（否则"自助餐"会变成随便一家店）
STRONG_KEYWORDS = {
    "自助餐", "自助", "放题", "任食", "buffet",
    "早茶", "点心", "火锅", "打边炉", "烧烤", "烤串", "宵夜",
    "海鲜", "刺身", "日料", "寿司", "茶餐厅", "甜品", "糖水",
    "肠粉", "拉肠", "粥", "粉面", "烧鹅", "白切鸡", "牛杂", "云吞面",
    "煲仔饭", "粤菜", "潮汕", "顺德菜", "川菜", "湘菜", "牛肉火锅",
}

# 同义展开：关键词按"命中任一即算"，避免「自助餐」只匹配到写成「自助」的店
KW_ALIAS = {
    "自助餐": ["自助", "放题", "任食"],
    "自助": ["自助餐", "放题", "任食"],
    "放题": ["自助", "任食"],
    "任食": ["自助", "放题"],
    "打边炉": ["火锅"],
}


def expand_keywords(keywords):
    out = []
    for k in keywords:
        for v in [k] + KW_ALIAS.get(k, []):
            if v not in out:
                out.append(v)
    return out


def load():
    global _restaurants
    if _restaurants is None:
        with open(_DATA_PATH, encoding="utf-8") as f:
            _restaurants = json.load(f)
    return _restaurants


def reload():
    """供测试/热更新用"""
    global _restaurants
    _restaurants = None
    return load()


def locations():
    seen = []
    for r in load():
        if r["location"] and r["location"] not in seen:
            seen.append(r["location"])
    return sorted(seen)


# ---------- 单维判定 ----------

def _hit_price(r, pmin, pmax):
    """按典型人均判定。缺价格的店不参与价格过滤（不误杀）。"""
    if pmin is None and pmax is None:
        return True
    m = mid_of(r)
    if m is None:
        return True
    if pmin is not None and m < pmin:
        return False
    if pmax is not None and m > pmax:
        return False
    return True


def _hit_category(r, category):
    if not category:
        return True
    return r["category"] == category or category in (r["type_raw"] or "")


def _hit_location(r, location):
    if not location:
        return True
    loc = r["location"] or ""
    return location in loc or location == r["region"]


def count_keyword_hits(r, keywords):
    """命中多少个关键词。空关键词返回 0。"""
    if not keywords:
        return 0
    text = r["search_text"]
    return sum(1 for kw in keywords if kw and kw in text)


# 业态词：用户说了就必须满足，不能靠"命中别的词"混过去（自助餐最容易出这个问题）
MUST_KEYWORDS = {"自助", "自助餐", "放题", "任食", "buffet"}


def _hit_must(r, keywords):
    must = [k for k in keywords if k in MUST_KEYWORDS]
    if not must:
        return True
    text = r["search_text"]
    return any(k in text for k in must)


def _hit_keywords_or(r, keywords):
    """OR 语义：命中任一即通过。这是 R3a 的临时方案，
    等 M2 的标签就绪后升级为加权（见 P1-3 的 R3b）。"""
    if not keywords:
        return True
    return count_keyword_hits(r, keywords) > 0


# ---------- 排序 ----------

def _sort(items, sort, hits=None):
    hits = hits or {}

    def price_key(r):
        m = mid_of(r)
        return m if m is not None else 9999

    if sort == "cheap":
        base = lambda r: (price_key(r), -(hits.get(id(r), 0)), -r["popularity"])
    elif sort == "expensive":
        base = lambda r: (-price_key(r), -(hits.get(id(r), 0)), -r["popularity"])
    else:
        base = lambda r: (-(hits.get(id(r), 0)), -r["popularity"], price_key(r))
    return sorted(items, key=base)


# ---------- 主入口 ----------

def _run(category, location, pmin, pmax, keywords):
    out = []
    for r in load():
        if not _hit_category(r, category):
            continue
        if not _hit_location(r, location):
            continue
        if not _hit_price(r, pmin, pmax):
            continue
        if not _hit_must(r, keywords):
            continue
        if not _hit_keywords_or(r, keywords):
            continue
        out.append(r)
    return out


def _region_of_location(location):
    """把路段名映射到区域，用于地点放宽一级。"""
    for r in load():
        if location and location in (r["location"] or ""):
            return r["region"]
    return None


def _nearest(category, pmax, pmin, keywords, n=3):
    """保留硬维度（类目 + 品类词），按价格贴近程度给最接近的几家。"""
    pool = [r for r in load() if _hit_category(r, category)]
    if keywords:
        same = [r for r in pool if _hit_keywords_or(r, keywords)]
        if same:                    # 有同品类的店就只在里面挑，不要拿无关店凑数
            pool = same
    if not pool:
        return []
    target = pmax if pmax is not None else pmin
    if target is not None:
        pool.sort(key=lambda r: (abs((mid_of(r) or 9999) - target), -(mid_of(r) or 0)))
    else:
        pool.sort(key=lambda r: -(count_keyword_hits(r, keywords) * 100 + r["popularity"]))
    out = []
    for r in pool[:n]:
        m = mid_of(r)
        item = dict(r)
        if target is not None and m is not None:
            diff = m - target
            item["gap"] = (f"人均 {round(m)}，超出 {round(diff)} 元" if diff > 0
                           else f"人均 {round(m)}，低于 {round(-diff)} 元")
        else:
            item["gap"] = f"人均 {round(m)}" if m is not None else "价格未知"
        out.append(item)
    return out


def filter_restaurants(category=None, price_min=None, price_max=None, location=None,
                       keywords=None, sort=None, limit=None):
    """返回 (results, relaxed)。relaxed 为 None 表示未放宽。

    relaxed = {
      "dropped": ["keywords", "price"],   # 被放弃/放宽的维度
      "detail": "价格上限 60 → 78（+30%）",
      "original_count": 0,
      "nearest": [...]                    # 仅当确实无解时非空
    }
    """
    keywords = expand_keywords([k for k in (keywords or []) if k])

    # 第一次：严格
    strict = _run(category, location, price_min, price_max, keywords)
    if strict:
        res = _sort(strict, sort, {id(r): count_keyword_hits(r, keywords) for r in strict})
        return (res[:limit] if limit else res), None

    strong = [k for k in keywords if k in STRONG_KEYWORDS]
    weak = [k for k in keywords if k not in STRONG_KEYWORDS]
    has_price = price_max is not None or price_min is not None

    def _price_steps(keep_kw, dropped_prefix):
        steps = []
        for pct in RELAX_LADDER:
            hi2 = round(price_max * (1 + pct)) if price_max is not None else None
            lo2 = round(price_min * (1 - pct)) if price_min is not None else None
            desc = (f"价格上限 {price_max} → {hi2}（+{int(pct * 100)}%）" if hi2 is not None
                    else f"价格下限 {price_min} → {lo2}（-{int(pct * 100)}%）")
            steps.append((dict(keywords=keep_kw, price_min=lo2, price_max=hi2),
                          list(dropped_prefix) + ["price"], desc))
        return steps

    attempts = []
    # 有品类词（自助餐、肠粉、烧鹅…）时：先在价格上让步，品类一个字都不丢
    if strong and has_price:
        attempts += _price_steps(keywords, [])
    # 弱关键词可以放弃，品类词保留
    if weak:
        attempts.append((dict(keywords=strong), ["keywords"],
                         f"已忽略关键词「{'、'.join(weak)}」"))
    # 没有品类词时，才允许把关键词整体丢掉（老逻辑）
    if keywords and not strong:
        attempts.append((dict(keywords=[]), ["keywords"],
                         f"已忽略关键词「{'、'.join(keywords)}」"))
    if has_price and not strong:
        attempts += _price_steps([], ["keywords"] if keywords else [])

    region = _region_of_location(location) if location else None
    if region:
        attempts.append((
            dict(keywords=[], location=region,
                 price_max=round(price_max * (1 + MAX_PRICE_RELAX)) if price_max is not None else None,
                 price_min=round(price_min * (1 - MAX_PRICE_RELAX)) if price_min is not None else None),
            (["keywords"] if keywords else []) + (["price"] if (price_min is not None or price_max is not None) else []) + ["location"],
            f"地点从「{location}」放宽到区域「{region}」"))

    for kw, dropped, detail in attempts:
        hit = _run(category,
                   kw.get("location", location),
                   kw.get("price_min", price_min),
                   kw.get("price_max", price_max),
                   kw["keywords"])
        if hit:
            res = _sort(hit, sort, {id(r): count_keyword_hits(r, keywords) for r in hit})
            res = res[:limit] if limit else res
            return res, {
                "dropped": dropped,
                "detail": detail,
                "original_count": 0,
                "nearest": [],
            }

    # 彻底无解：给最接近的几家，并量化差距
    near = _nearest(category, price_max, price_min, keywords)
    return [], {
        "dropped": [],
        "detail": "按你的条件没有匹配的店",
        "original_count": 0,
        "nearest": near,
    }
