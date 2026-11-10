# -*- coding: utf-8 -*-
"""把 CookLikeHOC 菜谱库（md）解析成 data/recipes.json。

来源：CookLikeHOC（https://github.com/Gar-b-age/CookLikeHOC），门店标准配方风格。
处理要点：
1. 配料清单不带克数 → 从步骤正文里反向抽取「数字+单位+食材」
2. 份量是门店批量（如 1500g 米饭）→ 折算成 2 人份，保留 original_servings
3. 品牌/供应商词在括号里 → 统一剥离
4. 营养不在这里算，由 core/nutrition.py 运行时按食材表累加
"""
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import nutrition

ZIP = "/root/uploads/wb-agentserver-86e7757a-1aa6-4aae-9922-c3cea74970a3-uploads-1794201/CookLikeHOC-main.zip"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "recipes.json")
QUEUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "review_queue.json")

QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(g|克|kg|千克|ml|毫升|L|升|个|只|根|片|瓣|朵|条|块|张|把|碗|汤匙|茶匙)")
HEAT_RE = re.compile(r"(大火|中大火|中火|中小火|小火|文火|武火)")
MIN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:分钟|min|分)")
SEC_RE = re.compile(r"(\d+)\s*秒")

# 菜系/标签推断用的词表
SPICY = ("辣椒", "辣", "花椒", "豆瓣", "剁椒", "泡椒", "小米辣")
DEEPFRY = ("炸", "油锅", "复炸")
MEAT = ("肉", "鸡", "鸭", "鱼", "虾", "排骨", "牛", "羊", "肠", "肚")
ALLERGEN_MAP = [
    ("花生", ["花生"]),
    ("坚果", ["坚果", "核桃", "腰果", "杏仁"]),
    ("蛋", ["鸡蛋", "蛋液", "鸭蛋", "咸蛋", "皮蛋"]),
    ("奶", ["牛奶", "淡奶油", "黄油", "芝士", "奶酪"]),
    ("甲壳类", ["虾", "蟹", "龙虾"]),
    ("贝类", ["蛤", "蚬", "蚝", "扇贝", "鲍"]),
    ("鱼", ["鱼", "鱼露", "鱼丸"]),
    ("大豆", ["豆腐", "豆干", "腐竹", "豆浆", "黄豆", "生抽", "老抽", "酱油", "豆瓣", "豆豉"]),
    ("麸质", ["面粉", "面条", "馒头", "面包", "饺子皮", "馄饨皮", "面筋"]),
]

CATEGORY_MAP = {
    "炒菜": "炒菜", "蒸菜": "蒸菜", "早餐": "早餐", "主食": "主食", "饮品": "饮品",
    "炸品": "炸物", "砂锅菜": "砂锅", "炖菜": "炖菜", "烫菜": "烫菜", "煮锅": "煮锅",
    "卤菜": "卤味", "汤": "汤羹", "凉拌": "凉菜", "烤类": "烤物", "配料": "酱料",
}


def clean_name(s):
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)          # markdown 链接
    prev = None
    while prev != s:                                        # 反复剥括号（可能嵌套）
        prev = s
        s = re.sub(r"[（(][^（）()]*[）)]", "", s)
    s = re.sub(r"[、，,）)]+\s*$", "", s.strip())            # 残留标点
    s = re.sub(r"^[\-\*\s]+", "", s).strip()
    return s


def split_sections(text):
    parts = re.split(r"^##\s*", text, flags=re.M)
    out = {}
    for p in parts[1:]:
        title, _, body = p.partition("\n")
        out[title.strip()] = body
    return out


def bullet_lines(block):
    return [re.sub(r"^[\-\*]\s*", "", l).strip() for l in block.splitlines()
            if re.match(r"^\s*[\-\*]\s+", l)]


def extract_ingredients(sections):
    for key in ("配料", "原料", "食材", "主料", "材料", "品类", "已知成分"):
        if key in sections:
            names = []
            for line in bullet_lines(sections[key]):
                n = clean_name(line)
                if not n or n.startswith("_") or "来自" in n:
                    continue
                names.append(n)
            if names:
                return names
    return []


def extract_steps(sections):
    for key in ("步骤", "做法", "操作", "制作"):
        if key in sections:
            steps = []
            for i, line in enumerate(bullet_lines(sections[key]), start=1):
                line = re.sub(r"^\d+[\.、)]\s*", "", line).strip()
                if not line:
                    continue
                heat = HEAT_RE.search(line)
                mins = MIN_RE.search(line)
                secs = SEC_RE.search(line)
                minutes = int(mins.group(1)) if mins else (round(int(secs.group(1)) / 60) if secs else None)
                steps.append({
                    "no": i,
                    "action": line,
                    "heat": heat.group(1) if heat else "",
                    "minutes": minutes if minutes else None,
                })
            if steps:
                return steps
    return []


def quantity_map(ings, steps):
    """从步骤正文抽取每个配料的用量。

    难点：门店 SOP 常把「汤底制作（15 份）：5600g 水、15g 盐」这种批量配方
    和「取 350g 汤底放入面碗」这种单份装配混在一起。
    策略：分句 → 标记批量句 → 非批量句优先 → 只有批量句时按份数摊分 → 荒谬值丢弃。
    """
    CLAUSE_RE = re.compile(r"[，,；;。、\n]")
    BATCH_RE = re.compile(r"[（(]\s*(\d+)\s*份\s*[）)]|汤底制作|底料制作|酱汁制作|批量|预制")
    CAPS = {"g": 1500, "克": 1500, "ml": 1500, "毫升": 1500, "kg": 3, "千克": 3,
            "L": 3, "升": 3}

    candidates = {ing: [] for ing in ings}
    for step_idx, step in enumerate(steps):
        for clause in CLAUSE_RE.split(step["action"]):
            batch_m = BATCH_RE.search(clause)
            batch_n = int(batch_m.group(1)) if (batch_m and batch_m.group(1)) else (15 if batch_m else 1)
            for m in QTY_RE.finditer(clause):
                num, unit = float(m.group(1)), m.group(2)
                before, after = clause[max(0, m.start() - 12):m.start()], clause[m.end():m.end() + 8]
                for ing in ings:
                    key = ing[:4]
                    if key and (key in before or key in after):
                        # 数量单位合理性：g 类超过上限视为批量总量
                        cap = CAPS.get(unit)
                        is_batch = bool(batch_m) or (cap is not None and num > cap)
                        candidates[ing].append({
                            "num": num, "unit": unit,
                            "batch": is_batch,
                            "batch_n": batch_n if is_batch else 1,
                            "order": step_idx,
                            "distance": abs(len(before) - before.rfind(key) - len(key)) if key in before else 1,
                        })

    result = {}
    for ing, cands in candidates.items():
        if not cands:
            continue
        singles = [c for c in cands if not c["batch"]]
        pool = singles or cands
        # 单份优先取「步骤越靠后、距离越近」的（装配句通常在后面）
        pool.sort(key=lambda c: (-c["order"], c["distance"]))
        best = pool[0]
        num, unit = best["num"], best["unit"]
        if best["batch"]:
            num = num / max(1, best["batch_n"])
        if unit in ("g", "克", "ml", "毫升") and not (0.5 <= num <= 1500):
            continue
        if unit in ("个", "只", "根", "片", "瓣", "朵", "条", "块", "张") and num > 60:
            continue
        result[ing] = (round(num, 1), unit)
    return result



def to_grams(num, unit):
    if unit in ("g", "克", "ml", "毫升"):
        return num
    if unit in ("kg", "千克", "L", "升"):
        return num * 1000
    return num * {"个": 50, "只": 50, "根": 100, "片": 5, "瓣": 5, "朵": 20,
                  "条": 200, "块": 100, "张": 8, "把": 30, "碗": 200,
                  "汤匙": 15, "茶匙": 5}.get(unit, 10)


def main():
    z = zipfile.ZipFile(ZIP)
    names = [n for n in z.namelist()
             if n.endswith(".md") and "/." not in n and not n.endswith(("README.md", "index.md"))]
    recipes, queue = [], []

    for path in sorted(names):
        dir_name = path.split("/")[1]
        if dir_name in ("docs", "docker_support", "配料"):
            continue
        text = z.read(path).decode("utf-8", "ignore")
        title_m = re.search(r"^#\s+(.+)$", text, re.M)
        title = clean_name(title_m.group(1)) if title_m else clean_name(path.split("/")[-1][:-3])
        sections = split_sections(text)
        ing_names = extract_ingredients(sections)
        steps = extract_steps(sections)
        if len(ing_names) < 3 or len(steps) < 2:
            continue

        steps_text = " ".join(s["action"] for s in steps)
        qmap = quantity_map(ing_names, steps)

        ingredients = []
        for name in ing_names:
            canonical = name
            try:
                entry = nutrition.match(name)
                if entry:
                    canonical = entry["name"]      # 归一：大麦若叶拉面 → 拉面
            except Exception:
                pass
            if name in qmap:
                num, unit = qmap[name]
                ingredients.append({"name": canonical, "amount": num, "unit": unit,
                                    "prep": "", "estimated": False})
            else:
                ingredients.append({"name": canonical, "amount": None, "unit": "",
                                    "prep": "", "estimated": True})

        missing = [i["name"] for i in ingredients if i["amount"] is None]

        # 合并同名食材
        merged, seen_idx = [], {}
        for i in ingredients:
            k = i["name"]
            if k in seen_idx and isinstance(i["amount"], (int, float)) and isinstance(merged[seen_idx[k]]["amount"], (int, float)) \
                    and merged[seen_idx[k]]["unit"] == i["unit"]:
                merged[seen_idx[k]]["amount"] = round(merged[seen_idx[k]]["amount"] + i["amount"], 1)
                continue
            seen_idx.setdefault(k, len(merged))
            merged.append(i)
        ingredients = merged

        # 抽取错位兜底：调料超过 80g 视为误抽，退回"适量"折算
        for i in ingredients:
            g = to_grams(i["amount"], i["unit"]) if isinstance(i["amount"], (int, float)) else 0
            entry = nutrition.match(i["name"])
            tags = (entry or {}).get("tags") or []
            if "调料" in tags and g > 80:
                i["amount"], i["unit"], i["estimated"] = None, "", True

        # 按"主料份量"反推份数：servings_est = 主料克数 / 每人典型量
        TYPICAL = {"主食": 120, "荤": 150, "水产": 150, "蔬菜": 200,
                   "蛋": 60, "豆制品": 150, "干货": 30}

        def typical_of(name):
            tags = (nutrition.match(name) or {}).get("tags") or []
            for t in tags:
                if t in TYPICAL:
                    return TYPICAL[t]
            # 营养表里没有的，按关键字估
            if any(k in name for k in ("肉", "鸡", "鸭", "鱼", "虾", "排骨", "牛", "羊", "肠")):
                return 150
            if any(k in name for k in ("菜", "笋", "瓜", "椒", "豆", "菇", "藕", "萝卜")):
                return 200
            if any(k in name for k in ("汁", "酱", "油", "盐", "糖", "醋", "粉")):
                return 10
            return None

        ests = []
        for i in ingredients:
            g = to_grams(i["amount"], i["unit"]) if isinstance(i["amount"], (int, float)) else 0
            if g <= 0:
                continue
            typ = typical_of(i["name"])
            if typ:
                ests.append(g / typ)
        servings_est = min(12, max(1, round(max(ests)))) if ests else 2
        original_servings = servings_est if servings_est > 2 else 2
        if servings_est > 2:
            scale = 2 / servings_est
            for i in ingredients:
                if isinstance(i["amount"], (int, float)):
                    i["amount"] = round(i["amount"] * scale, 1)

        missing = [i["name"] for i in ingredients if i["amount"] is None]

        minutes = sum(s["minutes"] for s in steps if s["minutes"])
        time_min = max(10, min(240, int(minutes * 1.2) + 10))
        difficulty = 1 if len(steps) <= 3 else (3 if (len(steps) >= 8 or any(k in steps_text for k in DEEPFRY)) else 2)
        allergens = sorted({a for a, keys in ALLERGEN_MAP if any(k in steps_text or k in " ".join(ing_names) for k in keys)})
        tags = [CATEGORY_MAP.get(dir_name, dir_name)]
        if any(k in steps_text for k in SPICY):
            tags.append("辣")
        if not any(k in " ".join(ing_names) for k in MEAT):
            tags.append("素")
        if time_min <= 20:
            tags.append("快手")

        rec = {
            "id": f"r{len(recipes)+1:03d}",
            "name": title,
            "category": CATEGORY_MAP.get(dir_name, dir_name),
            "cuisine": "家常" if dir_name not in ("汤", "蒸菜") else ("粤式" if "蒸" in dir_name else "家常"),
            "tags": tags,
            "servings": 2,
            "original_servings": original_servings,
            "time_min": time_min,
            "difficulty": difficulty,
            "ingredients": ingredients,
            "steps": steps,
            "tips": [],
            "allergens": allergens,
            "source": "import",
            "source_ref": path.replace("CookLikeHOC-main/", ""),
            "license_note": "菜谱来源：像老乡鸡那样做饭（CookLikeHOC），已结构化改写",
            "verified": False,
        }
        recipes.append(rec)
        if missing:
            queue.append({"name": title, "missing": missing})

    # 同名菜去重：保留用量信息最全的那一份
    best = {}
    for r in recipes:
        q = sum(1 for i in r["ingredients"] if isinstance(i["amount"], (int, float)))
        if r["name"] not in best or q > best[r["name"]][0]:
            best[r["name"]] = (q, r)
    recipes = [v[1] for v in best.values()]
    for idx, r in enumerate(recipes, start=1):
        r["id"] = f"r{idx:03d}"

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(recipes, f, ensure_ascii=False, indent=1)
    with open(QUEUE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=1)

    total_ing = sum(len(r["ingredients"]) for r in recipes)
    got = sum(1 for r in recipes for i in r["ingredients"] if i["amount"] is not None)
    print(f"入库菜谱 {len(recipes)} 道；配料条目 {total_ing}，抽到用量 {got}（{got/total_ing*100:.0f}%）")
    print(f"有用量缺口的菜谱 {len(queue)} 道 → tools/review_queue.json")
    cats = {}
    for r in recipes:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print("类目分布:", dict(sorted(cats.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
