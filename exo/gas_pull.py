"""voltsignal exogenous — STANDALONE gas puller (PUBLIC repo). NO private imports, NO DB, NO secret.

Runs on a GitHub Actions runner. Fetches the public AEMO STTM ex-ante price + GSH Wallumbilla benchmark,
parses them (textbook fields, vendored here — does NOT import the private voltsignal package), and writes a
THIN result file exo/data/gas.json. The Action then commits that file via the automatic GITHUB_TOKEN (no
secret). The app box ingests it from raw.githubusercontent.com and upserts via its own existing creds.

Publishes only RAW public data + the standard gas-implied-price (×8.2 heat rate). publish_time = STTM
approval_datetime / GSH LASTCHANGED — the leakage-guard input.

    python exo/gas_pull.py
"""
from __future__ import annotations

import csv
import io
import json
import re
import ssl
import urllib.request
import zipfile
from datetime import UTC, datetime, timedelta, timezone

import certifi

_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "voltsignal-exo/1.0 (research)"
AEST = timezone(timedelta(hours=10))
HEAT_RATE = 8.2
STTM_HUB_REGION = {"ADL": "SA1", "BRI": "QLD1", "SYD": "NSW1"}
STTM_CSV = "https://nemweb.com.au/Reports/Current/STTM/int651_v1_ex_ante_market_price_rpt_1.csv"
GSH_DIR = "https://nemweb.com.au/Reports/Current/GSH/Benchmark_Price/"
GSH_RECENT_DAYS = 14


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=90, context=_CTX) as r:  # noqa: S310
        return r.read()


def _sttm_dt(s: str) -> str:
    return datetime.strptime(s.strip(), "%d %b %Y %H:%M:%S").replace(tzinfo=AEST).astimezone(UTC).isoformat()


def _mms_dt(s: str) -> str:
    return datetime.strptime(s.strip().strip('"'), "%Y/%m/%d %H:%M:%S").replace(
        tzinfo=AEST).astimezone(UTC).isoformat()


def parse_sttm(text: str) -> list[dict]:
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        price = (r.get("ex_ante_market_price") or "").strip()
        if not price:
            continue
        hub = (r.get("hub_identifier") or "").strip()
        rows.append({
            "gas_date": datetime.strptime(r["gas_date"].strip(), "%d %b %Y").date().isoformat(),
            "hub": hub, "source": "STTM_EX_ANTE", "price_gj": float(price),
            "publish_time_utc": _sttm_dt(r["approval_datetime"]), "region_id": STTM_HUB_REGION.get(hub),
        })
    return rows


def parse_gsh(text: str, cutoff_iso: str) -> list[dict]:
    rows, hdr = [], {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 3 and row[0] == "I" and row[1] == "GSH" and row[2] == "BENCHMARK_PRICE":
            hdr = {c: i for i, c in enumerate(row)}
        elif hdr and len(row) > 3 and row[0] == "D" and row[1] == "GSH" and row[2] == "BENCHMARK_PRICE":
            price = row[hdr["BENCHMARK_PRICE"]].strip()
            gd = row[hdr["GAS_DATE"]].strip().strip('"')[:10]
            if not price or gd.replace("/", "-") < cutoff_iso:
                continue
            rows.append({
                "gas_date": datetime.strptime(gd, "%Y/%m/%d").date().isoformat(),
                "hub": row[hdr["PRODUCT_LOCATION"]].strip().strip('"'), "source": "GSH_BENCHMARK",
                "price_gj": float(price), "publish_time_utc": _mms_dt(row[hdr["LASTCHANGED"]]),
                "region_id": None,
            })
    return rows


def main() -> int:
    rows = parse_sttm(_get(STTM_CSV).decode("utf-8", "ignore"))
    listing = _get(GSH_DIR).decode("utf-8", "ignore")
    latest = sorted(set(re.findall(r"(PUBLIC_WALLUMBILLABENCHMARKPRICE_\d+_\d+\.zip)", listing)))[-1]
    z = zipfile.ZipFile(io.BytesIO(_get(GSH_DIR + latest)))
    cutoff = (datetime.now(UTC) - timedelta(days=GSH_RECENT_DAYS)).date().isoformat()
    rows += parse_gsh(z.read(z.namelist()[0]).decode("utf-8", "ignore"), cutoff)
    for r in rows:
        r["gas_implied_price_mwh"] = round(r["price_gj"] * HEAT_RATE, 2)
    out = {"feed": "gas", "generated_at_utc": datetime.now(UTC).isoformat(),
           "confidence_tier": "TIER_A_AUTHORITATIVE", "heat_rate_gj_per_mwh": HEAT_RATE, "rows": rows}
    with open("exo/data/gas.json", "w") as f:
        json.dump(out, f, indent=0)
    print(f"wrote exo/data/gas.json: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
