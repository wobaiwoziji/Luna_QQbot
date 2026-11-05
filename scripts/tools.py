#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""几个不依赖模型的小工具：天气、时间、计算。"""

from __future__ import annotations

import ast
import operator
import re
import time
from datetime import datetime

import requests

WEATHER_WORDS = re.compile(r"(天气|气温|温度|下雨|带伞|冷不冷|热不热)")
CALC_WORDS = re.compile(r"(算一下|算算|计算|等于多少|多少)")
TIME_WORDS = re.compile(r"(几点|现在时间|今天几号|日期|星期几|周几)")
CITY_AFTER = re.compile(r"(?:天气|气温|温度)[^\u4e00-\u9fa5]*([\u4e00-\u9fa5]{2,8})")
CITY_BEFORE = re.compile(r"([\u4e00-\u9fa5]{2,8})(?:今天|明天|现在)?(?:的)?(?:天气|气温|温度|下雨)")
SKIP_CITY = {"今天", "明天", "现在", "外面", "这里", "那里", "天气", "气温", "温度", "多少", "怎么样"}

WEATHER_CODES = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 81: "强阵雨", 82: "暴雨",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}

OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def log(message: str) -> None:
    print(f"[tools] {message}", flush=True)


def weather(city: str, tomorrow: bool = False) -> str:
    city = re.sub(r"^(今天|明天|现在|请问|查一下|看看)", "", city or "").strip()
    city = re.sub(r"(今天|明天|现在)$", "", city).strip()
    if not city or city in SKIP_CITY:
        return "没说是哪个城市，问一下对方在哪。"
    try:
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
            timeout=15,
        ).json()
        results = geo.get("results") or []
        if not results:
            return f"没找到「{city}」的天气。"
        place = results[0]
        data = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 2 if tomorrow else 1,
            },
            timeout=15,
        ).json()
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        index = 1 if tomorrow else 0
        code = current.get("weather_code")
        desc = WEATHER_CODES.get(int(code), "说不准") if code is not None else "说不准"
        high = (daily.get("temperature_2m_max") or [""])[0]
        low = (daily.get("temperature_2m_min") or [""])[0]
        if tomorrow:
            high = (daily.get("temperature_2m_max") or ["", ""])[index]
            low = (daily.get("temperature_2m_min") or ["", ""])[index]
        name = place.get("name") or city
        label = "明天" if tomorrow else "今天"
        return (
            f"{name}：{desc}，现在 {current.get('temperature_2m', '?')}°C，"
            f"体感 {current.get('apparent_temperature', '?')}°C，"
            f"{label} {low}~{high}°C，湿度 {current.get('relative_humidity_2m', '?')}%"
        )
    except Exception as exc:
        log(f"weather failed: {exc}")
        return "天气查不到，网络可能不通。"


def calc(text: str) -> str | None:
    expr = re.sub(r"(算一下|算算|计算|等于多少|多少|请|帮我|[？?，。,])", " ", text or "")
    expr = re.sub(r"[^0-9+\-*/().%\s]", "", expr).strip()
    if not expr or len(expr) > 60:
        return None
    try:
        node = ast.parse(expr, mode="eval").body
        value = _eval(node)
        if isinstance(value, float):
            value = round(value, 6)
        return f"{expr} = {value}"
    except Exception:
        return None


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 32 or abs(left) > 1e6):
            raise ValueError("too big")
        return OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in (ast.USub, ast.UAdd):
        return OPS[type(node.op)](_eval(node.operand))
    raise ValueError("bad expression")


def clock(text: str) -> str:
    now = datetime.now()
    weekday = "一二三四五六日"[now.weekday()]
    if "几点" in text or "时间" in text:
        return f"{now:%Y-%m-%d %H:%M}，周{weekday}"
    return f"{now:%Y-%m-%d}，周{weekday}"


def run(text: str) -> tuple[str, str] | None:
    value = (text or "").strip()
    if not value:
        return None
    if WEATHER_WORDS.search(value):
        city = ""
        match = CITY_BEFORE.search(value) or CITY_AFTER.search(value)
        if match:
            city = match.group(1)
        return "weather", weather(city, "明天" in value)
    if CALC_WORDS.search(value) and re.search(r"\d", value):
        result = calc(value)
        if result:
            return "calc", result
    if TIME_WORDS.search(value):
        return "time", clock(value)
    return None
