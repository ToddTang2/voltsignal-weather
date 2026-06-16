"""voltsignal exogenous — STANDALONE bid-stack puller (PUBLIC repo). NO private imports, NO DB, NO secret.

Does the FULL heavy job in the (unmetered) public Action: fetch Bidmove_Complete (~130 MB), parse
BIDDAYOFFER_D price bands + BIDPEROFFER_D latest-rebid band availabilities, join to the vendored PUBLIC
DUID→region map (exo/duid_region.json from AEMO registration), and bucket each region's ENERGY offers into
the standard price buckets → the thin ~1,440-row region supply curve. Writes exo/data/bidstack.json; the box
upserts it. publish_time = settlement_date + 1 day 04:30 AEST (the box T+1 guard blocks intraday).
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import ssl
import urllib.request
import zipfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone

import certifi

_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "voltsignal-exo/1.0 (research)"
AEST = timezone(timedelta(hours=10))
BID_DIR = "https://nemweb.com.au/Reports/Current/Bidmove_Complete/"
_HERE = os.path.dirname(os.path.abspath(__file__))
_BUCKETS = [("mw_0_50", 50.0), ("mw_50_100", 100.0), ("mw_100_300", 300.0),
            ("mw_300_1000", 1000.0), ("mw_1000_plus", float("inf"))]
BUCKET_KEYS = ["mw_neg", *[b[0] for b in _BUCKETS]]


def _bucket(price: float) -> str:
    if price < 0:
        return "mw_neg"
    for key, hi in _BUCKETS:
        if price < hi:
            return key
    return "mw_1000_plus"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=240, context=_CTX) as r:  # noqa: S310
        return r.read()


def _csv_text(raw: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(raw))
    data = z.read(z.namelist()[0])
    if data[:2] == b"PK":
        z2 = zipfile.ZipFile(io.BytesIO(data))
        data = z2.read(z2.namelist()[0])
    return data.decode("utf-8", "ignore")


def _publish(sd) -> str:
    rel = (datetime(sd.year, sd.month, sd.day) + timedelta(days=1)).replace(hour=4, minute=30, tzinfo=AEST)
    return rel.astimezone(UTC).isoformat()


def main() -> int:
    duid_region = json.load(open(os.path.join(_HERE, "duid_region.json")))["map"]
    listing = _get(BID_DIR).decode("utf-8", "ignore")
    f = sorted(set(re.findall(r"(PUBLIC_BIDMOVE_COMPLETE_\d+_\d+\.zip)", listing)))[-1]
    text = _csv_text(_get(BID_DIR + f))
    dayh = perh = None
    bands: dict[tuple, list[float]] = {}
    pers: dict[tuple, dict] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4:
            continue
        tag = row[2]
        if row[0] == "I" and tag == "BIDDAYOFFER_D":
            dayh = {c: i for i, c in enumerate(row)}
        elif row[0] == "I" and tag == "BIDPEROFFER_D":
            perh = {c: i for i, c in enumerate(row)}
        elif row[0] == "D" and tag == "BIDDAYOFFER_D" and dayh and row[dayh["BIDTYPE"]] == "ENERGY":
            k = (row[dayh["DUID"]], row[dayh["SETTLEMENTDATE"]], row[dayh["OFFERDATE"]], row[dayh["VERSIONNO"]])
            bands[k] = [float(row[dayh[f"PRICEBAND{i}"]] or 0) for i in range(1, 11)]
        elif row[0] == "D" and tag == "BIDPEROFFER_D" and perh and row[perh["BIDTYPE"]] == "ENERGY":
            duid = row[perh["DUID"]]
            if duid not in duid_region:
                continue
            sd, pid = row[perh["SETTLEMENTDATE"]], int(row[perh["PERIODID"]])
            od, vn = row[perh["OFFERDATE"]], row[perh["VERSIONNO"]]
            cur = pers.get((duid, pid))
            if cur is None or (od, vn) > cur["v"]:
                pers[(duid, pid)] = {"v": (od, vn), "sd": sd, "k": (duid, sd, od, vn),
                                     "avail": [float(row[perh[f"BANDAVAIL{i}"]] or 0) for i in range(1, 11)]}
    agg: dict[tuple, dict[str, float]] = {}
    totals: dict[tuple, float] = defaultdict(float)
    for (duid, pid), e in pers.items():
        bk = bands.get(e["k"])
        if not bk:
            continue
        region = duid_region[duid]
        key = (e["sd"], pid, region)
        bucket = agg.setdefault(key, {k: 0.0 for k in BUCKET_KEYS})
        for price, mw in zip(bk, e["avail"], strict=False):
            if mw and mw > 0:
                bucket[_bucket(price)] += mw
                totals[key] += mw
    rows = []
    for (sd, pid, region), bucket in agg.items():
        d = datetime.strptime(sd.strip().strip('"')[:10], "%Y/%m/%d").date()
        rows.append({"settlement_date": d.isoformat(), "period_id": pid, "region_id": region,
                     "total_offered_mw": round(totals[(sd, pid, region)], 3),
                     **{k: round(bucket[k], 3) for k in BUCKET_KEYS},
                     "publish_time_utc": _publish(d), "confidence_tier": "TIER_A_AUTHORITATIVE"})
    out = {"feed": "bidstack", "generated_at_utc": datetime.now(UTC).isoformat(),
           "source_file": f, "rows": rows}
    with open("exo/data/bidstack.json", "w") as fh:
        json.dump(out, fh, indent=0)
    print(f"wrote exo/data/bidstack.json: {len(rows)} region-interval rows from {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
