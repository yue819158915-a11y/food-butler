#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检索层评测：确定性断言 + 可选 LLM 裁判。

用法：
    python eval/run.py                     # 只跑硬约束（零成本、秒级）
    python eval/run.py --judge             # 追加软语义 LLM 裁判（需 ZHIPU_API_KEY）
    python eval/run.py --save              # 把本次结果写成基线快照 eval/baseline.json
    python eval/run.py --compare           # 与基线快照对比，输出 delta
    python eval/run.py --gate              # CI 模式：条目级违反率超阈值则退出码 1

设计原则：
1. 断言按用户意图写，不复用 store 的内部判定函数（避免拿实现验证实现）
2. 条目级违反率（每条结果是否合规）与行为级通过率（无解时是否该返回 0）分开统计
3. 严格执行用例与放宽用例分开统计——放宽用例本来就不满足约束，混算会掩盖问题
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import store  # noqa: E402

CASES_PATH = os.path.join(ROOT, "eval", "cases.json")
BASE_PATH = os.path.join(ROOT, "eval", "baseline.json")
LIMIT = 100


# ---------- 谓词求值 ----------

def ctx_of(r):
    """构造断言可用的变量表。注意这里独立计算 price_mid，不调用 store。"""
    a, b = r.get("price_min"), r.get("price_max")
    mid = None if a is None else (a if b is None else (a + b) / 2.0)
    return {
        "price_mid": mid,
        "price_min": a,
        "price_max": b,
        "category": r.get("category"),
        "location": r.get("location") or "",
        "region": r.get("region") or "",
        "type_raw": r.get("type_raw") or "",
        "name": r.get("name") or "",
        "note": r.get("note") or "",
        "popularity": r.get("popularity") or 0,
    }


def check_one(r, exprs):
    """返回 (是否合规, 失败谓词或 None, 失败原因)"""
    c = ctx_of(r)
    for e in exprs:
        try:
            ok = eval(e, {"__builtins__": {}}, c)  # noqa: S307 用例为本仓库自有 JSON
        except Exception as ex:
            return False, e, f"无法求值：{type(ex).__name__}"
        if not ok:
            return False, e, ""
    return True, None, ""


# ---------- LLM 裁判 ----------

def _judge_one(case, results, key):
    """对 soft 用例的前 5 条逐条独立打分（打乱顺序、temperature=0）"""
    import random
    import requests

    top = results[:5]
    if not top:
        return None, []
    order = list(range(len(top)))
    random.shuffle(order)
    scores, detail = [], []
    for i in order:
        r = top[i]
        prompt = (
            f"用户需求：「{case['utterance']}」\n"
            f"待评估餐厅：{r['name']}｜{r['type_raw']}｜{r['location']}｜人均{r['price_raw']}元\n"
            f"点评原文：{r['note']}\n\n"
            f"判定标准：{case['judge_criteria']}\n"
            "只输出一个 1-5 的整数，不要任何解释。"
        )
        try:
            resp = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                json={"model": "glm-4-flash", "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30)
            resp.raise_for_status()
            txt = resp.json()["choices"][0]["message"]["content"].strip()
            digit = "".join(ch for ch in txt if ch.isdigit())[:1]
            if digit:
                s = int(digit)
                if 1 <= s <= 5:
                    scores.append(s)
                    detail.append((r["name"], s))
        except Exception as ex:
            detail.append((r["name"], f"err:{type(ex).__name__}"))
    return (sum(scores) / len(scores) if scores else None), detail


# ---------- 输出 ----------

def dw(s):
    """显示宽度：中日韩字符按 2 计"""
    return sum(2 if ord(ch) > 127 else 1 for ch in str(s))


def pad(s, n):
    s = str(s)
    return s + " " * max(0, n - dw(s))


def run(cases, with_judge=False):
    key = os.environ.get("ZHIPU_API_KEY") or ""
    rows = []
    for c in cases:
        results, relaxed = store.filter_restaurants(limit=LIMIT, **(c.get("given") or {}))
        must = c.get("must") or []
        bad, reasons = [], []
        if must:
            for r in results:
                ok, expr, why = check_one(r, must)
                if not ok:
                    bad.append(r)
                    reasons.append((r["name"], r.get("price_raw"), expr, why))
        row = {
            "id": c["id"],
            "direction": c.get("direction", ""),
            "returned": len(results),
            "violations": len(bad),
            "rate": (len(bad) / len(results)) if results and must else None,
            "relaxed": bool(relaxed),
            "relaxed_detail": (relaxed or {}).get("detail", ""),
            "relaxed_dropped": (relaxed or {}).get("dropped", []),
            "nearest": len((relaxed or {}).get("nearest") or []),
            "has_must": bool(must),
            "expect_zero": bool(c.get("expect_zero")),
            "expect_min": c.get("expect_min"),
            "reasons": reasons[:5],
        }
        # 告知完整性：放宽了就必须说清楚放宽了什么，不许静默
        if relaxed:
            ok = bool(row["relaxed_detail"]) and (bool(row["relaxed_dropped"]) or row["nearest"] > 0)
            row["disclosure"] = "PASS" if ok else "FAIL"
        else:
            row["disclosure"] = "-"
        # 行为级判定
        if c.get("expect_zero"):
            row["behavior"] = "PASS" if len(results) == 0 else "FAIL"
        elif c.get("expect_min") is not None:
            row["behavior"] = "PASS" if len(results) >= c["expect_min"] else "FAIL"
        else:
            row["behavior"] = "-"
        if with_judge and c.get("soft") and key:
            score, detail = _judge_one(c, results, key)
            row["judge"] = score
            row["judge_detail"] = detail
        rows.append(row)
    return rows


def summarize(rows):
    strict = [r for r in rows if r["has_must"] and not r["relaxed"]]
    allm = [r for r in rows if r["has_must"]]
    s_ret = sum(r["returned"] for r in strict)
    s_vio = sum(r["violations"] for r in strict)
    a_ret = sum(r["returned"] for r in allm)
    a_vio = sum(r["violations"] for r in allm)
    beh = [r for r in rows if r["behavior"] != "-"]
    soft = [r for r in rows if r.get("judge") is not None]
    return {
        "strict_returned": s_ret,
        "strict_violations": s_vio,
        "strict_rate": (s_vio / s_ret) if s_ret else None,
        "overall_returned": a_ret,
        "overall_violations": a_vio,
        "overall_rate": (a_vio / a_ret) if a_ret else None,
        "behavior_pass": sum(1 for r in beh if r["behavior"] == "PASS"),
        "behavior_total": len(beh),
        "disclosure_pass": sum(1 for r in rows if r.get("disclosure") == "PASS"),
        "disclosure_total": sum(1 for r in rows if r.get("disclosure") in ("PASS", "FAIL")),
        "nearest_total": sum(r.get("nearest", 0) for r in rows),
        "soft_mean": (sum(r["judge"] for r in soft) / len(soft)) if soft else None,
        "relaxed_cases": sum(1 for r in rows if r["relaxed"]),
    }


def pct(v):
    return "  n/a" if v is None else f"{v * 100:5.1f}%"


def print_table(rows):
    print(pad("用例", 22) + pad("方向", 10) + pad("返回", 6) + pad("违反", 6) + pad("违反率", 9)
          + pad("放宽", 6) + pad("行为", 6) + "裁判")
    print("-" * 84)
    for r in rows:
        line = (pad(r["id"], 22) + pad(r["direction"], 10) + pad(r["returned"], 6)
                + pad(r["violations"], 6) + pad(pct(r["rate"]), 9)
                + pad("是" if r["relaxed"] else "-", 6) + pad(r["behavior"], 6))
        if r.get("judge") is not None:
            line += f"{r['judge']:.2f}/5"
        elif r.get("judge_detail"):
            line += "失败"
        print(line)


def print_summary(s):
    print()
    print("严格执行用例（未触发放宽）")
    print(f"  返回 {s['strict_returned']} 条，违反明示约束 {s['strict_violations']} 条，"
          f"条目级违反率 {pct(s['strict_rate']).strip()}")
    print("全部硬约束用例（含放宽）")
    print(f"  返回 {s['overall_returned']} 条，违反 {s['overall_violations']} 条，"
          f"条目级违反率 {pct(s['overall_rate']).strip()}")
    print(f"行为级断言通过 {s['behavior_pass']}/{s['behavior_total']}"
          f"（无解兜底 / 最少结果数）")
    print(f"放宽告知完整 {s['disclosure_pass']}/{s['disclosure_total']}"
          f"（放宽必须说明放宽了什么）")
    print(f"触发放宽的用例 {s['relaxed_cases']} 个")
    if s["soft_mean"] is not None:
        print(f"软语义裁判均分 {s['soft_mean']:.2f}/5")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", action="store_true", help="追加软语义 LLM 裁判（需 ZHIPU_API_KEY）")
    ap.add_argument("--save", action="store_true", help="写入基线快照")
    ap.add_argument("--compare", action="store_true", help="与基线快照对比")
    ap.add_argument("--gate", action="store_true", help="CI 模式，超阈值退出码 1")
    ap.add_argument("--max-violation", type=float, default=0.03, help="gate 阈值，默认 3%%")
    args = ap.parse_args()

    with open(CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    if args.judge and not os.environ.get("ZHIPU_API_KEY"):
        print("！未设置 ZHIPU_API_KEY，跳过软语义裁判（硬约束不受影响）\n")

    rows = run(cases, with_judge=args.judge)
    print_table(rows)
    s = summarize(rows)
    print_summary(s)

    if args.compare and os.path.exists(BASE_PATH):
        with open(BASE_PATH, encoding="utf-8") as f:
            base = json.load(f)
        print()
        print("与基线对比")
        bmap = {r["id"]: r for r in base.get("cases", [])}
        for r in rows:
            b = bmap.get(r["id"])
            if not b:
                print(f"  + {r['id']}（新增）")
                continue
            d_ret = r["returned"] - b["returned"]
            d_vio = r["violations"] - b["violations"]
            if d_ret or d_vio:
                print(f"  {r['id']}: 返回 {b['returned']}→{r['returned']} (Δ{d_ret:+d}), "
                      f"违反 {b['violations']}→{r['violations']} (Δ{d_vio:+d})")
        bs = base.get("summary", {})
        if bs.get("strict_rate") is not None and s["strict_rate"] is not None:
            print(f"  严格违反率: {bs['strict_rate'] * 100:.1f}% → {s['strict_rate'] * 100:.1f}%")

    if args.save:
        snap = {"created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "summary": s, "cases": rows}
        with open(BASE_PATH, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        print(f"\n基线已写入 {os.path.relpath(BASE_PATH, ROOT)}")

    if args.gate and s["strict_rate"] is not None and s["strict_rate"] > args.max_violation:
        print(f"\nGATE FAILED：严格违反率 {s['strict_rate'] * 100:.1f}% > "
              f"{args.max_violation * 100:.0f}%")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
