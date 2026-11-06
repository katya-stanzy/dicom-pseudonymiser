"""Date handling for DA and DT values: shift by whole days, or keep only the year."""
from __future__ import annotations

from datetime import date, timedelta


def _shift_da(v: str, days: int) -> str:
    v = v.strip()
    if len(v) < 8 or not v[:8].isdigit():
        return ""                       # unparseable: drop rather than leak
    d = date(int(v[:4]), int(v[4:6]), int(v[6:8])) + timedelta(days=days)
    return d.strftime("%Y%m%d")


def shift_value(v: str, vr: str, days: int) -> str:
    """Shift a DA ('YYYYMMDD') or the date part of a DT; times are kept."""
    parts = [p for p in str(v).split("\\")]
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            out.append(p)
        elif vr == "DA":
            out.append(_shift_da(p, days))
        else:  # DT: YYYYMMDDHHMMSS.FFFFFF&ZZXX, date part first
            out.append(_shift_da(p[:8], days) + p[8:] if len(p) >= 8 else "")
    return "\\".join(out)


def year_only(v: str, vr: str) -> str:
    parts = [p.strip() for p in str(v).split("\\")]
    out = []
    for p in parts:
        if len(p) >= 4 and p[:4].isdigit():
            out.append(p[:4] + "0101")       # DA and DT both accept YYYYMMDD
        else:
            out.append("")
    return "\\".join(out)
