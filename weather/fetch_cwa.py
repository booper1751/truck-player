#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""從中央氣象署開放資料平臺抓資料，整理成播放器讀的 weather.json。
授權碼從環境變數 CWA_API_KEY 讀 (GitHub Secrets)，不會寫進檔案，也不會印在記錄裡。
用法: CWA_API_KEY=xxx python fetch_cwa.py weather.json
抓不到或資料不完整時直接失敗 (不產生檔案)，這樣舊的 weather.json 會保留。"""
import json, os, ssl, sys, time, datetime, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("CWA_API_KEY", "").strip()
BASE = os.environ.get("CWA_BASE", "https://opendata.cwa.gov.tw/api/v1/rest/datastore/")
FORECAST = "F-C0032-001"      # 一般天氣預報-今明36小時天氣預報 (全台縣市)
OBSERVE = "O-A0003-001"       # 現在天氣觀測報告 (全台測站)


def _ctx():
    c = ssl.create_default_context()
    c.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)    # 政府網站憑證缺欄位時，新版 Python 會誤擋；憑證驗證本身仍開著
    return c


def get(ds):
    last = ""
    for mode in ("header", "query"):
        url = BASE + ds + "?format=JSON"
        hdr = {"accept": "application/json", "User-Agent": "truck-player-weather"}
        if mode == "header":
            hdr["Authorization"] = KEY
        else:
            url += "&Authorization=" + urllib.parse.quote(KEY)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60, context=_ctx()) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:           # 只印狀態碼，絕不印網址 (網址可能含授權碼)
            last = f"HTTP {e.code}"
            if e.code not in (401, 403):
                break
        except Exception as e:
            last = e.__class__.__name__
            break
    sys.exit(f"[{ds}] 抓取失敗: {last}")


def wx_code(text):
    """氣象署的天氣現象文字 → 播放器用的圖示代碼 (和 Open-Meteo 的 WMO 代碼同一套)。"""
    t = text or ""
    if "雷" in t:
        return 95
    if "雪" in t:
        return 73
    if "雨" in t:
        if "豪" in t or "大雨" in t:
            return 65
        if "短暫" in t or "陣" in t:
            return 80
        return 61
    if "霧" in t:
        return 45
    if "陰" in t:
        return 3
    if "多雲" in t:
        return 1 if t.startswith("晴") else 2
    if "晴" in t:
        return 0
    return None


def num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v <= -90 else v


def parse_forecast(js):
    recs = js.get("records") or {}
    locs = recs.get("location") or []
    if not locs and recs.get("Locations"):
        locs = (recs["Locations"][0] or {}).get("Location") or []
    out = {}
    for loc in locs:
        name = loc.get("locationName") or loc.get("LocationName")
        els = {e.get("elementName"): e.get("time") or [] for e in (loc.get("weatherElement") or [])}
        periods = []
        for i, w in enumerate(els.get("Wx", [])):
            def at(el, key="parameterName"):
                arr = els.get(el, [])
                return ((arr[i].get("parameter") or {}).get(key)) if i < len(arr) else None
            periods.append({"s": w.get("startTime", ""), "e": w.get("endTime", ""), "text": (w.get("parameter") or {}).get("parameterName", ""),
                            "pop": num(at("PoP")), "min": num(at("MinT")), "max": num(at("MaxT"))})
        days = {}
        for p in periods:
            days.setdefault(p["s"][:10], []).append(p)
        res = []
        for date in sorted(days):
            ps = days[date]
            pick = next((q for q in ps if q["s"][11:13] in ("06", "07", "08", "09", "10", "11", "12")), ps[0])
            mins = [q["min"] for q in ps if q["min"] is not None]
            maxs = [q["max"] for q in ps if q["max"] is not None]
            pops = [q["pop"] for q in ps if q["pop"] is not None]
            res.append({"date": date, "tmin": min(mins) if mins else None, "tmax": max(maxs) if maxs else None,
                        "pop": max(pops) if pops else None, "text": pick["text"], "code": wx_code(pick["text"])})
        if name and res:
            plist = [{"s": q["s"], "e": q["e"], "tmin": q["min"], "tmax": q["max"], "pop": q["pop"], "text": q["text"], "code": wx_code(q["text"])}
                     for q in periods]
            out[name] = {"days": res[:3], "periods": plist[:3]}      # periods: 今天白天 / 今晚 / 明天白天 這種 12 小時一段的預報
    return out


def parse_stations(js):
    out = []
    for s in (js.get("records") or {}).get("Station") or []:
        geo = s.get("GeoInfo") or {}
        co = geo.get("Coordinates") or []
        c = next((x for x in co if x.get("CoordinateName") == "WGS84"), co[0] if co else {})
        la, lo = num(c.get("StationLatitude")), num(c.get("StationLongitude"))
        we = s.get("WeatherElement") or {}
        t = num(we.get("AirTemperature"))
        if la is None or lo is None or t is None:
            continue
        wx = we.get("Weather")
        wx = wx if wx and not str(wx).startswith("-") else ""
        out.append({"n": s.get("StationName", ""), "c": geo.get("CountyName", ""), "t": geo.get("TownName", ""),
                    "la": round(la, 4), "lo": round(lo, 4), "temp": t, "wx": wx, "code": wx_code(wx) if wx else None})
    return out


def main():
    if not KEY:
        sys.exit("沒有設定 CWA_API_KEY (請到倉庫 Settings → Secrets and variables → Actions 新增)")
    out = sys.argv[1] if len(sys.argv) > 1 else "weather.json"
    counties = parse_forecast(get(FORECAST))
    stations = parse_stations(get(OBSERVE))
    if len(counties) < 15 or len(stations) < 50:
        sys.exit(f"資料不完整 (縣市 {len(counties)}、測站 {len(stations)})，不更新。")
    data = {"ts": int(time.time()), "updated": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
            "counties": counties, "stations": stations}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完成: 縣市 {len(counties)}、測站 {len(stations)}、{os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()
