# -*- coding: utf-8 -*-
"""回复生成：普通话输出；支持筛选推荐 / 自由对话两种模式；LLM 优先，模板降级"""
from . import nlu
from . import store as _store
from .rules import price_label


def _summary(r, note_len=90):
    note = (r["note"] or "").strip()
    if len(note) > note_len:
        note = note[:note_len] + "…"
    gap = f"｜{r['gap']}" if r.get("gap") else ""
    return f"{r['name']}（{r['type_raw']}｜{r['location']}｜人均{price_label(r)}元{gap}）—— {note}"


def _relax_line(relaxed):
    """把结构化 relaxed 渲染成一句人话，前端和话术共用同一条信息。"""
    if not relaxed:
        return ""
    detail = relaxed.get("detail") or ""
    if relaxed.get("nearest"):
        if detail and detail != "按你的条件没有匹配的店":
            return f"按你的条件 0 家（{detail}）。"
        return "按你的条件 0 家。"
    return f"没有完全符合的店，已放宽：{detail}。"


def generate_reply(message, restaurants, conditions, header_key=None, relaxed=None, total=None):
    """筛选推荐语。relaxed 为结构化对象或 None。返回 (reply, source)"""
    cfg = nlu.load_config()
    key = nlu.resolve_key(header_key)
    total = total if total is not None else len(restaurants)
    cond_desc = describe_conditions(conditions)
    lead = _relax_line(relaxed)

    # 彻底无解：给最接近的几家，不编造
    if total == 0:
        near = (relaxed or {}).get("nearest") or []
        if near:
            head = lead or "按你的条件没有完全匹配的店。"
            lines = "；".join(f"{r['name']}（{r.get('gap', '')}）" for r in near[:3])
            return f"{head}最接近的是：{lines}。要不要把条件放松一点？", "template"
        return ("暂时没找到符合条件的餐厅～ 试试放宽一下？比如不限价格，"
                "或者换个类目（早茶、火锅、海鲜、甜品都可以）。"), "template"

    top = restaurants[:8]
    if key:
        try:
            lines = "\n".join(_summary(r) for r in top)
            relax_desc = f"\n注意：{lead}回复开头必须如实说明放宽了什么，不要假装是严格匹配的结果。\n" if relaxed else ""
            prompt = (
                f"用户问：「{message}」\n解析出的条件：{cond_desc}\n"
                f"共找到 {total} 家，以下是候选（最多8家）：\n{lines}\n{relax_desc}\n"
                "请用轻松口语化的简体中文（普通话，不要用粤语词，可带少量 emoji）回复："
                "先一句话概括筛选条件与总数，再重点推荐 2~3 家（说明人均和亮点）。"
                "注意：只能使用上面候选里出现的信息；营业时间、预约、地址、电话等若候选里没写就不要提，严禁编造。"
                f"候选只有 {total} 家时，就只介绍这 {'1' if total == 1 else total} 家，不要说「还有一家」「另外几家」这类话。"
                "结尾可以问是否要看更多或换条件。控制在 180 字内。"
            )
            content = nlu.call_llm(
                [{"role": "user", "content": prompt}], key,
                cfg.get("llm_base_url", "https://open.bigmodel.cn/api/paas/v4"),
                cfg.get("llm_model", "glm-4-flash"),
                temperature=0.7, json_mode=False, timeout=12)
            return content.strip(), "llm"
        except Exception:
            pass

    # 模板降级（普通话）
    best = top[0]
    others = "、".join(r["name"] for r in top[1:4])
    reply = (f"按「{cond_desc}」找到 {total} 家 👉 首推【{best['name']}】：{best['type_raw']}，"
             f"位于{best['location']}，人均 {price_label(best)} 元。{(best['note'] or '')[:80]}")
    if lead:
        reply = f"{lead}\n{reply}"
    if others:
        reply += f"\n其他不错的选择：{others}。想看完整列表可以调一调右侧筛选面板～"
    return reply, "template"


def chat_reply(message, history=None, header_key=None):
    """自由对话模式：闲聊、追问推荐理由、店铺细节等。返回 (reply, source)"""
    cfg = nlu.load_config()
    key = nlu.resolve_key(header_key)
    if not key:
        return ("这个问题我需要 AI 才能聊得来～ 目前可以先继续用筛选功能，"
                "比如「晚餐人均40以下」「顺德菜哪家好」这类问题我都能秒答。"), "template"
    try:
        # 上下文：人气榜前 25 家 + 最近推荐过的店
        context_lines = [_summary(r, 70) for r in _store.filter_restaurants(sort="popular", limit=25)[0]]
        system = (
            "你是「美食小助手」，帮广州本地食客找好吃的。请始终用轻松口语化的简体中文（普通话，不要用粤语词）回答。\n"
            "下面是餐厅数据库摘要（共111家，此处展示人气前25）：\n" + "\n".join(context_lines) + "\n\n"
            "规则：回答推荐理由、菜品、人均、地点等要以上面数据为准，不要编造数据外的餐厅信息；"
            "数据没有的信息（如具体地址门牌、距离）要坦白说明并建议用户自行核实；可以自然闲聊，语气亲切。回复控制在200字内。"
        )
        msgs = [{"role": "system", "content": system}]
        for h in (history or [])[-6:]:
            text = h.get("text")
            if text:
                msgs.append({"role": h.get("role") or "user", "content": str(text)})
        msgs.append({"role": "user", "content": message})
        content = nlu.call_llm(
            msgs, key,
            cfg.get("llm_base_url", "https://open.bigmodel.cn/api/paas/v4"),
            cfg.get("llm_model", "glm-4-flash"),
            temperature=0.7, json_mode=False, timeout=12)
        return content.strip(), "llm"
    except Exception:
        return ("AI 刚才走神了，再问一次试试？也可以直接用筛选条件问我，"
                "比如「晚餐人均40以下，适合老人家的清淡菜」。"), "template"


def describe_conditions(c):
    if not c:
        return "全部餐厅"
    parts = []
    if c.get("category"):
        parts.append(f"类目={c['category']}")
    if c.get("location"):
        parts.append(f"地点={c['location']}")
    if c.get("price_min") is not None or c.get("price_max") is not None:
        lo = c.get("price_min")
        hi = c.get("price_max")
        parts.append(f"人均 {lo if lo is not None else 0}~{hi if hi is not None else '不限'} 元")
    if c.get("keywords"):
        parts.append(f"关键词={'、'.join(c['keywords'])}")
    if c.get("sort"):
        parts.append(f"排序={c['sort']}")
    return "，".join(parts) if parts else "全部餐厅"


# ============================ 做菜推荐话术 ============================

def cook_reply(message, picks, ctx=None, header_key=None, meta=None):
    """picks: [{recipe:{...}, kcal, protein_g, reason}] → (reply, source)"""
    cfg = nlu.load_config()
    key = nlu.resolve_key(header_key)
    if not picks:
        return ("没找到符合条件的菜谱。试试放宽时间或预算？过敏原和忌口我不会放宽。"), "template"

    lines = []
    for p in picks[:3]:
        r = p["recipe"]
        nut = {"kcal": p.get("kcal"), "protein_g": p.get("protein_g")}
        lines.append(f"{r['name']}（{r['category']}｜{r['time_min']}分钟｜难度{r['difficulty']}｜"
                     f"约{r['kcal']}kcal｜蛋白{r['protein_g']}g｜人均成本约{p.get('cost_per_serving')}元）——{p.get('reason')}")

    if key:
        try:
            env = (ctx or {}).get("env") or {}
            prompt = (
                f"用户说：「{message}」\n"
                f"今天环境：{env.get('headline') or '无天气数据'}；条件：{_ctx_desc(ctx)}\n"
                f"候选菜谱：\n" + "\n".join(lines) + "\n\n"
                "请用亲切、务实的普通话（像懂做饭也懂营养的朋友）回复：先用一句话说明为什么这几道适合他，"
                "再逐道给 1 句亮点（火候/口感/省时），最后问一句要不要看详细步骤或换一道。"
                "只能使用上面给出的信息，不要编造热量、时间或食材；总字数 180 字内。"
            )
            content = nlu.call_llm([{"role": "user", "content": prompt}], key,
                                   cfg.get("llm_base_url", "https://open.bigmodel.cn/api/paas/v4"),
                                   cfg.get("llm_model", "glm-4-flash"),
                                   temperature=0.6, json_mode=False, timeout=12)
            return content.strip(), "llm"
        except Exception:
            pass

    head = "按你的条件，我挑了这几道："
    body = "\n".join(f"· {p['recipe']['name']}（{p['recipe']['time_min']}分钟，约{p['kcal']}kcal）" for p in picks[:3])
    tail = "\n点开菜谱卡能看食材用量和步骤。想换口味或调时间，直接说。"
    if meta and meta.get("relaxed_note"):
        tail = f"\n（{meta['relaxed_note']}）" + tail
    return head + "\n" + body + tail, "template"


def _ctx_desc(ctx):
    ctx = ctx or {}
    bits = []
    if ctx.get("people"):
        bits.append(f"{ctx['people']}人")
    if ctx.get("max_time"):
        bits.append(f"{ctx['max_time']}分钟内")
    if ctx.get("budget"):
        bits.append(f"人均{ctx['budget']}元内")
    if ctx.get("goal"):
        bits.append(ctx["goal"])
    if ctx.get("avoid"):
        bits.append("忌口：" + "、".join(ctx["avoid"]))
    return "、".join(bits) or "无特别限制"
