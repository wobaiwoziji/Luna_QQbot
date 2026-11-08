#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""天气卡片：/天气 [地名] 的数据抓取和图片渲染。"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = ROOT / "logs" / "weather"

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

FONT_REGULAR = Path(r"C:\Windows\Fonts\msyh.ttc")
FONT_BOLD = Path(r"C:\Windows\Fonts\msyhbd.ttc")
FONT_ICON = Path(r"C:\Windows\Fonts\seguiemj.ttf")

WEATHER = {
    0: ("晴", "☀️"),
    1: ("晴间多云", "🌤️"),
    2: ("多云", "⛅"),
    3: ("阴", "☁️"),
    45: ("雾", "🌫️"),
    48: ("雾凇", "🌫️"),
    51: ("毛毛雨", "🌦️"),
    53: ("毛毛雨", "🌦️"),
    55: ("毛毛雨", "🌦️"),
    56: ("冻雨", "🌧️"),
    57: ("冻雨", "🌧️"),
    61: ("小雨", "🌧️"),
    63: ("中雨", "🌧️"),
    65: ("大雨", "🌧️"),
    66: ("冻雨", "🌧️"),
    67: ("冻雨", "🌧️"),
    71: ("小雪", "🌨️"),
    73: ("中雪", "🌨️"),
    75: ("大雪", "🌨️"),
    77: ("雪粒", "🌨️"),
    80: ("阵雨", "🌦️"),
    81: ("阵雨", "🌦️"),
    82: ("强阵雨", "🌧️"),
    85: ("阵雪", "🌨️"),
    86: ("阵雪", "🌨️"),
    95: ("雷阵雨", "⛈️"),
    96: ("雷雨冰雹", "⛈️"),
    99: ("雷雨冰雹", "⛈️"),
}

WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
W = 760
MARGIN = 36


def _font(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except Exception:
        return ImageFont.truetype(str(FONT_REGULAR), size)


def _desc(code) -> str:
    try:
        return WEATHER.get(int(code), ("未知", "☁️"))[0]
    except Exception:
        return "未知"


def _icon(code) -> str:
    try:
        return WEATHER.get(int(code), ("未知", "☁️"))[1]
    except Exception:
        return "☁️"


def _aqi_label(value) -> str:
    try:
        aqi = float(value)
    except Exception:
        return "未知"
    if aqi <= 50:
        return "优"
    if aqi <= 100:
        return "良"
    if aqi <= 150:
        return "轻度污染"
    if aqi <= 200:
        return "中度污染"
    if aqi <= 300:
        return "重度污染"
    return "严重污染"


def _clock(value: str) -> str:
    return str(value or "")[11:16] or "--:--"


def fetch(city: str) -> dict:
    city = (city or "").strip()
    if not city:
        raise ValueError("没说是哪个城市")
    geo = requests.get(
        GEO_URL,
        params={"name": city, "count": 1, "language": "zh", "format": "json"},
        timeout=15,
    ).json()
    results = geo.get("results") or []
    if not results:
        raise ValueError(f"没找到「{city}」")
    place = results[0]
    lat = place["latitude"]
    lon = place["longitude"]

    forecast = requests.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
                "weather_code,wind_speed_10m,surface_pressure"
            ),
            "hourly": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "timezone": "auto",
            "forecast_days": 5,
            "wind_speed_unit": "kmh",
        },
        timeout=15,
    ).json()
    air = {}
    try:
        air = requests.get(
            AIR_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "us_aqi,pm2_5",
                "timezone": "auto",
            },
            timeout=12,
        ).json()
    except Exception:
        air = {}

    current = forecast.get("current") or {}
    hourly = forecast.get("hourly") or {}
    daily = forecast.get("daily") or {}
    air_now = air.get("current") or {}

    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    codes = hourly.get("weather_code") or []
    now_key = str(current.get("time") or "")[:13]
    start = 0
    for index, value in enumerate(times):
        if str(value)[:13] >= now_key:
            start = index
            break
    start = min(start + 1, max(0, len(times) - 6))
    hours = []
    for index in range(start, min(start + 6, len(times))):
        hours.append(
            {
                "time": str(times[index])[11:16],
                "temp": temps[index] if index < len(temps) else None,
                "code": codes[index] if index < len(codes) else None,
            }
        )

    days = []
    d_times = daily.get("time") or []
    d_codes = daily.get("weather_code") or []
    d_high = daily.get("temperature_2m_max") or []
    d_low = daily.get("temperature_2m_min") or []
    for index in range(min(5, len(d_times))):
        day = str(d_times[index])
        try:
            weekday = WEEK[datetime.strptime(day, "%Y-%m-%d").weekday()]
        except Exception:
            weekday = ""
        label = "今天" if index == 0 else ("明天" if index == 1 else weekday)
        days.append(
            {
                "label": label,
                "date": day[5:].replace("-", "/"),
                "code": d_codes[index] if index < len(d_codes) else None,
                "high": d_high[index] if index < len(d_high) else None,
                "low": d_low[index] if index < len(d_low) else None,
            }
        )

    location = " ".join(
        str(part).strip()
        for part in (place.get("country"), place.get("admin1"), place.get("name"))
        if str(part or "").strip()
    )
    return {
        "location": location or city,
        "current": {
            "temp": current.get("temperature_2m"),
            "feels": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind": current.get("wind_speed_10m"),
            "pressure": current.get("surface_pressure"),
            "code": current.get("weather_code"),
            "desc": _desc(current.get("weather_code")),
            "icon": _icon(current.get("weather_code")),
            "aqi": air_now.get("us_aqi"),
            "aqi_label": _aqi_label(air_now.get("us_aqi")),
            "sunrise": _clock((daily.get("sunrise") or [""])[0]),
            "sunset": _clock((daily.get("sunset") or [""])[0]),
        },
        "hours": hours,
        "days": days,
    }


def _gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), (120, 145, 255))
    draw = ImageDraw.Draw(image)
    top = (104, 128, 248)
    bottom = (135, 166, 255)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _panel(draw, box, radius=22):
    draw.rounded_rectangle(box, radius=radius, fill=(255, 255, 255, 28))


def render(data: dict) -> Path:
    height = 1380
    base = _gradient(W, height).convert("RGBA")
    overlay = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    _panel(od, (MARGIN, 338, W - MARGIN, 548))
    _panel(od, (MARGIN, 574, W - MARGIN, 1024))
    _panel(od, (MARGIN, 1050, W - MARGIN, 1310))
    image = Image.alpha_composite(base, overlay).convert("RGB")
    draw = ImageDraw.Draw(image)

    regular = _font(FONT_REGULAR, 24)
    small = _font(FONT_REGULAR, 20)
    tiny = _font(FONT_REGULAR, 17)
    bold = _font(FONT_BOLD, 28)
    big = _font(FONT_BOLD, 158)
    icon_font = _font(FONT_ICON, 44)
    icon_small = _font(FONT_ICON, 34)

    current = data["current"]
    draw.text((W / 2, 34), data["location"], font=bold, fill=(255, 255, 255), anchor="ma")
    temp = current.get("temp")
    if temp is not None:
        number = str(round(float(temp)))
        bbox = draw.textbbox((0, 0), number, font=big)
        nw = bbox[2] - bbox[0]
        nx = W / 2 - nw / 2
        draw.text((nx, 78), number, font=big, fill=(255, 255, 255), anchor="lt")
        draw.text((nx + nw + 2, 112), "°", font=_font(FONT_BOLD, 62), fill=(255, 255, 255), anchor="lt")
    else:
        draw.text((W / 2, 88), "--°", font=big, fill=(255, 255, 255), anchor="ma")
    condition = current.get("desc") or "未知"
    aqi = current.get("aqi")
    if aqi is not None:
        condition += f" ｜ 空气{current.get('aqi_label')} {round(float(aqi))}"
    draw.text((W / 2, 270), condition, font=regular, fill=(255, 255, 255), anchor="ma")

    # 未来几小时
    draw.text((MARGIN + 18, 356), "未来几小时", font=small, fill=(255, 255, 255))
    hours = data["hours"]
    if hours:
        column = (W - MARGIN * 2 - 36) / len(hours)
        for index, hour in enumerate(hours):
            cx = MARGIN + 18 + column * (index + 0.5)
            draw.text((cx, 398), hour["time"], font=small, fill=(255, 255, 255), anchor="ma")
            draw.text((cx, 440), _icon(hour.get("code")), font=icon_font, fill=(255, 255, 255), anchor="mm")
            value = hour.get("temp")
            text = f"{round(float(value))}°" if value is not None else "--°"
            draw.text((cx, 506), text, font=regular, fill=(255, 255, 255), anchor="ma")

    # 未来五天
    draw.text((MARGIN + 18, 592), "未来 5 天", font=small, fill=(255, 255, 255))
    days = data["days"]
    if days:
        column = (W - MARGIN * 2 - 36) / len(days)
        highs = []
        lows = []
        centers = []
        for index, day in enumerate(days):
            cx = MARGIN + 18 + column * (index + 0.5)
            centers.append(cx)
            draw.text((cx, 636), day["label"], font=small, fill=(255, 255, 255), anchor="ma")
            draw.text((cx, 668), day["date"], font=tiny, fill=(255, 255, 255), anchor="ma")
            draw.text((cx, 712), _icon(day.get("code")), font=icon_small, fill=(255, 255, 255), anchor="mm")
            high = day.get("high")
            low = day.get("low")
            highs.append(float(high) if high is not None else 0.0)
            lows.append(float(low) if low is not None else 0.0)
            draw.text(
                (cx, 756),
                f"{round(float(high))}°" if high is not None else "--°",
                font=regular,
                fill=(255, 255, 255),
                anchor="ma",
            )
            draw.text(
                (cx, 972),
                f"{round(float(low))}°" if low is not None else "--°",
                font=regular,
                fill=(255, 255, 255),
                anchor="ma",
            )

        top = max(highs) if highs else 1
        bottom = min(lows) if lows else 0
        span = max(1.0, top - bottom)

        def y_of(value):
            return 860 - (value - bottom) / span * 70

        high_points = [(centers[i], y_of(highs[i])) for i in range(len(centers))]
        low_points = [(centers[i], y_of(lows[i])) for i in range(len(centers))]
        draw.line(high_points, fill=(255, 255, 255), width=2, joint="curve")
        draw.line(low_points, fill=(255, 255, 255), width=2, joint="curve")
        for x, y in high_points + low_points:
            draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))

    # 详情
    details = [
        ("湿度", f"{current.get('humidity', '--')}%"),
        ("体感", f"{round(float(current['feels']))}°" if current.get("feels") is not None else "--°"),
        ("风速", f"{current.get('wind', '--')} km/h"),
        ("气压", f"{current.get('pressure', '--')} hPa"),
        ("日出", current.get("sunrise") or "--:--"),
        ("日落", current.get("sunset") or "--:--"),
    ]
    for index, (label, value) in enumerate(details):
        col = index % 2
        row = index // 2
        x = MARGIN + 26 + col * 340
        y = 1072 + row * 72
        draw.text((x, y), label, font=tiny, fill=(235, 240, 255))
        draw.text((x, y + 22), str(value), font=small, fill=(255, 255, 255))

    draw.text(
        (W / 2, 1334),
        "数据来源 Open-Meteo · Generated by Luna",
        font=tiny,
        fill=(235, 240, 255),
        anchor="ma",
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / f"weather-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    image.save(target, "PNG")
    return target
