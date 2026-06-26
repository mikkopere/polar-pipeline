# Polar Data Pipeline

A personal project for fetching, storing, and analysing training and recovery data from a Polar sports watch using the [Polar AccessLink v4 API](https://www.polar.com/polar-api-v4/).

Built as a learning project covering Python, REST APIs, OAuth2, SQLite, and SQL.

---

## What it does

- Fetches data from Polar AccessLink v4: nightly recharge, training sessions, orthostatic tests, and fitness test results
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
https://auth.polar.com/oauth/authorize?client_id=YOUR_CLIENT_ID&response_type=code&scope=sleep:read%20nightly_recharge:read%20training_sessions:read%20tests:read%20activity:read%20profile:read%20continuous_samples:read%20devices:read&redirect_uri=http://localhost:5000/oauth2_callback
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

### Known API limitations

**Cardio load (TRIMP) not accessible via any public API.**
The v4 training sessions endpoint (`/v4/data/training-sessions/list`) is the only training session endpoint — there is no per-session detail endpoint. HR zones and Polar's own cardio load values are not returned by any v4 endpoint. An endpoint at `/v4/data/users/{id}/cardio-load` suggested elsewhere does not exist (returns 404). Polar computes cardio load internally from raw HR samples and serves it through private APIs to their own apps. The v3 transaction model gives access to raw HR samples from which it could be computed, but only for sessions synced after client registration.

**Sleep scoring not accessible.**
The `/v4/data/sleeps` endpoint returns only a list of dates. The `/v4/data/sleep-wake-vectors` endpoint returns raw sleep/wake state transitions as millisecond offsets. Polar's sleep score is computed internally and not exposed through the public API. Total sleep time and fragmentation can be derived from the state vectors but requires additional processing.

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
| 83 | Auto-detected activity (Loop Gen 2) |
| 177 | E-bike |

---

## Future work

- **Streamlit dashboard**: browser-based daily summary showing recovery status, TSB, and HRV trend
- **Sleep processing**: derive total sleep time and fragmentation from sleep-wake vectors
- **SwiftUI app**: iOS app as the long-term interface goal
- **Automation**: daily cron job on Mac Mini