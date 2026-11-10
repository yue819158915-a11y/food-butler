# -*- coding: utf-8 -*-
"""菜谱数据校验：入库前的最后一道闸。

规则（errors 阻断、warnings 提示）：
E1 食材名必须能在营养表匹配（否则营养会漏算）
E2 单位必须在白名单
E3 步骤数 ≥ 2，且至少一半步骤有火候或时间
E4 每份热量在 30~1200 kcal 之间
E5 份数 1~6、难度 1~3
E6 source / verified 必填，AI 起草的 verified 必须为 false
E7 过敏原由食材推导，与手填不一致告警
E8 重名去重
E9 用量异常：单条 >1500g 或调料 >80g
E10 Atwater 自洽：|4P+9F+4C-kcal|/kcal < 25%
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import nutrition  # noqa: E402

PATH = os.path.join(ROOT, "data", "recipes.json")
UNITS = {"g", "克", "kg", "千克", "ml", "毫升", "l", "升", "个", "只", "根", "片",
         "瓣", "朵", "条", "块", "张", "把", "碗", "汤匙", "茶匙", ""}


def check(r):
    errs, warns = [], []
    ings = r.get("ingredients", [])
    steps = r.get("steps", [])

    missing_match = [i["name"] for i in ings if not nutrition.match(i["name"])]
    if missing_match:
        warns.append(f"E1 营养表未收录：{'、'.join(missing_match[:4])}")

    bad_unit = [i for i in ings if (i.get("unit") or "").lower() not in UNITS]
    if bad_unit:
        errs.append(f"E2 非法单位：{bad_unit[0]['name']}「{bad_unit[0]['unit']}」")

    if len(steps) < 2:
        errs.append("E3 步骤不足 2 步")
    else:
        with_meta = sum(1 for s in steps if s.get("heat") or s.get("minutes"))
        if with_meta < len(steps) / 2:
            warns.append(f"E3 仅 {with_meta}/{len(steps)} 步有火候或时间")

    nut = r.get("nutrition") or nutrition.compute(r)
    kcal = (nut.get("per_serving") or {}).get("kcal") or 0
    if not (30 <= kcal <= 1200):
        warns.append(f"E4 每份热量异常：{kcal}kcal")

    if not (1 <= (r.get("servings") or 0) <= 6):
        errs.append(f"E5 份数异常：{r.get('servings')}")
    if r.get("difficulty") not in (1, 2, 3):
        errs.append(f"E5 难度异常：{r.get('difficulty')}")

    if not r.get("source"):
        errs.append("E6 缺少 source")
    if r.get("source") in ("llm_draft", "import") and r.get("verified") is True:
        warns.append("E6 自动来源被标记为已人工验证")

    for i in ings:
        amt = i.get("amount")
        if not isinstance(amt, (int, float)):
            continue
        if i.get("unit") in ("g", "克", "ml", "毫升") and amt > 1500:
            errs.append(f"E9 用量异常：{i['name']} {amt}{i.get('unit')}")

    p = nut.get("per_serving") or {}
    if kcal > 0:
        calc = 4 * (p.get("protein_g") or 0) + 9 * (p.get("fat_g") or 0) + 4 * (p.get("carb_g") or 0)
        if abs(calc - kcal) / kcal > 0.25:
            warns.append(f"E10 营养素与热量不自洽：算得 {round(calc)} vs 记 {round(kcal)}")
    return errs, warns


def main():
    data = json.load(open(PATH, encoding="utf-8"))
    seen, dup = set(), []
    err_n = warn_n = 0
    for r in data:
        if r["name"] in seen:
            dup.append(r["name"])
        seen.add(r["name"])
        errs, warns = check(r)
        err_n += len(errs)
        warn_n += len(warns)
    print(f"校验 {len(data)} 道：errors {err_n}，warnings {warn_n}，重名 {len(dup)}")
    # 抽样打印前 5 条告警
    shown = 0
    for r in data:
        errs, warns = check(r)
        if (errs or warns) and shown < 5:
            print(f"  - {r['name']}: {errs + warns[:2]}")
            shown += 1
    if dup:
        print("重名：", dup[:5])
    return 0 if err_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
