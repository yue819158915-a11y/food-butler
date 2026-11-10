#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交互式人工打分：逐条填 calibration.json 的 human 字段。

    python eval/label.py              # 只问未打分的条目
    python eval/label.py --reset      # 清空已打的 human 重来
    python eval/label.py --path x.json

规则：1=完全不合适 5=非常合适，回车跳过，q 保存退出（每条已打的都会即时写盘）。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, "eval", "calibration.json")


def bar(ch="─", n=68):
    print(ch * n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=DEFAULT)
    ap.add_argument("--reset", action="store_true", help="清空 human 重新打")
    args = ap.parse_args()

    with open(args.path, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"]

    if args.reset:
        for it in items:
            it["human"] = None
        with open(args.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("已清空 human 字段\n")

    todo = [it for it in items if it.get("human") is None]
    if not todo:
        print(f"全部 {len(items)} 条都打过分了，直接运行：python eval/calibrate.py --judge")
        return 0

    print(f"共 {len(todo)} 条待打分（已打 {len(items) - len(todo)} 条）")
    print("1=完全不合适  2=不太合适  3=一般  4=比较合适  5=非常合适")
    print("提示：凭直觉独立打分，不要试图猜模型会打几分；尽量用满整个量表，全打 3 分会让 kappa 失真。\n")

    done = 0
    for it in todo:
        bar()
        print(f"[{it['id']}] {it['case']}")
        print(f"用户需求：{it['utterance']}")
        print(f"待评餐厅：{it['restaurant']}｜{it['type_raw']}｜人均 {it['price']} 元")
        print(f"点评原文：{it['note']}")
        print(f"判定标准：{it['criteria']}")
        bar("·")
        while True:
            try:
                raw = input("评分 1-5（回车跳过 / q 保存退出）：").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "q"
            if raw.lower() == "q":
                data["meta"]["labeled"] = sum(1 for it in items if it.get("human") is not None)
                with open(args.path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print("\n已保存，下次继续。")
                return 0
            if raw == "":
                break
            if raw.isdigit() and 1 <= int(raw) <= 5:
                it["human"] = int(raw)
                done += 1
                with open(args.path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                break
            print("  请输入 1-5 的整数，或直接回车跳过")

    left = sum(1 for it in items if it.get("human") is None)
    data["meta"]["labeled"] = len(items) - left
    with open(args.path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print()
    bar()
    print(f"本次打分 {done} 条，剩余未打分 {left} 条")
    if left == 0:
        print("已完成全部打分，下一步：python eval/calibrate.py --judge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
