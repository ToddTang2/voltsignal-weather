"""voltsignal exogenous — STANDALONE weather puller (PUBLIC repo). NO DB, NO secret.

Migrated sink: instead of pushing to Supabase via WEATHER_DB_URL, this fetches Open-Meteo forecast-vintage
data for the 5 NEM regions, derives the documented features (features.py, byte-identical to the app), and
COMMITS the rows to exo/data/weather.json. The Action commits via GITHUB_TOKEN (no secret); the box ingests
+ upserts into weather_features preserving valid/issue/publish — so get_features_asof stays leakage-safe.
publish_time = issue + PUBLISH_LATENCY_HOURS (verified Open-Meteo global-model availability), never nominal
init. Idempotent on the box; graceful: a region that fails to fetch is logged + skipped, never stale.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from statistics import mean

import certifi

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root for features.py
import features  # noqa: E402

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
REGIONS: dict[str, dict] = {
    "NSW1": {"load": (-33.87, 151.21), "renew": [(-30.50, 150.50), (-33.30, 148.70)]},
    "QLD1": {"load": (-27.47, 153.03), "renew": [(-27.60, 151.30), (-23.40, 150.50)]},
    "VIC1": {"load": (-37.81, 144.96), "renew": [(-37.50, 142.50), (-36.10, 146.90)]},
    "SA1":  {"load": (-34.93, 138.60), "renew": [(-33.50, 138.20), (-34.60, 135.90)]},
    "TAS1": {"load": (-42.88, 147.33), "renew": [(-41.30, 146.40), (-42.00, 146.60)]},
}
MODEL = "openmeteo_best_match"
PUBLISH_LATENCY_H = int(os.environ.get("PUBLISH_LATENCY_HOURS", "5"))
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
    with urllib.request.urlopen(req, timeout=40, context=_SSL_CTX) as r:  # noqa: S310
        return json.load(r)["hourly"]


def _col(h: dict, var: str, sfx: str, i: int):
    arr = h.get(f"{var}{sfx}")
    return arr[i] if arr and i < len(arr) and arr[i] is not None else None


def _region_rows(region: str, spec: dict, run_anchor: datetime) -> list[dict]:
    load_h = _fetch(*spec["load"])
    renew_h = [_fetch(*p) for p in spec["renew"]]
    times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in load_h["time"]]
    rows: list[dict] = []
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
                rows.append({"valid_time_utc": vt.isoformat(), "issue_time_utc": issue.isoformat(),
                             "publish_time_utc": publish.isoformat(), "lead_time_hours": int(round(lead)),
                             "model": MODEL, "geo_key": region, "feature_name": fname, "value": float(val)})
    return rows


def main() -> int:
    run_anchor = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    all_rows, failed = [], []
    for region, spec in REGIONS.items():
        try:
            rows = _region_rows(region, spec, run_anchor)
        except Exception as e:  # noqa: BLE001 — graceful: skip a region, never write stale
            print(f"  {region}: SKIPPED ({e})", file=sys.stderr)
            failed.append(region)
            continue
        all_rows += rows
        print(f"  {region}: {len(rows)} rows")
    out = {"feed": "weather", "generated_at_utc": datetime.now(UTC).isoformat(),
           "model": MODEL, "publish_latency_hours": PUBLISH_LATENCY_H, "rows": all_rows}
    with open("exo/data/weather.json", "w") as f:
        json.dump(out, f, indent=0)
    print(f"wrote exo/data/weather.json: {len(all_rows)} rows"
          f"{'; skipped ' + ','.join(failed) if failed else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
