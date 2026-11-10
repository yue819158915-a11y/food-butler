# -*- coding: utf-8 -*-
"""美食小助手 — FastAPI 入口（单端口：静态页面 + API）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import daily, generator, nlu, planner, profile, recipes, store

app = FastAPI(title="美食小助手", docs_url=None, redoc_url=None)

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# 启动探针：验证发布环境是否保留 data/（结论会回显在 /api/health）
try:
    profile.write_probe("startup")
except Exception:
    pass


class ChatRequest(BaseModel):
    message: str
    history: list = []


@app.get("/api/health")
def health():
    return {"ok": True, "restaurants": len(store.load()), "probe": profile.probe_status()}


@app.get("/api/meta")
def meta():
    return {
        "categories": store.CATEGORIES,
        "locations": store.locations(),
        "has_server_key": bool(nlu.resolve_key()),
        "llm_model": nlu.load_config().get("llm_model", "glm-4-flash"),
        "total": len(store.load()),
    }


@app.get("/api/restaurants")
def list_restaurants(category: str = None, price_min: int = None, price_max: int = None,
                     location: str = None, q: str = None, sort: str = None, limit: int = 50):
    keywords = [k.strip() for k in (q or "").split() if k.strip()] if q else []
    results, relaxed = store.filter_restaurants(
        category=category, price_min=price_min, price_max=price_max,
        location=location, keywords=keywords, sort=sort, limit=min(limit, 100))
    # 无解时把"最接近的几家"一并返回，否则面板会显示空白
    nearest = (relaxed or {}).get("nearest") or []
    return {"total": len(results), "relaxed": relaxed,
            "restaurants": results if results else nearest}


# ---------------- 菜谱 / 情境推荐 ----------------

@app.get("/api/recipes")
def list_recipes(q: str = None, tags: str = None, max_time: int = None,
                 avoid: str = None, goal: str = None, limit: int = 30):
    avoid_list = [a.strip() for a in (avoid or "").split(",") if a.strip()]
    items, meta = recipes.search(q=q, tags=[t for t in (tags or "").split(",") if t],
                                 max_time=max_time, avoid=avoid_list, goal=goal,
                                 limit=min(limit, 100))
    return {"total": meta["total"], "recipes": items}


@app.get("/api/recipes/{rid}")
def recipe_detail(rid: str, servings: int = None):
    r = recipes.get(rid, servings=servings)
    if not r:
        return JSONResponse(status_code=404, content={"error": "菜谱不存在"})
    return recipes.full(r)


@app.get("/api/recommend")
def recommend(people: int = 2, max_time: int = None, budget: int = None,
              avoid: str = None, goal: str = None, n: int = 3):
    """开屏推荐：天气/时段 + 3 道菜"""
    env = daily.context()
    avoid_list = [a.strip() for a in (avoid or "").split(",") if a.strip()]
    cards, meta = planner.plan(
        {"people": people, "max_time": max_time, "budget": budget,
         "avoid": avoid_list, "goal": goal, "env": env}, n=n)
    for c in cards:
        c["full"] = recipes.full(recipes.get(c["recipe"]["id"]))
    return {"ok": True, "env": env, "cards": cards, "meta": meta}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request = None,
               x_llm_key: str = Header(default=None, alias="X-LLM-Key")):
    message = (req.message or "").strip()
    if not message:
        return JSONResponse(status_code=400, content={"error": "message 不能为空"})
    try:
        # 做菜意图预分流（独立 prompt，不动餐厅检索链路，避免评测回归）
        if nlu.COOK_HINTS.search(message):
            conds, nlu_source = nlu.parse_cook_intent(message, req.history, header_key=x_llm_key)
            env = daily.context()
            ctx = {**conds, "env": env}
            cards, pmeta = planner.plan(ctx, n=3)
            reply, gen_source = generator.cook_reply(message, cards, ctx, header_key=x_llm_key, meta=pmeta)
            for c in cards:
                c["full"] = recipes.full(recipes.get(c["recipe"]["id"]))
            return {
                "mode": "cook",
                "conditions": conds,
                "nlu_source": nlu_source,
                "reply": reply,
                "gen_source": gen_source,
                "env": env,
                "recipes": cards,
                "assumptions": pmeta.get("assumptions"),
                "relaxed": ({"detail": pmeta["relaxed_note"]} if pmeta.get("relaxed_note") else None),
                "total": len(cards),
            }

        conditions, nlu_source = nlu.parse_intent(message, req.history, header_key=x_llm_key)

        # 对话模式：闲聊 / 追问推荐理由 / 店铺细节
        if conditions.get("mode") == "chat":
            reply, gen_source = generator.chat_reply(message, req.history, header_key=x_llm_key)
            return {
                "conditions": None,
                "nlu_source": nlu_source,
                "reply": reply,
                "gen_source": gen_source,
                "mode": "chat",
                "relaxed": False,
                "total": 0,
                "restaurants": [],
            }

        # 筛选模式
        kws = conditions.get("keywords") or []
        results, relaxed = store.filter_restaurants(
            category=conditions.get("category"),
            price_min=conditions.get("price_min"),
            price_max=conditions.get("price_max"),
            location=conditions.get("location"),
            keywords=kws,
            sort=conditions.get("sort"),
            limit=8)

        reply, gen_source = generator.generate_reply(
            message, results, conditions, header_key=x_llm_key, relaxed=relaxed)

        nearest = (relaxed or {}).get("nearest") or []
        return {
            "conditions": conditions,
            "nlu_source": nlu_source,
            "reply": reply,
            "gen_source": gen_source,
            "mode": "filter",
            "relaxed": relaxed,
            "total": len(results),
            "restaurants": results if results else nearest,
        }
    except Exception as e:  # 兜底：任何异常都给出可用的本地结果
        results, _ = store.filter_restaurants(sort="popular", limit=8)
        return {
            "conditions": None, "nlu_source": "fallback", "gen_source": "template",
            "reply": f"处理时遇到点小状况（{type(e).__name__}），先给你人气榜前几名：",
            "mode": "filter",
            "relaxed": {"dropped": [], "detail": "服务异常，已回退到人气榜", "original_count": 0, "nearest": []},
            "total": len(results), "restaurants": results,
        }


# 静态前端挂在根路径（放最后，避免遮蔽 /api）
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
