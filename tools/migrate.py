# -*- coding: utf-8 -*-
"""一次性迁移：给 data/restaurants.json 补 price_mid / price_floor 字段。

    python tools/migrate.py            # 执行（自动备份 .bak）
    python tools/migrate.py --dry      # 只看会改什么，不写盘

幂等：已有 price_mid 的条目跳过，可重复执行。
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "data", "restaurants.json")
BAK = os.path.join(ROOT, "data", "restaurants.v1.bak.json")


def compute_mid(r):
    a, b = r.get("price_min"), r.get("price_max")
    if a is None:
        return None
    if b is None:
        return float(a)
    return round((a + b) / 2.0, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    with open(DATA, encoding="utf-8") as f:
        items = json.load(f)

    changed = []
    for r in items:
        if r.get("price_mid") is not None:
            continue
        mid = compute_mid(r)
        new = dict(r)
        new["price_mid"] = mid
        new["price_floor"] = r.get("price_min")
        if mid is not None and r.get("price_min") != r.get("price_max"):
            changed.append((r["name"], r.get("price_raw"), mid))
        r.update(price_mid=mid, price_floor=r.get("price_min"))

    total_missing = sum(1 for r in items if r.get("price_mid") is None and r.get("price_min") is not None)
    print(f"共 {len(items)} 家；本次为 {len(changed)} 家区间店计算典型人均")
    for name, raw, mid in changed:
        print(f"  {name[:26]:<28}{raw:<10}-> {mid}")
    if total_missing:
        print(f"警告：{total_missing} 家价格缺失，price_mid = null（不参与价格过滤）")

    span = sorted(changed, key=lambda x: -abs(x[2] - 0))[:0]
    wide = [(n, raw, mid) for n, raw, mid in changed if mid > 150]
    if wide:
        print("\n跨度/高档位建议人工复核（中点可能低估实际人均）：")
        for n, raw, mid in wide:
            print(f"  {n[:26]:<28}{raw:<10}-> {mid}")

    if args.dry:
        print("\n--dry：未写盘")
        return 0

    if not os.path.exists(BAK):
        shutil.copy2(DATA, BAK)
        print(f"\n已备份到 {os.path.relpath(BAK, ROOT)}")

    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    print(f"已写入 {os.path.relpath(DATA, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
