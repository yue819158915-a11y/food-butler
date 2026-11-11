# -*- coding: utf-8 -*-
"""粤语/口语同义词表：LLM 不可用时的本地降级解析"""

# 词条 -> (筛选条件字段, 值)
CATEGORY_WORDS = {
    "饮茶": "早茶", "喝茶": "早茶", "早茶": "早茶", "点心": "早茶", "一盅两件": "早茶", "叹早茶": "早茶",
    "宵夜": "烧烤宵夜", "夜宵": "烧烤宵夜", "深夜": "烧烤宵夜", "打边炉": "火锅", "火锅": "火锅",
    "牛肉火锅": "火锅", "寿喜烧": "火锅", "椰子鸡": "火锅",
    "海鲜": "海鲜", "食海鲜": "海鲜", "吃海鲜": "海鲜", "鱼生": "海鲜",
    "甜品": "甜品", "糖水": "甜品", "雪糕": "甜品", "吃甜的": "甜品", "甜品小吃": "甜品",
    "小吃": "小吃", "食小吃": "小吃", "拉面": "日料", "寿司": "日料", "日料": "日料",
    "日本菜": "日料", "居酒屋": "日料",
    "烧烤": "烧烤宵夜", "撸串": "烧烤宵夜", "烤肉": "烧烤宵夜",
    "粤菜": "粤菜", "顺德菜": "粤菜", "潮汕菜": "粤菜", "白切鸡": "粤菜", "烧鹅": "粤菜",
    "炖汤": "粤菜", "老火汤": "粤菜", "乳鸽": "粤菜", "啫啫煲": "粤菜",
    "茶餐厅": "茶餐厅", "东南亚": "异国风味", "土耳其": "异国风味",
}

LOCATION_WORDS = {
    "西华路": "西华路", "珠江新城": "珠江新城", "北京路": "北京路", "宝华路": "宝华路",
    "淘金": "淘金", "南村万博": "南村万博", "五羊邨": "五羊邨", "陈家祠": "陈家祠",
    "上下九": "上下九", "琶洲": "琶洲", "市桥": "市桥", "新造": "新造", "滨江": "滨江中",
    "长堤": "长堤", "顺德": "顺德", "东莞": "东莞", "东晓路": "东晓路",
}

# 低价/口语价格表述 -> price_max
# 注意：不要放单字「平」，它会让「平时人多那家」被判成 price_max=60
CHEAP_WORDS = {"平靓正": 60, "便宜": 60, "实惠": 60, "平价": 60, "平啲": 60, "平d": 60,
               "学生党": 50, "穷鬼": 50, "预算少": 50}
# 「约会」是场景不是价格档，已移除（否则本地模式下"约会去哪"会被筛成 150 以上）
EXPENSIVE_WORDS = {"贵": 200, "高档": 200, "高端": 200, "商务": 200, "请客": 200}

import re

from .rules import price_window

_NOT_LIMIT = ("超过", "以上", "起码", "至少")


# 用户说法 -> 数据里的写法。只收录已确认关键词匹配不到的变体。
FEATURE_WORDS = {
    "自助餐": "自助", "自助": "自助", "放题": "自助", "涮涮锅": "自助",
}


def parse_local(message: str):
    """本地规则解析，返回 conditions dict（字段同 LLM 输出 schema）"""
    msg = message.lower().replace("，", ",").replace("。", ".")
    conditions = {"category": None, "keywords": [], "price_min": None,
                  "price_max": None, "location": None, "sort": None}

    for word, cat in CATEGORY_WORDS.items():
        if word in message:
            conditions["category"] = cat
            break

    for word, loc in LOCATION_WORDS.items():
        if word in message:
            conditions["location"] = loc
            break

    for word, pm in CHEAP_WORDS.items():
        if word in msg:
            conditions["price_max"] = pm
            break
    for word, pm in EXPENSIVE_WORDS.items():
        if word in msg:
            if conditions["price_min"] is None:
                conditions["price_min"] = pm
            break

    # 数字价格：区分「上限 / 下限 / 档位」三种语义
    # 旧实现把「人均」也吃进匹配里，导致取前缀拿到空串，"人均40" 解析成无条件
    m = re.search(
        r"(人均|不超过|唔超过|不超|最多|低于|起码|至少)?\s*(\d{1,4})\s*(?:元|块|蚊)?\s*(以内|以下|左右|上下|以上|起步)?",
        message)
    if m:
        prefix, num_s, suffix = m.group(1), m.group(2), m.group(3)
        num = int(num_s)
        if 0 < num < 2000:
            if suffix in ("以内", "以下") or prefix in ("不超过", "唔超过", "不超", "最多", "低于"):
                conditions["price_max"] = min(conditions["price_max"] or 9999, num)
            elif suffix in ("以上", "起步") or prefix in ("起码", "至少"):
                conditions["price_min"] = max(conditions["price_min"] or 0, num)
            elif prefix == "人均" or suffix in ("左右", "上下"):
                lo, hi = price_window(num)
                conditions["price_min"] = lo
                conditions["price_max"] = hi

    # 排序
    if re.search(r"便宜|最平|低价|性价比", message):
        conditions["sort"] = "cheap"
    elif re.search(r"人气|最火|推荐最多|爆款", message):
        conditions["sort"] = "popular"

    # 关键词：提取引号内的词作为菜品关键词
    kw = re.findall(r"[「『\"“]([^」』\"”]{2,12})[」』\"”]", message)
    # 特征词：不是菜系，而是"店的性质"，数据里也没有对应类目，只能落到 search_text 上匹配。
    # 用短词，因为数据里是「自助涮涮锅」「全牛自助牛肉火锅」这类写法。
    for word, alias in FEATURE_WORDS.items():
        if word in message and alias not in kw:
            kw.append(alias)
    conditions["keywords"] = kw
    return conditions
