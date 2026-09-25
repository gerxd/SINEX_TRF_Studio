# analysis/episodes.py
import datetime

import numpy as np

from . import datum

A = 6378137.0
F = 1.0 / 298.257222101


def epoch_tuple(text):
    parts = str(text or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        year, doy, sec = (int(p) for p in parts)
    except ValueError:
        return None
    if year == 0 and doy == 0 and sec == 0:
        return None
    if len(parts[0]) <= 2:
        year += 2000 if year <= 50 else 1900
    return year, doy, sec


def epoch_date(text):
    t = epoch_tuple(text)
    if t is None:
        return ""
    try:
        return (datetime.date(t[0], 1, 1) + datetime.timedelta(days=t[1] - 1, seconds=t[2])).isoformat()
    except (ValueError, OverflowError):
        return ""


def span_text(start, end):
    a, b = epoch_date(start), epoch_date(end)
    if a and b:
        return f"{a} to {b}"
    if a:
        return f"from {a}"
    if b:
        return f"until {b}"
    return ""


def key(record):
    return record.get("code"), record.get("pt", ""), record.get("soln")


def labels(epochs=None, breaks=None):
    out = {}
    for e in epochs or []:
        out.setdefault(key(e), {"span": "", "break": ""})["span"] = span_text(
            e.get("data_start"), e.get("data_end"))
    positions = [b for b in breaks or [] if b.get("type") == "P"]
    ends = {}
    for b in positions:
        end = epoch_tuple(b.get("end"))
        if end is not None and b.get("reason"):
            ends[(b.get("code"), b.get("pt"), end)] = b["reason"]
    for b in positions:
        start = epoch_tuple(b.get("start"))
        reason = ends.get((b.get("code"), b.get("pt"), start)) if start is not None else None
        if reason:
            out.setdefault(key(b), {"span": "", "break": ""})["break"] = reason
    return out


def geodetic(x, y, z):
    x, y, z = (np.asarray(v, dtype=float) for v in (x, y, z))
    b = A * (1.0 - F)
    e2 = F * (2.0 - F)
    ep2 = e2 / (1.0 - e2)
    p = np.hypot(x, y)
    th = np.arctan2(z * A, p * b)
    lat = np.arctan2(z + ep2 * b * np.sin(th) ** 3, p - e2 * A * np.cos(th) ** 3)
    return np.degrees(lat), np.degrees(np.arctan2(y, x))


def positions(sol):
    eps = list(datum.parse_station_coordinates(sol or []).items())
    if not eps:
        return []
    lat, lon = geodetic(*([m[c] for _, m in eps] for c in ("x", "y", "z")))
    return [{"label": label, "code": m["code"], "pt": m["pt"], "soln": m["soln"],
             "latitude": float(la), "longitude": float(lo)}
            for (label, m), la, lo in zip(eps, lat, lon)]
