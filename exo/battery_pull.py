"""voltsignal exogenous — STANDALONE battery puller (PUBLIC repo). NO private imports, NO DB, NO secret.

DUID-SPLIT: publishes only per-battery-DUID RAW charge/discharge (net dispatch + available MW) for the public
battery DUIDs (identities from exo/battery_duids.json). NO region map and NO SoC method here — the box applies
the PRIVATE DUID→region map and computes the SoC/headroom proxy on ingest. Writes thin exo/data/battery.json.
publish_time = settlement_date + 1 day 04:30 AEST (the public next-day release; the box guard blocks intraday).
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
from datetime import UTC, datetime, timedelta, timezone

import certifi

_CTX = ssl.create_default_context(cafile=certifi.where())
_UA = "voltsignal-exo/1.0 (research)"
AEST = timezone(timedelta(hours=10))
DISP_DIR = "https://nemweb.com.au/Reports/Current/Next_Day_Dispatch/"
_HERE = os.path.dirname(os.path.abspath(__file__))


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=180, context=_CTX) as r:  # noqa: S310
        return r.read()


def _csv_text(raw: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(raw))
    data = z.read(z.namelist()[0])
    if data[:2] == b"PK":
        z2 = zipfile.ZipFile(io.BytesIO(data))
        data = z2.read(z2.namelist()[0])
    return data.decode("utf-8", "ignore")


def _nem(s: str) -> datetime:
    return datetime.strptime(s.strip().strip('"'), "%Y/%m/%d %H:%M:%S").replace(tzinfo=AEST)


def _publish(sd) -> str:
    rel = (datetime(sd.year, sd.month, sd.day) + timedelta(days=1)).replace(hour=4, minute=30, tzinfo=AEST)
    return rel.astimezone(UTC).isoformat()


def main() -> int:
    bat = set(json.load(open(os.path.join(_HERE, "battery_duids.json")))["duids"])
    listing = _get(DISP_DIR).decode("utf-8", "ignore")
    f = sorted(set(re.findall(r"(PUBLIC_NEXT_DAY_DISPATCH_\d+_\d+\.zip)", listing)))[-1]
    text = _csv_text(_get(DISP_DIR + f))
    hdr, rows = None, []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4:
            continue
        if row[0] == "I" and row[2] == "UNIT_SOLUTION":
            hdr = {c: i for i, c in enumerate(row)}
        elif hdr and row[0] == "D" and row[2] == "UNIT_SOLUTION":
            if row[hdr["INTERVENTION"]].strip() not in ("0", "") or row[hdr["DUID"]] not in bat:
                continue
            iv = _nem(row[hdr["SETTLEMENTDATE"]])
            sd = iv.date()
            rows.append({"duid": row[hdr["DUID"]], "settlement_date": sd.isoformat(),
                         "interval_end_utc": iv.astimezone(UTC).isoformat(),
                         "net_dispatch_mw": float(row[hdr["TOTALCLEARED"]] or 0),
                         "available_mw": float(row[hdr["AVAILABILITY"]] or 0),
                         "publish_time_utc": _publish(sd)})
    out = {"feed": "battery", "generated_at_utc": datetime.now(UTC).isoformat(),
           "source_file": f, "rows": rows}
    with open("exo/data/battery.json", "w") as fh:
        json.dump(out, fh, indent=0)
    print(f"wrote exo/data/battery.json: {len(rows)} per-DUID rows ({len(bat)} battery DUIDs) from {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
