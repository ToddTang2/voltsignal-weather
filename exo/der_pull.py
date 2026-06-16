"""voltsignal exogenous — STANDALONE DER puller (PUBLIC repo). NO private imports, NO DB, NO secret.

CER small-scale postcode capacity (SGU-Solar kW + SGU-Battery kWh, monthly) aggregated to NEM region. Writes
thin exo/data/der.json. Publishes raw public capacity + the standard region rollup. publish_time = month-end
+ ~14-day CER publication lag (the data for month M is public ~mid M+1) — the leakage-guard input.
"""
from __future__ import annotations

import csv
import io
import json
import re
import ssl
import urllib.request
from datetime import UTC, date, datetime, timedelta

import certifi

_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "Mozilla/5.0 (voltsignal-exo research)"
CER_LAG_DAYS = 14
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
FEEDS = [
    ("rooftop_solar", "MW", "https://cer.gov.au/document/sgu-solar-capacity-2011-to-present-and-totals"),
    ("battery", "MWh", "https://cer.gov.au/document/sgu-battery-capacity-2011-to-present-and-totals"),
]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=90, context=_CTX) as r:  # noqa: S310
        return r.read().decode("utf-8", "ignore")


def postcode_region(pc: str) -> str | None:
    try:
        n = int(pc)
    except (TypeError, ValueError):
        return None
    if 1000 <= n <= 2599 or 2619 <= n <= 2899 or 2921 <= n <= 2999 or 200 <= n <= 299 \
            or 2600 <= n <= 2618 or 2900 <= n <= 2920:
        return "NSW1"
    if 3000 <= n <= 3999 or 8000 <= n <= 8999:
        return "VIC1"
    if 4000 <= n <= 4999 or 9000 <= n <= 9999:
        return "QLD1"
    if 5000 <= n <= 5799:
        return "SA1"
    if 7000 <= n <= 7799:
        return "TAS1"
    return None


def _month_end(label: str) -> date | None:
    m = re.match(r"^([A-Z][a-z]{2}) (\d{4})", label.strip())
    if not m:
        return None
    mo, yr = _MONTHS[m.group(1)], int(m.group(2))
    return date(yr + (mo == 12), (mo % 12) + 1, 1) - timedelta(days=1)


def _parse(csv_text: str):
    rows = list(csv.reader(io.StringIO(csv_text)))
    hdr = rows[0]
    total_col = next((i for i, c in enumerate(hdr) if c.strip().lower() == "total rated power output in kw"
                      or ("total" in c.strip().lower() and "kw" in c.lower() and "historic" not in c.lower())),
                     len(hdr) - 1)
    month_cols = [i for i, c in enumerate(hdr) if re.match(r"^[A-Z][a-z]{2} \d{4} - ", c.strip())]
    as_at = _month_end(hdr[month_cols[-1]]) if month_cols else None
    agg: dict[str, dict[str, float]] = {}

    def fv(r, i):
        try:
            return float(r[i].replace(",", "") or 0)
        except (ValueError, IndexError):
            return 0.0

    for r in rows[1:]:
        if not r or not r[0].strip().isdigit():
            continue
        reg = postcode_region(r[0])
        if reg is None:
            continue
        a = agg.setdefault(reg, {"cumulative": 0.0, "added_12m": 0.0})
        a["cumulative"] += fv(r, total_col)
        a["added_12m"] += sum(fv(r, i) for i in month_cols[-12:])
    return as_at, agg


def main() -> int:
    out_rows = []
    for tech, unit, url in FEEDS:
        as_at, agg = _parse(_get(url))
        if as_at is None:
            continue
        pub = datetime.combine(as_at + timedelta(days=CER_LAG_DAYS), datetime.min.time(),
                               tzinfo=UTC).isoformat()
        src = f"CER SGU-{tech} postcode capacity (as at {as_at.isoformat()})"
        for reg, v in agg.items():
            out_rows.append({"as_at_date": as_at.isoformat(), "region_id": reg, "tech": tech,
                             "cumulative_capacity": round(v["cumulative"] / 1000, 3),
                             "added_12m": round(v["added_12m"] / 1000, 3), "unit": unit,
                             "publish_time_utc": pub, "source": src,
                             "confidence_tier": "TIER_A_AUTHORITATIVE"})
    out = {"feed": "der", "generated_at_utc": datetime.now(UTC).isoformat(), "rows": out_rows}
    with open("exo/data/der.json", "w") as f:
        json.dump(out, f, indent=0)
    print(f"wrote exo/data/der.json: {len(out_rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
