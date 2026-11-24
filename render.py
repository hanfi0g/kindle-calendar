#!/usr/bin/env python3
"""Kindle 台历渲染：日期 / 星期 / 农历万年历 / 一周天气 / 空气质量 -> 灰度 PNG。"""
import calendar
import datetime as dt
import json
import os
import sys
import urllib.request
from zoneinfo import ZoneInfo

from lunar_python import Solar
from PIL import Image, ImageDraw, ImageFont

CITY = os.environ.get("CAL_CITY", "上海")
LAT = float(os.environ.get("CAL_LAT", "31.23"))
LON = float(os.environ.get("CAL_LON", "121.47"))
TZ = ZoneInfo(os.environ.get("CAL_TZ", "Asia/Shanghai"))
OUT = os.environ.get("CAL_OUT", "out")
SIZES = [(1072, 1448), (600, 800), (758, 1024), (1236, 1648)]

FONT_DIR = "/usr/share/fonts/opentype/noto"
SC = 2  # .ttc 中简体中文的序号
WEEK = "一二三四五六日"

# ---------------------------------------------------------------- 数据

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "kindle-calendar"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_weather():
    base = "https://api.open-meteo.com/v1/forecast"
    q = (f"?latitude={LAT}&longitude={LON}&timezone=Asia%2FShanghai&forecast_days=7"
         "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature"
         "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max")
    return get_json(base + q)


def fetch_air():
    base = "https://air-quality-api.open-meteo.com/v1/air-quality"
    q = (f"?latitude={LAT}&longitude={LON}&timezone=Asia%2FShanghai&forecast_days=7"
         "&current=pm2_5,pm10&hourly=pm2_5,pm10")
    return get_json(base + q)


# 国标 HJ 633-2012 IAQI 分段（24 小时均值，μg/m³）
_BP = {
    "pm2_5": [0, 35, 75, 115, 150, 250, 350, 500],
    "pm10": [0, 50, 150, 250, 350, 420, 500, 600],
}
_IAQI = [0, 50, 100, 150, 200, 300, 400, 500]


def iaqi(kind, c):
    bp = _BP[kind]
    if c is None:
        return None
    for i in range(1, len(bp)):
        if c <= bp[i]:
            lo, hi = bp[i - 1], bp[i]
            return round((_IAQI[i] - _IAQI[i - 1]) / (hi - lo) * (c - lo) + _IAQI[i - 1])
    return 500


def aqi_of(pm25, pm10):
    vals = [v for v in (iaqi("pm2_5", pm25), iaqi("pm10", pm10)) if v is not None]
    return max(vals) if vals else None


def aqi_level(a):
    if a is None:
        return "—"
    for lim, name in ((50, "优"), (100, "良"), (150, "轻度"), (200, "中度"), (300, "重度")):
        if a <= lim:
            return name
    return "严重"


def daily_aqi(air):
    """按天求 PM 均值 -> 国标 AQI。"""
    h = air.get("hourly", {})
    days = {}
    for t, p25, p10 in zip(h.get("time", []), h.get("pm2_5", []), h.get("pm10", [])):
        d = t[:10]
        days.setdefault(d, ([], []))
        if p25 is not None:
            days[d][0].append(p25)
        if p10 is not None:
            days[d][1].append(p10)
    out = {}
    for d, (a, b) in days.items():
        if len(a) >= 12:
            out[d] = aqi_of(sum(a) / len(a), sum(b) / len(b) if b else None)
    return out


WMO = {
    0: ("晴", "sun"), 1: ("晴间云", "sun_cloud"), 2: ("多云", "sun_cloud"), 3: ("阴", "cloud"),
    45: ("雾", "fog"), 48: ("雾凇", "fog"),
    51: ("毛毛雨", "rain"), 53: ("毛毛雨", "rain"), 55: ("毛毛雨", "rain"),
    56: ("冻雨", "rain"), 57: ("冻雨", "rain"),
    61: ("小雨", "rain"), 63: ("中雨", "rain"), 65: ("大雨", "rain_heavy"),
    66: ("冻雨", "rain"), 67: ("冻雨", "rain_heavy"),
    71: ("小雪", "snow"), 73: ("中雪", "snow"), 75: ("大雪", "snow"), 77: ("雪粒", "snow"),
    80: ("阵雨", "rain"), 81: ("阵雨", "rain"), 82: ("强阵雨", "rain_heavy"),
    85: ("阵雪", "snow"), 86: ("阵雪", "snow"),
    95: ("雷阵雨", "thunder"), 96: ("雷阵雨", "thunder"), 99: ("雷阵雨", "thunder"),
}


def wmo(code):
    return WMO.get(int(code) if code is not None else -1, ("—", "cloud"))


# ---------------------------------------------------------------- 绘图工具

class Canvas:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.s = w / 1072.0
        self.img = Image.new("L", (w, h), 255)
        self.d = ImageDraw.Draw(self.img)
        self._cache = {}

    def S(self, v):
        return int(round(v * self.s))

    def font(self, size, weight="Regular", serif=False):
        key = (size, weight, serif)
        if key not in self._cache:
            name = f"Noto{'Serif' if serif else 'Sans'}CJK-{weight}.ttc"
            self._cache[key] = ImageFont.truetype(os.path.join(FONT_DIR, name), self.S(size), index=SC)
        return self._cache[key]

    def text(self, xy, s, size, weight="Regular", fill=0, anchor="la", serif=False):
        x, y = xy
        self.d.text((self.S(x), self.S(y)), s, font=self.font(size, weight, serif), fill=fill, anchor=anchor)

    def width(self, s, size, weight="Regular"):
        return self.d.textlength(s, font=self.font(size, weight)) / self.s

    def line(self, x1, y1, x2, y2, w=2, fill=0):
        self.d.line([(self.S(x1), self.S(y1)), (self.S(x2), self.S(y2))], fill=fill, width=max(1, self.S(w)))

    def rect(self, x1, y1, x2, y2, fill=None, outline=None, w=2, r=0):
        box = [self.S(x1), self.S(y1), self.S(x2), self.S(y2)]
        if r:
            self.d.rounded_rectangle(box, radius=self.S(r), fill=fill, outline=outline, width=max(1, self.S(w)))
        else:
            self.d.rectangle(box, fill=fill, outline=outline, width=max(1, self.S(w)))

    def ellipse(self, cx, cy, r, fill=None, outline=None, w=2):
        self.d.ellipse([self.S(cx - r), self.S(cy - r), self.S(cx + r), self.S(cy + r)],
                       fill=fill, outline=outline, width=max(1, self.S(w)))

    # -- 天气图标（以 cx,cy 为中心，边长约 2r）
    def icon(self, kind, cx, cy, r):
        if kind == "sun":
            self._sun(cx, cy, r * 0.45, r)
        elif kind == "sun_cloud":
            self._sun(cx - r * 0.3, cy - r * 0.3, r * 0.32, r * 0.7)
            self._cloud(cx + r * 0.12, cy + r * 0.2, r * 0.8)
        elif kind == "cloud":
            self._cloud(cx, cy, r)
        elif kind == "fog":
            self._cloud(cx, cy - r * 0.25, r * 0.85)
            for i, dy in enumerate((0.45, 0.72)):
                self.line(cx - r * 0.8, cy + r * dy, cx + r * 0.8, cy + r * dy, w=r * 0.1)
        elif kind in ("rain", "rain_heavy"):
            self._cloud(cx, cy - r * 0.25, r * 0.85)
            n = 4 if kind == "rain_heavy" else 3
            for i in range(n):
                x = cx - r * 0.5 + i * (r / (n - 1))
                self.line(x, cy + r * 0.4, x - r * 0.18, cy + r * 0.85, w=r * 0.1)
        elif kind == "snow":
            self._cloud(cx, cy - r * 0.25, r * 0.85)
            for i in range(3):
                x = cx - r * 0.5 + i * r * 0.5
                self.text((x, cy + r * 0.62), "*", int(r * 0.9 / 1), "Bold", anchor="mm")
        elif kind == "thunder":
            self._cloud(cx, cy - r * 0.25, r * 0.85)
            pts = [(cx + r * 0.1, cy + r * 0.25), (cx - r * 0.2, cy + r * 0.62), (cx + r * 0.02, cy + r * 0.62),
                   (cx - r * 0.12, cy + r * 0.98), (cx + r * 0.3, cy + r * 0.5), (cx + r * 0.08, cy + r * 0.5)]
            self.d.polygon([(self.S(x), self.S(y)) for x, y in pts], fill=0)

    def _sun(self, cx, cy, core, ray):
        import math
        self.ellipse(cx, cy, core, fill=0)
        for k in range(8):
            a = math.pi / 4 * k
            self.line(cx + math.cos(a) * core * 1.35, cy + math.sin(a) * core * 1.35,
                      cx + math.cos(a) * ray * 0.95, cy + math.sin(a) * ray * 0.95, w=ray * 0.11)

    def _cloud(self, cx, cy, r):
        # 黑色描边 + 白色填充的云
        parts = [(-0.45, 0.1, 0.38), (0.05, -0.15, 0.5), (0.48, 0.12, 0.36)]
        for pad, col in ((r * 0.09, 0), (0, 255)):
            for dx, dy, rr in parts:
                self.ellipse(cx + dx * r, cy + dy * r, rr * r + pad, fill=col)
            self.rect(cx - 0.45 * r - pad * 0.0, cy + 0.1 * r - pad * 0.2, cx + 0.48 * r, cy + 0.48 * r + pad, fill=col)


# ---------------------------------------------------------------- 版面

def render(w, h, now, wx, air):
    c = Canvas(w, h)
    W = 1072
    H = h / c.s  # 逻辑高度（按 1072 宽换算）
    M = 48
    today = now.date()
    solar = Solar.fromYmd(today.year, today.month, today.day)
    lunar = solar.getLunar()

    # ---------- 顶部：年月 + 星期
    c.text((M, 40), f"{today.year}年{today.month}月", 44, "Bold")
    c.text((W - M, 40), f"星期{WEEK[today.weekday()]}", 44, "Bold", anchor="ra")
    c.line(M, 104, W - M, 104, w=3)

    # ---------- 大日期
    c.text((W / 2, 296), str(today.day), 210, "Bold", anchor="ms", serif=True)

    # 农历
    nl = f"农历{lunar.getMonthInChinese()}月{lunar.getDayInChinese()}"
    gz = f"{lunar.getYearInGanZhi()}{lunar.getYearShengXiao()}年 · {lunar.getMonthInGanZhi()}月 · {lunar.getDayInGanZhi()}日"
    c.text((W / 2, 356), nl, 44, "Bold", anchor="ms")
    c.text((M, 408), gz, 28, anchor="ls")

    # 节日 / 节气
    tags = list(solar.getFestivals()) + list(lunar.getFestivals())
    if lunar.getJieQi():
        tags.insert(0, lunar.getJieQi())
    if tags:
        tag = "  ".join(tags[:3])
    else:
        nj = lunar.getNextJieQi()
        nd = nj.getSolar()
        days = (dt.date(nd.getYear(), nd.getMonth(), nd.getDay()) - today).days
        tag = f"距{nj.getName()}还有 {days} 天"
    c.text((W - M, 408), tag, 28, "Bold", anchor="rs")

    # 宜 / 忌
    yi = " ".join(lunar.getDayYi()[:5]) or "—"
    ji = " ".join(lunar.getDayJi()[:5]) or "—"
    y0 = 428
    bw = 46
    c.rect(M, y0, M + bw, y0 + bw, fill=0, r=8)
    c.text((M + bw / 2, y0 + bw / 2), "宜", 32, "Bold", fill=255, anchor="mm")
    c.text((M + bw + 16, y0 + bw / 2), yi, 28, anchor="lm")
    x2 = W / 2 + 10
    c.rect(x2, y0, x2 + bw, y0 + bw, outline=0, w=3, r=8)
    c.text((x2 + bw / 2, y0 + bw / 2), "忌", 32, "Bold", anchor="mm")
    c.text((x2 + bw + 16, y0 + bw / 2), ji, 28, anchor="lm")

    # ---------- 月历
    gy = 494
    c.line(M, gy, W - M, gy, w=2)
    colw = (W - 2 * M) / 7
    for i, wd in enumerate(WEEK):
        c.text((M + colw * (i + 0.5), gy + 36), wd, 26, "Bold", anchor="ms",
               fill=0 if i < 5 else 90)
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(today.year, today.month)
    avail = (H - 515) - (gy + 48)
    rowh = min(80, avail / len(weeks))
    for r_i, wk in enumerate(weeks):
        for c_i, d in enumerate(wk):
            cx = M + colw * (c_i + 0.5)
            top = gy + 48 + r_i * rowh
            if d.month != today.month:
                continue
            l = Solar.fromYmd(d.year, d.month, d.day).getLunar()
            sub = l.getJieQi() or ""
            if not sub:
                fs = l.getFestivals() or Solar.fromYmd(d.year, d.month, d.day).getFestivals()
                sub = fs[0] if fs else ""
            if len(sub) > 3:
                sub = sub[:3]
            if not sub:
                sub = l.getDayInChinese() if l.getDay() != 1 else f"{l.getMonthInChinese()}月"
            is_today = d == today
            fg = 255 if is_today else (0 if c_i < 5 else 70)
            if is_today:
                c.rect(cx - colw / 2 + 6, top + 2, cx + colw / 2 - 6, top + rowh - 4, fill=0, r=12)
            c.text((cx, top + rowh * 0.53), str(d.day), int(rowh * 0.44), "Bold", fill=fg, anchor="ms")
            c.text((cx, top + rowh * 0.87), sub, int(rowh * 0.24), fill=fg, anchor="ms")

    # ---------- 天气区
    wy = H - 505
    c.line(M, wy, W - M, wy, w=3)
    dq = daily_aqi(air) if air else {}
    if wx:
        cur = wx["current"]
        name, kind = wmo(cur.get("weather_code"))
        c.icon(kind, M + 62, wy + 92, 52)
        t = round(cur.get("temperature_2m", 0))
        c.text((M + 140, wy + 128), f"{t}°", 96, "Bold", anchor="ls")
        tx = M + 140 + c.width(f"{t}°", 96, "Bold") + 24
        c.text((tx, wy + 72), f"{CITY} · {name}", 34, "Bold", anchor="ls")
        feel = cur.get("apparent_temperature")
        hum = cur.get("relative_humidity_2m")
        wind = cur.get("wind_speed_10m")
        c.text((tx, wy + 122),
               f"体感 {round(feel)}° · 湿度 {round(hum)}% · 风 {round(wind)}km/h", 26, anchor="ls")
    else:
        c.text((M, wy + 90), f"{CITY} · 天气暂时获取失败", 34, "Bold", anchor="ls")

    # 当前空气质量
    cur_aqi = None
    if air and air.get("current"):
        cur_aqi = aqi_of(air["current"].get("pm2_5"), air["current"].get("pm10"))
    bx1, bx2 = W - M - 200, W - M
    c.rect(bx1, wy + 30, bx2, wy + 140, fill=0, r=14)
    c.text(((bx1 + bx2) / 2, wy + 66), "空气质量", 24, fill=255, anchor="ms")
    c.text(((bx1 + bx2) / 2, wy + 124),
           f"{cur_aqi if cur_aqi is not None else '—'} {aqi_level(cur_aqi)}", 38, "Bold", fill=255, anchor="ms")
    if air and air.get("current", {}).get("pm2_5") is not None:
        c.text((bx2, wy + 172), f"PM2.5 {round(air['current']['pm2_5'])}  PM10 {round(air['current']['pm10'] or 0)}",
               22, anchor="rs")

    # 7 天预报
    fy = wy + 186
    c.line(M, fy - 8, W - M, fy - 8, w=1, fill=150)
    if wx:
        dd = wx["daily"]
        n = min(7, len(dd["time"]))
        cw = (W - 2 * M) / n
        for i in range(n):
            ds = dd["time"][i]
            d = dt.date.fromisoformat(ds)
            cx = M + cw * (i + 0.5)
            label = "今天" if d == today else ("明天" if d == today + dt.timedelta(days=1) else f"周{WEEK[d.weekday()]}")
            if i:
                c.line(M + cw * i, fy + 6, M + cw * i, fy + 296, w=1, fill=170)
            c.text((cx, fy + 30), label, 28, "Bold", anchor="ms")
            c.text((cx, fy + 56), f"{d.month}/{d.day}", 20, anchor="ms", fill=80)
            name, kind = wmo(dd["weather_code"][i])
            c.icon(kind, cx, fy + 98, 28)
            c.text((cx, fy + 158), name, 24, anchor="ms")
            hi, lo = dd["temperature_2m_max"][i], dd["temperature_2m_min"][i]
            c.text((cx, fy + 198), f"{round(hi)}°/{round(lo)}°", 30, "Bold", anchor="ms")
            pp = dd.get("precipitation_probability_max", [None] * n)[i]
            a = dq.get(ds)
            c.text((cx, fy + 236), f"AQI {a}" if a is not None else "AQI —", 22, anchor="ms")
            c.text((cx, fy + 262), aqi_level(a) if a is not None else "", 20, anchor="ms", fill=60)
            if pp is not None:
                c.text((cx, fy + 290), f"降水{pp}%", 18, anchor="ms", fill=80)

    # 页脚
    c.text((M, wy + 172), f"更新于 {now:%m-%d %H:%M} · Open-Meteo", 20, anchor="ls", fill=90)
    return c.img


def main():
    now = dt.datetime.now(TZ)
    wx = air = None
    if len(sys.argv) > 1 and sys.argv[1] == "--mock":
        with open(sys.argv[2]) as f:
            mock = json.load(f)
        wx, air = mock["weather"], mock["air"]
    else:
        try:
            wx = fetch_weather()
        except Exception as e:  # noqa
            print("weather error:", e)
        try:
            air = fetch_air()
        except Exception as e:  # noqa
            print("air error:", e)
    os.makedirs(OUT, exist_ok=True)
    for w, h in SIZES:
        img = render(w, h, now, wx, air)
        img.save(os.path.join(OUT, f"cal_{w}x{h}.png"), optimize=True)
    with open(os.path.join(OUT, "status.txt"), "w") as f:
        f.write(f"{now.isoformat()} weather={'ok' if wx else 'fail'} air={'ok' if air else 'fail'}\n")
    print("done", now)


if __name__ == "__main__":
    main()
