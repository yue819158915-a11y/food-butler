# -*- coding: utf-8 -*-
"""天气与时段情境：给推荐引擎提供"今天适合吃什么"的环境输入。

数据源（按顺序尝试，任一成功即用）：
1. Open-Meteo（免 Key）
2. wttr.in（免 Key，Cloudflare 前置，作为 Open-Meteo 连不通时的备份）

失败一律降级为按时段推荐，不阻塞主流程；失败结果缓存 5 分钟，避免每次请求都等超时。
"""
import datetime as dt
import os
import threading
import time

import requests

_CA = "/etc/ssl/certs/ca-certificates.crt" if os.path.exists("/etc/ssl/certs/ca-certificates.crt") else True
_CACHE = {"data": None, "ts": 0}
_FAIL_TS = 0
_LOCK = threading.Lock()
TTL = 900        # 成功缓存 15 分钟
FAIL_TTL = 300   # 失败缓存 5 分钟

LAT, LON = 23.1291, 113.2644
OPEN_METEO = ("https://api.open-meteo.com/v1/forecast"
              f"?latitude={LAT}&longitude={LON}"
              "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code"
              "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
              "&timezone=Asia%2FShanghai&forecast_days=1")
WTTR = "https://wttr.in/Guangzhou?format=j1&lang=zh"


def _desc(code):
    if code == 0:
        return "晴", "☀️", False
    if code in (1, 2):
        return "多云", "🌤", False
    if code == 3:
        return "阴", "☁️", False
    if code in (45, 48):
        return "雾", "🌫", False
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "雨", "🌧", True
    if 71 <= code <= 77 or 85 <= code <= 86:
        return "雪", "🌨", True
    if code >= 95:
        return "雷雨", "⛈", True
    return "多云", "🌤", False


def _from_desc_text(text):
    """从天气文字推断（wttr.in 用）"""
    t = (text or "").lower()
    if any(k in t for k in ("雷", "thunder")):
        return "雷雨", "⛈", True
    if any(k in t for k in ("雨", "rain", "drizzle", "shower")):
        return "雨", "🌧", True
    if any(k in t for k in ("雪", "snow", "sleet")):
        return "雪", "🌨", True
    if any(k in t for k in ("雾", "mist", "fog", "haze")):
        return "雾", "🌫", False
    if any(k in t for k in ("阴", "overcast")):
        return "阴", "☁️", False
    if any(k in t for k in ("云", "cloud")):
        return "多云", "🌤", False
    if any(k in t for k in ("晴", "sun", "clear")):
        return "晴", "☀️", False
    return "多云", "🌤", False


def _from_open_meteo():
    try:
        r = requests.get(OPEN_METEO, timeout=(4, 8), verify=_CA)
        if r.status_code != 200:
            return None
        d = r.json()
        cur = d.get("current") or {}
        code = int(cur.get("weather_code", 3))
        text, emoji, is_rain = _desc(code)
        daily = d.get("daily") or {}
        return {
            "temp": round(cur.get("temperature_2m", 0)),
            "apparent": round(cur.get("apparent_temperature", cur.get("temperature_2m", 0))),
            "humidity": round(cur.get("relative_humidity_2m", 0)),
            "precip": cur.get("precipitation", 0),
            "rain_prob": (daily.get("precipitation_probability_max") or [0])[0],
            "code": code, "text": text, "emoji": emoji, "is_rain": is_rain,
            "tmax": (daily.get("temperature_2m_max") or [None])[0],
            "tmin": (daily.get("temperature_2m_min") or [None])[0],
            "source": "open-meteo",
        }
    except Exception:
        return None


def _from_wttr():
    try:
        r = requests.get(WTTR, timeout=(4, 10), verify=_CA,
                         headers={"User-Agent": "food-butler/1.0"})
        if r.status_code != 200:
            return None
        d = r.json()
        cur = (d.get("current_condition") or [{}])[0]
        desc = ((cur.get("weatherDesc") or [{}])[0].get("value")
                or (cur.get("lang_zh") or [{}])[0].get("value") or "")
        text, emoji, is_rain = _from_desc_text(desc)
        precip = float(cur.get("precipMM") or 0)
        return {
            "temp": round(float(cur.get("temp_C") or 0)),
            "apparent": round(float(cur.get("FeelsLikeC") or cur.get("temp_C") or 0)),
            "humidity": round(float(cur.get("humidity") or 0)),
            "precip": precip,
            "rain_prob": 100 if (is_rain or precip > 0) else 0,
            "code": -1, "text": text or "多云", "emoji": emoji,
            "is_rain": is_rain or precip > 0,
            "tmax": None, "tmin": None, "source": "wttr.in",
        }
    except Exception:
        return None


def fetch_weather():
    for fn in (_from_open_meteo, _from_wttr):
        w = fn()
        if w:
            return w
    return None


def weather():
    """带 TTL 缓存；全部数据源失败后 5 分钟内直接返回 None，不再重试"""
    global _FAIL_TS
    now = time.time()
    with _LOCK:
        if _CACHE["data"] and now - _CACHE["ts"] < TTL:
            return _CACHE["data"]
        if not _CACHE["data"] and _FAIL_TS and now - _FAIL_TS < FAIL_TTL:
            return None
    data = fetch_weather()
    with _LOCK:
        if data:
            _CACHE["data"], _CACHE["ts"] = data, now
        else:
            _FAIL_TS = now
            if _CACHE["data"]:
                return _CACHE["data"]
    return data


def period(now=None):
    try:
        from zoneinfo import ZoneInfo
        h = (now or dt.datetime.now()).astimezone(ZoneInfo("Asia/Shanghai")).hour
    except Exception:
        h = (now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))).hour
    if 5 <= h < 10:
        return {"key": "breakfast", "label": "早餐"}
    if 10 <= h < 14:
        return {"key": "lunch", "label": "午餐"}
    if 14 <= h < 17:
        return {"key": "teatime", "label": "下午茶"}
    if 17 <= h < 21:
        return {"key": "dinner", "label": "晚餐"}
    return {"key": "supper", "label": "宵夜"}


def context():
    """给 planner 的环境上下文：天气 + 时段 + 一句话建议"""
    w = weather()
    p = period()
    ctx = {"weather": w, "period": p}
    if not w:
        ctx["headline"] = f"🍽 {p['label']}时段 · 按当下时间推荐"
        ctx["mood"] = "normal"
        return ctx

    temp, rain, humid = w["temp"], w["is_rain"] or (w.get("rain_prob") or 0) >= 60, w["humidity"]
    if rain:
        mood, why = "rainy", "落雨天，室内慢慢煮"
    elif temp <= 16:
        mood, why = "cold", "天冷，来点热的"
    elif temp >= 30:
        mood, why = "hot", "天热，清爽一点"
    elif humid >= 80 and temp >= 26:
        mood, why = "muggy", "湿气重，汤水舒服"
    else:
        mood, why = "mild", "天气正好，随便煮都行"
    ctx.update({"mood": mood, "reason": why,
                "headline": f"{w['emoji']} 广州 {temp}°C {w['text']} · {why}"})
    return ctx
