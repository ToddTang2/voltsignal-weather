# voltsignal-weather

Public, generic weather feature puller for VoltSignal. **No secrets, no app/model edge code** — just:
fetch Open-Meteo forecast-vintage data for the 5 NEM regions, derive documented features
(temp/cdd/hdd, wind_speed_100m/wind_power_proxy/low_wind_flag, ghi/solar_proxy — `features.py`, kept
byte-identical to the app), and UPSERT vintage-keyed rows into `weather_features`.

Data is public weather (CC-BY Open-Meteo) + textbook transforms. The proprietary edge stays in the
private app repo.

## How it works
- **Schedule:** `.github/workflows/pull.yml` cron at 05:20/11:20/17:20/23:20 UTC — after each Open-Meteo
  global run (init 00/06/12/18 UTC, available ~init+4–6h) becomes available.
- **Vintage keys:** every row carries `valid_time / issue_time / publish_time / lead_time`.
- **publish_time = issue + PUBLISH_LATENCY_HOURS** (default 5h, the verified realistic Open-Meteo
  global-model availability). This is the downstream **leakage guard** input — it reflects when a forecast
  was *actually available*, never nominal init.
- **Idempotent:** `ON CONFLICT ... DO UPDATE` upsert — re-runs never duplicate.
- **Graceful failure:** a region that fails to fetch/parse is logged and skipped — never written stale-as-fresh.

## Credential (scoped, write-only)
`WEATHER_DB_URL` (GitHub Secret) connects as a least-privilege Supabase role with **INSERT/UPDATE on
`weather_features` only** — no read of any other table. A leak is containable to the one table.

## Run locally (off-box)
```
pip install -r requirements.txt
WEATHER_DB_URL='postgresql://weather_writer:...@<pooler-host>:6543/postgres' python pull.py
```
