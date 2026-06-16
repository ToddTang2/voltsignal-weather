"""voltsignal exogenous — STANDALONE hydro puller (PUBLIC repo). NO private imports, NO DB, NO secret.

Snowy per-lake storage % (getData.php JSON, capacity-weighted scheme total) + Hydro Tasmania TEIS %
(official EnergyInStorage XLS). Writes thin exo/data/hydro.json; the Action commits it via GITHUB_TOKEN; the
box ingests + upserts via its own creds. Publishes raw public storage % + the standard capacity-weighted
total. publish_time = Snowy dataTimestamp / HydroTas reading_date + 1 day (next-working-day publication).
"""
from __future__ import annotations

import io
import json
import ssl
import urllib.request
from datetime import UTC, datetime, timedelta, timezone

import certifi

_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "Mozilla/5.0 (voltsignal-exo research)"
AEST = timezone(timedelta(hours=10))
SNOWY_URL = "https://www.snowyhydro.com.au/wp-content/themes/snowyhydro/inc/getData.php?yearA={y}&yearB={y}"
HYDROTAS_XLS = "https://www.hydro.com.au/docs/energyinstorage/download/EnergyInStorage-HistoricalData.xls"
SNOWY_CAPACITY_GL = {"Lake Eucumbene": 4798.0, "Lake Jindabyne": 688.0, "Tantangara Reservoir": 254.0}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=90, context=_CTX) as r:  # noqa: S310
        return r.read()


def _snowy_weighted_total(lake_pct: dict[str, float]) -> float:
    num = sum(lake_pct[k] * SNOWY_CAPACITY_GL[k] for k in lake_pct if k in SNOWY_CAPACITY_GL)
    den = sum(SNOWY_CAPACITY_GL[k] for k in lake_pct if k in SNOWY_CAPACITY_GL)
    return round(num / den, 2) if den else 0.0


def _snowy_row(year: int) -> dict | None:
    d = json.loads(_get(SNOWY_URL.format(y=year)).decode("utf-8", "ignore"))
    levels = d.get(str(year), {}).get("snowyhydro", {}).get("level", [])
    if not levels:
        return None
    last = levels[-1]
    lake_pct = {lk["-name"]: float(lk["#text"]) for lk in last["lake"] if lk.get("#text")}
    ts = last["lake"][0].get("-dataTimestamp") or last["-date"]
    return {
        "reading_date": datetime.fromisoformat(last["-date"]).date().isoformat(),
        "system": "SNOWY", "region_id": "NSW1", "storage_pct": _snowy_weighted_total(lake_pct),
        "components": lake_pct,
        "publish_time_utc": datetime.fromisoformat(ts).replace(tzinfo=AEST).astimezone(UTC).isoformat(),
        "source": "Snowy Hydro getData.php (per-lake %, capacity-weighted)",
        "confidence_tier": "TIER_B_SCRAPED_DERIVED",
    }


def _hydrotas_row() -> dict | None:
    import xlrd
    sh = xlrd.open_workbook(file_contents=_get(HYDROTAS_XLS)).sheet_by_index(0)
    full = sh.cell_value(7, 22)
    last = sh.nrows - 1
    teis = sh.cell_value(last, 22)
    rdate = (datetime(1899, 12, 30) + timedelta(days=int(sh.cell_value(last, 0)))).date()
    pub = datetime.combine(rdate + timedelta(days=1), datetime.min.time()).replace(
        hour=10, tzinfo=AEST).astimezone(UTC)
    return {
        "reading_date": rdate.isoformat(), "system": "HYDRO_TAS", "region_id": "TAS1",
        "storage_pct": round(teis / full * 100, 2),
        "components": {"teis_gwh": round(teis, 1), "full_supply_gwh": round(full, 1)},
        "publish_time_utc": pub.isoformat(),
        "source": "Hydro Tasmania EnergyInStorage-HistoricalData.xls (TEIS)",
        "confidence_tier": "TIER_A_AUTHORITATIVE",
    }


def main() -> int:
    rows = [r for r in (_snowy_row(datetime.now(UTC).astimezone(AEST).year), _hydrotas_row()) if r]
    out = {"feed": "hydro", "generated_at_utc": datetime.now(UTC).isoformat(), "rows": rows}
    with open("exo/data/hydro.json", "w") as f:
        json.dump(out, f, indent=0)
    print(f"wrote exo/data/hydro.json: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
