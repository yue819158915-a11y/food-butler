# -*- coding: utf-8 -*-
"""天气与时段情境：给推荐引擎提供"今天适合吃什么"的环境输入。

数据源：Open-Meteo（免 Key）。失败一律降级为按时段推荐，不阻塞主流程。
"""
import datetime as dt
import json
import os
import threading
import time

import requests

_CA = "/etc/ssl/certs/ca-certificates.crt" if os.path.exists("/etc/ssl/certs/ca-certificates.crt") else True
_CACHE = {"data": None, "ts": 0}
_LOCK = threading.Lock()
TTL = 900  # 15 分钟

# 广州天河
LAT, LON = 23.1291, 113.2644
URL = ("https://api.open-meteo.com/v1/forecast"
       f"?latitude={LAT}&longitude={LON}"
       "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code"
       "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
       "&timezone=Asia%2FShanghai&forecast_days=1")


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


def fetch_weather():
    try:
        r = requests.get(URL, timeout=(2, 3), verify=_CA)
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
        }
    except Exception:
        return None


def weather():
    """带 TTL 缓存，失败返回 None（调用方降级）"""
    now = time.time()
    with _LOCK:
        if _CACHE["data"] and now - _CACHE["ts"] < TTL:
            return _CACHE["data"]
    data = fetch_weather()
    with _LOCK:
        if data:
            _CACHE["data"], _CACHE["ts"] = data, now
        elif _CACHE["data"]:
            return _CACHE["data"]  # 接口挂了用旧数据
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
