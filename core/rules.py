# -*- coding: utf-8 -*-
"""共用规则：价格窗口、典型人均。

为什么单独抽一个模块：
原先 prompt 里写一套价格规则、synonyms 里写另一套，两边会漂移
（"人均40"在 LLM 模式是 20-60、在词库模式直接丢条件）。
这里只保留一份定义，供 nlu / synonyms / store / 前端说明共用。
"""

# 价格窗口：用户说"人均X"时，下界 0.7X、上界 1.15X
PRICE_DOWN = 0.30
PRICE_UP = 0.15

# 放宽阶梯：0% → +15% → +30% → +50%
RELAX_LADDER = (0.15, 0.30, 0.50)


def price_window(x, down=PRICE_DOWN, up=PRICE_UP):
    """'人均X' → (下界, 上界)。下界不小于 0。

    >>> price_window(40)
    (28, 46)
    >>> price_window(150)
    (105, 172)
    """
    if x is None:
        return (None, None)
    try:
        x = float(x)
    except (TypeError, ValueError):
        return (None, None)
    lo = max(0, round(x * (1 - down)))
    hi = round(x * (1 + up))
    return (lo, hi)


def mid_of(r):
    """典型人均。优先用 price_mid 字段，缺失则回退 price_min。"""
    m = r.get("price_mid")
    if m is not None:
        return m
    a, b = r.get("price_min"), r.get("price_max")
    if a is None:
        return None
    return a if b is None else (a + b) / 2.0


def price_label(r):
    """展示用价格文案：区间店标注典型值，避免和筛选条件看起来矛盾。"""
    raw = (r.get("price_raw") or "").strip()
    a, b = r.get("price_min"), r.get("price_max")
    if a is None:
        return raw or "未知"
    if b is None or a == b:
        return raw or str(a)
    m = mid_of(r)
    return f"{raw}，约 {round(m)}" if m is not None else raw
