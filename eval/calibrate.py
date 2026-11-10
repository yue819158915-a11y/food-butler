#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LLM 裁判校准：计算二次加权 Cohen's kappa。

    python eval/calibrate.py            # 计算（需先人工填 human）
    python eval/calibrate.py --judge    # 先跑裁判打分，再计算（需 ZHIPU_API_KEY）

判定门槛：二次加权 kappa >= 0.6 才可把 LLM 裁判分当作趋势指标；
0.4~0.6 只能当参考，<0.4 说明裁判不可用，必须退回人工黄金集。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PATH = os.path.join(ROOT, "eval", "calibration.json")


def weighted_kappa(a, b, k=5, power=2):
    """a, b 为等长的 1..k 评分序列，返回二次加权 kappa"""
    n = len(a)
    if n == 0:
        return None
    idx = range(1, k + 1)
    O = {(i, j): 0 for i in idx for j in idx}
    for x, y in zip(a, b):
        O[(x, y)] += 1
    h = {i: sum(O[(i, j)] for j in idx) for i in idx}
    v = {j: sum(O[(i, j)] for i in idx) for j in idx}
    Po = Pe = 0.0
    for i in idx:
        for j in idx:
            w = 1.0 - ((i - j) ** power) / ((k - 1) ** power)
            Po += w * O[(i, j)] / n
            Pe += w * (h[i] * v[j]) / (n * n)
    if Pe == 1:
        return 1.0
    return (Po - Pe) / (1 - Pe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", action="store_true", help="先调用 LLM 裁判打分")
    args = ap.parse_args()

    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"]

    if args.judge:
        key = os.environ.get("ZHIPU_API_KEY")
        if not key:
            print("未设置 ZHIPU_API_KEY")
            return 1
        sys.path.insert(0, os.path.join(ROOT, "eval"))
        from run import _judge_one  # noqa: E402
        for it in items:
            case = {"utterance": it["utterance"], "judge_criteria": it["criteria"]}
            fake = [{"name": it["restaurant"], "type_raw": it["type_raw"],
                     "location": "", "price_raw": it["price"], "note": it["note"]}]
            score, _ = _judge_one(case, fake, key)
            it["judge"] = round(score, 2) if score is not None else None
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("裁判打分完成")

    pairs = [(it["human"], it["judge"]) for it in items
             if it.get("human") is not None and it.get("judge") is not None]
    if not pairs:
        miss_h = sum(1 for it in items if it.get("human") is None)
        miss_j = sum(1 for it in items if it.get("judge") is None)
        print(f"校准样本 {len(items)} 条：待人工标注 {miss_h} 条，待裁判打分 {miss_j} 条")
        print("→ 打开 eval/calibration.json，把 human 填成 1-5，再运行：")
        print("  python eval/calibrate.py --judge")
        return 0

    a = [int(round(x)) for x, _ in pairs]
    b = [int(round(y)) for _, y in pairs]
    kappa = weighted_kappa(a, b)
    print(f"样本 {len(pairs)} 条，二次加权 kappa = {kappa:.3f}")
    if kappa >= 0.6:
        print("结论：裁判可用，可作为软语义趋势指标")
    elif kappa >= 0.4:
        print("结论：一致性偏低，只能做参考，不建议进 CI 门槛")
    else:
        print("结论：裁判不可用，退回人工黄金集或修改判定标准")
    return 0


if __name__ == "__main__":
    sys.exit(main())
