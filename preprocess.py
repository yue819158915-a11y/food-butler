# -*- coding: utf-8 -*-
"""一次性预处理：xlsx -> data/restaurants.json"""
import json
import re
import openpyxl

SRC = "/root/uploads/1793879380029130687-四季度精选美食推荐v4.0.0 (1).xlsx"
OUT = "data/restaurants.json"

# 52 种原始类型 -> 11 个一级类目
TYPE_MAP = {
    "粤菜": "粤菜", "粤菜/炖汤": "粤菜", "炖汤": "粤菜", "东江菜": "粤菜",
    "湛江菜": "粤菜", "潮汕菜": "粤菜", "广州/潮汕菜": "粤菜", "顺德菜": "粤菜",
    "顺德私房菜": "粤菜", "私房菜": "粤菜", "融合菜": "粤菜", "啫啫煲": "粤菜",
    "烧鹅": "粤菜", "鲮鱼": "粤菜", "水乡菜": "粤菜", "乳鸽": "粤菜",
    "全虫宴": "粤菜", "河鲜": "粤菜", "农家乐": "粤菜", "农庄": "粤菜", "番禺菜": "粤菜",
    "早茶": "早茶", "早茶/粤菜": "早茶",
    "潮汕牛肉火锅": "火锅", "牛肉火锅": "火锅", "清水火锅": "火锅",
    "自助涮涮锅": "火锅", "寿喜烧": "火锅", "椰子鸡": "火锅",
    "海鲜": "海鲜", "粤菜海鲜": "海鲜",
    "甜品": "甜品", "甜品小吃": "甜品", "甜品雪糕": "甜品",
    "小吃": "小吃", "小吃/夜宵": "小吃", "竹升面": "小吃", "快餐小店": "小吃",
    "烧烤": "烧烤宵夜", "烤肉": "烧烤宵夜", "晚餐/宵夜": "烧烤宵夜",
    "回转寿司": "日料", "天妇罗": "日料", "居酒屋": "日料", "炸猪扒": "日料", "拉面": "日料",
    "东南亚菜": "异国风味", "土耳其菜": "异国风味", "地中海菜": "异国风味",
    "茶餐厅": "茶餐厅",
    "鲁菜": "其他",
}

REGION_RULES = [
    ("顺德", ["顺德"]),
    ("东莞", ["东莞"]),
    ("番禺", ["南村万博", "员岗", "市桥", "石楼", "石壁", "化龙", "低涌", "南村坑头", "观龙岛", "茭塘", "兴业大道", "汉溪长隆", "正佳/万博"]),
    ("老城区", ["北京路", "西华路", "宝华路", "上下九", "十八甫", "陈家祠", "公园前", "农讲所", "永庆坊", "文明路", "德政中路", "惠福西", "龙津东", "荔枝湾", "荔湾路", "文化公园", "长堤", "海珠广场", "越秀南", "小北", "海幢", "海珠北路", "光明北路", "淘金", "西丽路"]),
    ("市中心商圈", ["珠江新城", "体育东", "天河南", "天河北", "冼村", "猎德", "林和中", "石牌桥", "五羊邨", "冼村路南"]),
    ("海珠", ["滨江中", "滨江东", "东晓路", "客村", "晓港", "江南西", "泰沙路", "艺苑南路", "磨碟沙", "琶洲", "万胜围", "上渡路", "鹭江", "海幢", "中医药大学"]),
    ("天河东部", ["东圃", "大沙地", "鹤边"]),
]


def region_of(loc: str) -> str:
    for region, keys in REGION_RULES:
        if any(k in loc for k in keys):
            return region
    return "其他区域"


def parse_price(raw):
    s = str(raw).strip()
    m = re.search(r"(\d+)\s*[-–~至]\s*(\d+)", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+)", s)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None


def popularity(note: str) -> int:
    score = 0
    if "人气推荐" in note:
        score += 3
    if "华工" in note:
        score += 2
    if "米其林" in note:
        score += 2
    for kw in ("必点", "招牌", "隐藏", "惊艳", "划算", "便宜"):
        if kw in note:
            score += 1
    return score


def main():
    wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))
    items, unmapped = [], []
    for r in rows[1:]:
        name, typ, loc, price, note = r[0], r[1], r[2], r[3], r[4]
        if not name:
            continue
        typ_raw = str(typ).strip() if typ else ""
        cat = TYPE_MAP.get(typ_raw)
        if cat is None:
            unmapped.append(typ_raw)
            cat = "其他"
        note = str(note).strip() if note else ""
        pmin, pmax = parse_price(price)
        items.append({
            "id": len(items) + 1,
            "name": str(name).strip(),
            "type_raw": typ_raw,
            "category": cat,
            "location": str(loc).strip() if loc else "",
            "region": region_of(str(loc).strip() if loc else ""),
            "price_raw": str(price).strip() if price is not None else "",
            "price_min": pmin,
            "price_max": pmax,
            "note": note,
            "popularity": popularity(note),
            "search_text": f"{name} {typ_raw} {loc} {note}",
        })
    if unmapped:
        print("警告：未映射类型 ->", set(unmapped))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    cats = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    print(f"共 {len(items)} 家；类目分布: {cats}")
    print(f"价格缺失: {sum(1 for it in items if it['price_min'] is None)}")


if __name__ == "__main__":
    main()
