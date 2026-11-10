# -*- coding: utf-8 -*-
"""把新增餐厅批次合并进 data/restaurants.json（幂等：按餐厅名去重）

用法：python tools/merge_v3.py
新增数据来源：tools/new_batch_v3.json（由《广州美食推荐总表》中「来源=网络搜集」的 98 行导出）
"""
import json
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "restaurants.json")
BATCH = os.path.join(ROOT, "tools", "new_batch_v3.json")
BACKUP = os.path.join(ROOT, "data", "restaurants.v2.bak.json")

# 新增类型 -> 11 个一级类目
TYPE_MAP = {
    "早茶/粤菜": "早茶", "早茶/园林酒家": "早茶", "粤菜/早茶": "早茶", "早茶": "早茶",
    "粤菜": "粤菜", "粤菜/鸡煲": "粤菜", "粤菜大排档": "粤菜", "顺德菜": "粤菜",
    "潮汕菜": "粤菜", "潮汕粥": "粤菜", "烧鹅": "粤菜", "烧腊": "粤菜",
    "农家菜": "粤菜", "农庄": "粤菜", "羊肉煲": "粤菜", "大排档/鱼": "粤菜",
    "海鲜": "海鲜", "海鲜/粤菜": "海鲜", "粤菜/海鲜": "海鲜", "海鲜加工": "海鲜",
    "牛肉火锅": "火锅", "川味火锅": "火锅", "重庆火锅": "火锅", "粥水火锅": "火锅",
    "椰子鸡": "火锅", "牛杂煲": "火锅",
    "甜品": "甜品", "甜品/港式": "甜品", "奶茶": "甜品",
    "云吞面": "小吃", "粥品/肠粉": "小吃", "粥粉面": "小吃", "粥品": "小吃",
    "肠粉": "小吃", "煲仔饭": "小吃", "粉面": "小吃", "牛杂": "小吃",
    "小吃": "小吃", "小吃/鱼皮": "小吃", "快餐": "小吃", "湘菜/小吃": "小吃",
    "粤菜/烧烤": "烧烤宵夜", "烤鱼": "烧烤宵夜",
    "日料": "日料",
    "西餐": "异国风味", "中东菜": "异国风味", "越南菜": "异国风味", "泰国菜": "异国风味",
    "茶餐厅": "茶餐厅",
    "川菜": "其他", "自助餐": "其他",
}

# 新表的行政区 -> 现有 region 方案
REGION_MAP = {
    "越秀": "老城区", "荔湾": "老城区", "天河": "市中心商圈", "海珠": "海珠",
    "番禺": "番禺", "佛山": "顺德", "东莞": "东莞", "连锁": "其他区域",
    "白云": "其他区域", "花都": "其他区域", "黄埔": "其他区域",
    "增城": "其他区域", "南沙": "其他区域", "其他": "其他区域",
}


def category_of(t: str) -> str:
    if t in TYPE_MAP:
        return TYPE_MAP[t]
    if any(k in t for k in ("早茶", "点心")):
        return "早茶"
    if any(k in t for k in ("火锅", "煲")):
        return "火锅"
    if any(k in t for k in ("海鲜", "鱼生", "水产")):
        return "海鲜"
    if any(k in t for k in ("甜品", "糖水", "奶")):
        return "甜品"
    if any(k in t for k in ("烤", "烧", "宵夜", "夜宵")):
        return "烧烤宵夜"
    if any(k in t for k in ("寿司", "日料", "拉面", "天妇罗")):
        return "日料"
    if "茶餐厅" in t:
        return "茶餐厅"
    if any(k in t for k in ("粤菜", "潮汕", "顺德", "农家", "农庄", "粥", "鸡")):
        return "粤菜"
    return "其他"


def parse_price(raw: str):
    """返回 (price_min, price_max, price_mid, price_floor)。'300以上' 这类开口区间取 300 起。"""
    s = (raw or "").strip().replace("～", "-").replace("~", "-")
    m = re.search(r"(\d+)\s*[-—至]\s*(\d+)", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return lo, hi, round((lo + hi) / 2, 1), lo
    m = re.search(r"(\d+)\s*以上", s)
    if m:
        lo = int(m.group(1))
        return lo, None, float(lo), lo
    m = re.search(r"(\d+)", s)
    if m:
        v = int(m.group(1))
        return v, v, float(v), v
    return None, None, None, None


def popularity(note: str) -> int:
    score = 0
    if "人气推荐" in note:
        score += 3
    if "华工" in note:
        score += 2
    if "米其林" in note:
        score += 2
    for kw in ("必点", "招牌", "隐藏", "惊艳", "划算", "便宜", "老字号", "排队"):
        if kw in note:
            score += 1
    return score


def main():
    data = json.load(open(DATA, encoding="utf-8"))
    batch = json.load(open(BATCH, encoding="utf-8"))
    if not os.path.exists(BACKUP):
        shutil.copy(DATA, BACKUP)
        print(f"已备份 -> {os.path.basename(BACKUP)}")

    existing = {r["name"] for r in data}
    added, skipped = [], []
    for item in batch:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        if name in existing:
            skipped.append(name)
            continue
        note = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", (item.get("note") or "").strip())
        type_raw = (item.get("type") or "").strip()
        loc = (item.get("location") or "").strip()
        region = REGION_MAP.get((item.get("region") or "").strip(), "其他区域")
        pmin, pmax, pmid, pfloor = parse_price(item.get("price"))
        added.append({
            "id": 0,
            "name": name,
            "type_raw": type_raw,
            "category": category_of(type_raw),
            "location": loc,
            "region": region,
            "price_raw": (item.get("price") or "").strip(),
            "price_min": pmin,
            "price_max": pmax,
            "note": note,
            "popularity": popularity(note),
            "search_text": f"{name} {type_raw} {loc} {note}",
            "price_mid": pmid,
            "price_floor": pfloor,
        })
        existing.add(name)

    merged = data + added
    for i, r in enumerate(merged, start=1):
        r["id"] = i
    json.dump(merged, open(DATA, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    cats = {}
    for r in merged:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    missing_price = sum(1 for r in merged if r["price_min"] is None)
    print(f"原有 {len(data)} 家，新增 {len(added)} 家，合并后 {len(merged)} 家")
    if skipped:
        print(f"跳过重名 {len(skipped)} 条: {skipped}")
    print(f"类目分布: {dict(sorted(cats.items(), key=lambda x: -x[1]))}")
    print(f"价格缺失: {missing_price}")


if __name__ == "__main__":
    main()
