# -*- coding: utf-8 -*-
"""规则纯函数单测：不启动服务、不调用模型，秒级完成。

    python eval/test_rules.py

覆盖 R1c（取窗规则）与 R1b（价格解析），这两项改动在检索层评测里测不到，
因为 eval/cases.json 的 given 是写死条件、绕过了 NLU。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.rules import price_window, price_label, mid_of  # noqa: E402
from core import synonyms  # noqa: E402

FAIL = []


def check(desc, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {desc}: {got!r}" + ("" if ok else f"（期望 {want!r}）"))
    if not ok:
        FAIL.append(desc)


def main():
    print("price_window —— 向下30% / 向上15%")
    check("人均40", price_window(40), (28, 46))
    check("人均150", price_window(150), (105, 172))
    check("人均20", price_window(20), (14, 23))
    check("人均300", price_window(300), (210, 345))
    check("None 输入", price_window(None), (None, None))
    check("0 不出现负下界", price_window(1)[0] >= 0, True)

    print("\n本地词库价格解析（R1b：正则不再吞掉「人均」）")
    cases = {
        "人均40": (28, 46),
        "人均40左右": (28, 46),
        "人均40以下": (None, 40),
        "人均150以上": (150, None),
        "100以内": (None, 100),
        "人均8元": (6, 9),
    }
    for q, want in cases.items():
        c = synonyms.parse_local(q)
        check(q, (c["price_min"], c["price_max"]), want)

    print("\n不该被误伤的表述")
    for q in ("平时人多那家", "约会去哪", "晚上10点还开"):
        c = synonyms.parse_local(q)
        check(f"{q} 不产生价格条件", (c["price_min"], c["price_max"]), (None, None))

    print("\nprice_label —— 区间店标注典型值")
    check("40-70 标注约 55", price_label({"price_raw": "40-70", "price_min": 40, "price_max": 70, "price_mid": 55}), "40-70，约 55")
    check("单值不加约", price_label({"price_raw": "35", "price_min": 35, "price_max": 35, "price_mid": 35}), "35")
    check("缺失价格", price_label({"price_raw": "", "price_min": None}), "未知")

    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
