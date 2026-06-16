"""voltsignal-weather — production Open-Meteo forecast-vintage puller (PUBLIC repo).

Runs on a GitHub Actions runner (16 GB, unlimited minutes for public repos) — NEVER on the 2 GB app box.
Fetches Open-Meteo forecast-vintage data for the 5 NEM regions, derives the documented features
(features.py, byte-identical to the app), and UPSERTs rows into weather_features via a SCOPED write-only
Supabase credential (DATABASE_URL from GitHub Secrets — never committed).

publish_time = issue + PUBLISH_LATENCY_HOURS — the realistic Open-Meteo global-model availability
(verified init+4–6 h; default 5 h, configurable). This is the leakage-guard input: it must reflect when
the run was ACTUALLY available, never nominal init.

Idempotent (ON CONFLICT upsert). Graceful failure: a region that fails to fetch/parse is logged and
SKIPPED — never written stale-as-fresh.
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from statistics import mean

import asyncpg
import certifi

import features

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

REGIONS: dict[str, dict] = {
    "NSW1": {"load": (-33.87, 151.21), "renew": [(-30.50, 150.50), (-33.30, 148.70)]},
    "QLD1": {"load": (-27.47, 153.03), "renew": [(-27.60, 151.30), (-23.40, 150.50)]},
    "VIC1": {"load": (-37.81, 144.96), "renew": [(-37.50, 142.50), (-36.10, 146.90)]},
    "SA1":  {"load": (-34.93, 138.60), "renew": [(-33.50, 138.20), (-34.60, 135.90)]},
    "TAS1": {"load": (-42.88, 147.33), "renew": [(-41.30, 146.40), (-42.00, 146.60)]},
}
MODEL = "openmeteo_best_match"
PUBLISH_LATENCY_H = int(os.environ.get("PUBLISH_LATENCY_HOURS", "5"))   # verified Open-Meteo availability
N_VINTAGES = int(os.environ.get("N_VINTAGES", "3"))
_RAW = ("temperature_2m", "wind_speed_100m", "shortwave_radiation")
_UA = "voltsignal-weather/1.0 (research; CC-BY Open-Meteo)"


def _fetch(lat: float, lon: float) -> dict:
    cols = []
    for v in _RAW:
        cols += [v] + [f"{v}_previous_day{n}" for n in range(1, N_VINTAGES)]
    q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "hourly": ",".join(cols),
                                "forecast_days": 2, "past_days": 1, "models": "best_match"})
    req = urllib.request.Request(f"https://previous-runs-api.open-meteo.com/v1/forecast?{q}",
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=40, context=_SSL_CTX) as r:
        return json.load(r)["hourly"]


def _col(h: dict, var: str, sfx: str, i: int):
    arr = h.get(f"{var}{sfx}")
    return arr[i] if arr and i < len(arr) and arr[i] is not None else None


def _region_rows(region: str, spec: dict, run_anchor: datetime) -> list[tuple]:
    load_h = _fetch(*spec["load"])
    renew_h = [_fetch(*p) for p in spec["renew"]]
    times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in load_h["time"]]
    rows: list[tuple] = []
    for n in range(N_VINTAGES):
        issue = run_anchor - timedelta(days=n)
        publish = issue + timedelta(hours=PUBLISH_LATENCY_H)
        sfx = "" if n == 0 else f"_previous_day{n}"
        for i, vt in enumerate(times):
            lead = (vt - issue).total_seconds() / 3600.0
            if lead < 0:
                continue
            temp = _col(load_h, "temperature_2m", sfx, i)
            winds = [w for w in (_col(h, "wind_speed_100m", sfx, i) for h in renew_h) if w is not None]
            ghis = [g for g in (_col(h, "shortwave_radiation", sfx, i) for h in renew_h) if g is not None]
            feats: dict[str, float] = {}
            if temp is not None:
                feats |= {"temperature_2m": temp, "cdd_18": features.cdd(temp), "hdd_18": features.hdd(temp)}
            if winds:
                w = mean(winds)
                feats |= {"wind_speed_100m": w, "wind_power_proxy": features.wind_power_proxy(w),
                          "low_wind_flag": features.low_wind_flag(w)}
            if ghis:
                g = mean(ghis)
                feats |= {"shortwave_radiation": g, "solar_proxy": features.solar_proxy(g)}
            for fname, val in feats.items():
                rows.append((vt, issue, publish, int(round(lead)), MODEL, region, fname, float(val)))
    return rows


_UPSERT = """
INSERT INTO weather_features (valid_time_utc, issue_time_utc, publish_time_utc, lead_time_hours,
                              model, geo_key, feature_name, value)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
ON CONFLICT (valid_time_utc, issue_time_utc, model, geo_key, feature_name)
DO UPDATE SET value = EXCLUDED.value, publish_time_utc = EXCLUDED.publish_time_utc,
              lead_time_hours = EXCLUDED.lead_time_hours
"""


async def main() -> int:
    dsn = os.environ["WEATHER_DB_URL"]                 # scoped write-only credential (GitHub Secret)
    run_anchor = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    conn = await asyncpg.connect(dsn)
    total, failed = 0, []
    try:
        for region, spec in REGIONS.items():
            try:
                rows = _region_rows(region, spec, run_anchor)             # fetch + derive (may fail)
            except Exception as e:                                        # graceful: log + skip, never stale
                print(f"  {region}: SKIPPED (fetch/parse error: {e})", file=sys.stderr)
                failed.append(region)
                continue
            for r in rows:
                await conn.execute(_UPSERT, *r)
            total += len(rows)
            print(f"  {region}: wrote {len(rows)} rows")
    finally:
        await conn.close()
    print(f"wrote {total} rows across {len(REGIONS) - len(failed)} regions "
          f"(publish = issue + {PUBLISH_LATENCY_H}h){'; skipped: ' + ','.join(failed) if failed else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
