"""voltsignal exogenous — STANDALONE PASA puller (PUBLIC repo). NO private imports, NO DB, NO secret.

MTPASA region availability (scheduled PASA availability vs 10%/50% POE demand per region per forecast day),
near-horizon slice. Writes thin exo/data/pasa.json. Publishes raw public availability/demand. Vintage keys:
valid_date (forecast day) + publish_time = PUBLISH_DATETIME (the PASA run) — the leakage-guard input.
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
MTPASA_DIR = "https://nemweb.com.au/Reports/Current/MTPASA_RegionAvailability/"
HORIZON_DAYS = 90


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})  # noqa: S310
    with urllib.request.urlopen(req, timeout=90, context=_CTX) as r:  # noqa: S310
        return r.read()


def _unzip(raw: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(raw))
    data = z.read(z.namelist()[0])
    if data[:2] == b"PK":
        z2 = zipfile.ZipFile(io.BytesIO(data))
        data = z2.read(z2.namelist()[0])
    return data.decode("utf-8", "ignore")


def _dt(s: str) -> str:
    return datetime.strptime(s.strip().strip('"'), "%Y/%m/%d %H:%M:%S").replace(
        tzinfo=AEST).astimezone(UTC).isoformat()


def _date(s: str) -> str:
    return datetime.strptime(s.strip().strip('"')[:10], "%Y/%m/%d").date().isoformat()


def _f(s: str):
    s = s.strip().strip('"')
    return float(s) if s else None


def main() -> int:
    listing = _get(MTPASA_DIR).decode("utf-8", "ignore")
    f = sorted(set(re.findall(r"(PUBLIC_MTPASAREGIONAVAILABILITY_\d+_\d+\.zip)", listing)))[-1]
    text = _unzip(_get(MTPASA_DIR + f))
    cutoff = (datetime.now(UTC) + timedelta(days=HORIZON_DAYS)).date().isoformat()
    hdr, out_rows = {}, []
    for row in csv.reader(io.StringIO(text)):
        if len(row) > 3 and row[0] == "I" and row[1] == "MTPASA" and row[2] == "REGIONAVAILABILITY":
            hdr = {c: i for i, c in enumerate(row)}
        elif hdr and len(row) > 3 and row[0] == "D" and row[1] == "MTPASA" \
                and row[2] == "REGIONAVAILABILITY":
            avail = _f(row[hdr["PASAAVAILABILITY_SCHEDULED"]])
            vd = _date(row[hdr["DAY"]])
            if avail is None or vd > cutoff:
                continue
            out_rows.append({"valid_date": vd, "publish_time_utc": _dt(row[hdr["PUBLISH_DATETIME"]]),
                             "region_id": row[hdr["REGIONID"]].strip().strip('"'),
                             "pasa_availability_mw": avail, "demand10_mw": _f(row[hdr["DEMAND10"]]),
                             "demand50_mw": _f(row[hdr["DEMAND50"]]),
                             "confidence_tier": "TIER_A_AUTHORITATIVE"})
    out = {"feed": "pasa", "generated_at_utc": datetime.now(UTC).isoformat(),
           "source_file": f, "rows": out_rows}
    with open("exo/data/pasa.json", "w") as fh:
        json.dump(out, fh, indent=0)
    print(f"wrote exo/data/pasa.json: {len(out_rows)} rows from {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
