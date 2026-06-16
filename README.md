# voltsignal-weather — public exogenous data pullers

Standalone, **no-secret** pullers for the VoltSignal NEM analytics platform. Each puller runs on a GitHub
Actions runner (unlimited minutes, never the app box), fetches a **public** upstream (NEMWeb, Open-Meteo,
CER, Snowy/Hydro Tas), derives only **standard/textbook** features, and **commits a thin JSON artifact**
to `exo/data/<feed>.json` via the automatic `GITHUB_TOKEN`. Nothing here writes a database and nothing
here holds a secret.

## Design (publish-and-ingest, zero secrets)

```
public source ──(Action: fetch+parse+derive)──► exo/data/<feed>.json ──commit via GITHUB_TOKEN──► this repo
                                                                                │ raw.githubusercontent.com (public)
                                                  the VoltSignal box ingests ◄──┘  and upserts via its own creds
```

- **No DB writes here.** The app box pulls each latest `exo/data/*.json` over `raw.githubusercontent.com`
  (public read, no auth) and upserts into its own Postgres using its own existing credentials.
- **No secrets.** The only "auth" is the workflow's automatic `GITHUB_TOKEN` (Settings → Actions → Workflow
  permissions → *Read and write*), used solely to commit the JSON files.
- **No private code.** Pullers are self-contained (stdlib `urllib` + `certifi`, `xlrd` for the Hydro Tas
  xls); `features.py` is vendored **byte-identical** from the app so published features match the app exactly.
- **Leakage-safe vintages.** Forecast feeds carry `valid_time / issue_time / publish_time`; `publish_time`
  reflects when the data was *actually* available (never nominal init) — the downstream leakage guard.

## Feeds (`exo/<feed>_pull.py` → `exo/data/<feed>.json`)

| feed | upstream | cadence |
|---|---|---|
| gas | NEMWeb STTM + GSH benchmark | daily |
| pasa | NEMWeb MTPASA region availability | 6-hourly |
| hydro | Snowy `getData.php` + Hydro Tas TEIS xls | weekly |
| der | CER small-scale postcode capacity | monthly |
| battery | NEMWeb Next_Day_Dispatch (per-DUID raw) | daily T+1 |
| bidstack | NEMWeb Bidmove_Complete (region supply curve) | daily T+1 |
| weather | Open-Meteo previous-runs forecast vintages | ~6-hourly |

Schedules + the file commit live in `.github/workflows/exo-pullers.yml`. `duid_region.json` /
`battery_duids.json` are public AEMO registration maps used by the bidstack/battery pullers.

## Run one locally

```
pip install -r requirements.txt
python exo/gas_pull.py        # writes exo/data/gas.json — no DB, no secret
```
