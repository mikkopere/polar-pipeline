# Polar Data Pipeline

A personal project for fetching, storing, and analysing training and recovery data from a Polar sports watch using the [Polar AccessLink v4 API](https://www.polar.com/polar-api-v4/).

Built as a learning project covering Python, REST APIs, OAuth2, SQLite, and SQL.

---

## What it does

- Fetches data from Polar AccessLink v4: nightly recharge, training sessions, sleep-wake vectors, 24/7 heart rate, PPI (pulse-to-pulse interval) HRV, orthostatic tests, and fitness test results
- Fetches per-second exercise detail from the v3 exercises API: HR/speed/cadence/altitude samples, GPS route, HR zones, and Polar's own cardio load (recent sessions only)
- Stores everything in a local SQLite database (`polar.db`)
- Computes training load metrics: TRIMP (Banister), ATL (acute training load / fatigue), CTL (chronic training load / fitness), and TSB (training stress balance / form)
- Handles OAuth2 token refresh automatically
- Visualises training load and recovery trends

---

## Project structure

```
polar-pipeline/
├── main.py                  # Entry point — fetch data and recompute metrics
├── fetch_all.py             # Fetches all data types from Polar API
├── compute_ctl_atl.py       # Computes TRIMP, ATL, CTL, TSB
├── polar_client.py          # Polar API client with automatic token refresh
├── plot_training_load.py    # Three-panel matplotlib training load chart
├── dashboard.py             # Streamlit dashboard (readiness, training load, recovery & HRV, sessions)
├── schema.sql               # SQLite schema — recreates database from scratch
├── environment.yml          # Conda environment specification
├── .gitignore
├── README.md
└── tokens.json              # NOT in git — create manually (see setup below)
```

---

## Database schema

Seven tables in `polar.db`:

| Table | Contents |
|---|---|
| `training_sessions` | One row per workout (sport, duration, HR, calories, distance) |
| `hr_zones` | Time in each HR zone per session (currently empty — see API notes) |
| `nightly_recharge` | ANS charge, recovery indicator, nocturnal RMSSD and baseline values |
| `sleep` | Sleep duration and score (placeholder — see API notes) |
| `sleep_wake_events` | Raw sleep/wake state transitions per night and device |
| `continuous_hr` | 24/7 heart rate samples (~10 s resolution) |
| `hrv_windows` | 5-minute RMSSD / mean-HR windows aggregated from PPI samples at fetch time (raw PPI ≈ 80k samples/day is not stored) |
| `exercise_samples` | Per-second HR, speed, altitude, cadence per exercise |
| `exercise_route` | GPS route points per exercise |
| `orthostatic_tests` | Morning HRV test results (RMSSD supine/standing, RR intervals) |
| `daily_physical` | Weight, resting HR, VO2max from fitness tests |
| `daily_training_load` | Computed TRIMP, ATL, CTL, TSB per day |

---

## Setup

### 1. Prerequisites

Clone the repo and create the conda environment:

```bash
git clone https://github.com/YOUR_USERNAME/polar-pipeline.git
cd polar-pipeline
conda env create -f environment.yml
conda activate polar
```

### 2. Polar AccessLink credentials

You need a Polar AccessLink API client. Create one at [admin.polaraccesslink.com](https://admin.polaraccesslink.com) with:
- Redirect URI: `http://localhost:5000/oauth2_callback`
- Data subscriptions: Exercise data, Daily activity data, Physical information data

### 3. Create tokens.json

Create `tokens.json` in the project folder with your credentials:

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "access_token": "",
  "refresh_token": "",
  "expires_at": 0
}
```

### 4. Authorise with Polar (first time only)

Open this URL in a browser (substitute your client_id):

```
https://auth.polar.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&scope=sleep:read%20nightly_recharge:read%20training_sessions:read%20tests:read%20activity:read%20profile:read%20continuous_samples:read%20devices:read%20ppi_data:read%20routes:read%20sports:read&redirect_uri=http://localhost:5000/oauth2_callback
```

After authorising, copy the `code` parameter from the browser address bar and exchange it for tokens:

```bash
curl -X POST https://auth.polar.com/oauth/token \
  -u "YOUR_CLIENT_ID:YOUR_CLIENT_SECRET" \
  -d "grant_type=authorization_code&code=YOUR_CODE&redirect_uri=http://localhost:5000/oauth2_callback"
```

Save the returned `access_token` and `refresh_token` into `tokens.json`. After this, token refresh is fully automatic.

### 5. Register your Polar user (first time only)

```bash
curl -X POST https://www.polaraccesslink.com/v3/users \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{"member-id": "YOUR_NAME"}'
```

### 6. Create the database

```bash
sqlite3 polar.db ".read schema.sql"
```

---

## Daily use

Sync your Polar watch via the Polar Flow app, then run:

```bash
python main.py
```

This fetches any new data and recomputes ATL/CTL/TSB automatically.

To generate the training load chart:

```bash
python plot_training_load.py
```

To open the interactive dashboard in a browser:

```bash
streamlit run dashboard.py
```

---

## Training load metrics

Since Polar's cardio load is not available through any public API endpoint (see API notes below), TRIMP is computed using the Banister formula for males:

$$\text{HRR} = \frac{\text{HR}_{avg} - \text{HR}_{rest}}{\text{HR}_{max} - \text{HR}_{rest}}$$

$$\text{TRIMP} = t_{min} \times \text{HRR} \times 0.64 \times e^{1.92 \times \text{HRR}}$$

ATL and CTL are exponentially weighted averages of daily TRIMP load:

$$\text{ATL}_t = \text{ATL}_{t-1} + (L_t - \text{ATL}_{t-1})(1 - e^{-1/7})$$

$$\text{CTL}_t = \text{CTL}_{t-1} + (L_t - \text{CTL}_{t-1})(1 - e^{-1/42})$$

$$\text{TSB}_t = \text{CTL}_{t-1} - \text{ATL}_{t-1}$$

HR rest (55 bpm) and max (171 bpm) are taken from Polar profile settings.

---

## Polar API notes

### What works in v4
- Nightly recharge: full ANS charge, recovery indicator, nocturnal RMSSD, baseline values
- Training sessions: summary data (duration, HR, calories, distance, sport)
- Tests: orthostatic test results, fitness tests (VO2max, weight, resting HR)
- Sleep: raw sleep-wake state vectors (see limitations below)

### API version policy

The pipeline uses **v4 for everything it can**: nightly recharge, sleep-wake vectors, training session summaries, and tests (orthostatic + fitness) all come from v4 endpoints. The single exception is per-exercise detail, which v4 does not offer at all. Verified against the [official v4 documentation](https://www.polar.com/polar-api-v4/) and by probing:

- `/v4/data/training-sessions/list` is the **only** training-session endpoint in v4 — there is no per-session or per-exercise endpoint for samples, HR zones, GPS track, or cardio load. Candidate paths (`data/exercises`, `data/exercise-samples`, `data/training-sessions/{id}`, …) return 404, and the list endpoint silently ignores `samples`/`zones`/`route` query parameters. Hence exercise detail comes from the v3 exercises API.
- `/v4/data/routes/{routeId}` (scope `routes:read`) returns **user-saved navigation routes** (Polar Flow favorites for on-device guidance), *not* GPS tracks recorded during sessions — so it is of no use here.
- `/v4/data/continuous-samples?features=heart-rate-samples` (scope `continuous_samples:read`) returns 24/7 heart rate (roughly 10-second sampling, entries without `heartRate`/`offsetMillis` mark gaps) — fetched into `continuous_hr`. Too sparse and untimed for per-session analysis, so exercise detail still needs v3.
- `/v4/data/ppi-samples?features=samples` (scope `ppi_data:read`) returns raw pulse-to-pulse intervals with quality flags — but only one day per request, so the fetcher walks day by day and skips days already stored. Around 80k samples/day, aggregated at fetch time into 5-minute RMSSD windows (`hrv_windows`) using beats with skin contact, no movement, and error estimate ≤ 30 ms.

### Known API limitations

**Cardio load and per-second detail available via v3 only, and only for recently synced sessions.**
The v4 training sessions endpoint (`/v4/data/training-sessions/list`) has no per-session detail endpoint, no HR zones, and no cardio load. However, the v3 exercises API (`/v3/exercises`) lists exercises synced after client registration (last 30 days), and `/v3/exercises/{id}?samples=true&zones=true&route=true` returns Polar's cardio load (`training_load_pro`), HR zone times with limits, per-second samples (type 0 = HR bpm, 1 = speed km/h, 2 = cadence rpm, 3 = altitude m, 9 = temperature, 10 = distance, 11 = RR intervals), and the GPS route. Older sessions have summary data only, so the computed Banister TRIMP remains the basis for ATL/CTL/TSB.

**Sleep scoring not accessible.**
The `/v4/data/sleeps` endpoint returns only a list of dates. The `/v4/data/sleep-wake-vectors` endpoint (max 7 days per request) returns raw sleep/wake state transitions as millisecond offsets from the start of the record date, one vector per device. Polar's sleep score and sleep stages are computed internally and not exposed through the public API. The pipeline stores the raw transitions in `sleep_wake_events`; the dashboard derives the main sleep period, time asleep, awakenings, and efficiency from them, picking the most plausible device record per night.

**Historical data window.**
The API only provides data from the 90 days prior to client registration. Older data is not accessible through any API endpoint.

**Endpoint date format quirks.**
- Plain dates (`2026-06-01`): nightly recharge, sleep, tests
- Datetime strings (`2026-06-01T00:00:00`): training sessions

### Sport codes
| Code | Sport |
|---|---|
| 15 | Strength training |
| 18 | Road/indoor cycling |
| 38 | Road cycling |
| 83 | Auto-detected activity (Loop Gen 2) |
| 177 | E-bike |

---

## Future work

- **SwiftUI app**: iOS app as the long-term interface goal
- **Automation**: daily cron job on Mac Mini