# Polar Data Pipeline

A personal project for fetching, storing, and analysing training and recovery data from a Polar sports watch using the [Polar AccessLink v4 API](https://www.polar.com/polar-api-v4/).

Built as a learning project covering Python, REST APIs, OAuth2, SQLite, and SQL.

---

## What it does

- Fetches data from Polar AccessLink v4: nightly recharge, training sessions, orthostatic tests, and fitness test results
- Stores everything in a local SQLite database (`polar.db`)
- Computes training load metrics: TRIMP (Banister), ATL (acute training load / fatigue), CTL (chronic training load / fitness), and TSB (training stress balance / form)
- Handles OAuth2 token refresh automatically

---

## Project structure

```
polar-pipeline/
├── main.py                 # Entry point — fetch data and recompute metrics
├── fetch_all.py            # Fetches all data types from Polar API
├── compute_ctl_atl.py      # Computes TRIMP, ATL, CTL, TSB
├── polar_client.py         # Polar API client with automatic token refresh
├── schema.sql              # SQLite schema — recreates database from scratch
├── .gitignore
├── README.md
└── tokens.json             # NOT in git — create manually (see setup below)
```

---

## Database schema

Seven tables in `polar.db`:

| Table | Contents |
|---|---|
| `training_sessions` | One row per workout (sport, duration, HR, calories, distance) |
| `hr_zones` | Time in each HR zone per session |
| `nightly_recharge` | ANS charge, recovery indicator, nocturnal RMSSD |
| `sleep` | Sleep duration and score (placeholder — see notes) |
| `orthostatic_tests` | Morning HRV test results (RMSSD supine/standing, RR intervals) |
| `daily_physical` | Weight, resting HR, VO2max from fitness tests |
| `daily_training_load` | Computed TRIMP, ATL, CTL, TSB per day |

---

## Setup

### 1. Prerequisites

Python 3.9+ and one external library:

```bash
pip install requests
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

Save the returned `access_token` and `refresh_token` into `tokens.json`. After this, token refresh is automatic.

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

---

## Training load metrics

TRIMP (TRaining IMPulse) is computed using the Banister formula for males:

$$\text{HRR} = \frac{\text{HR}_{avg} - \text{HR}_{rest}}{\text{HR}_{max} - \text{HR}_{rest}}$$

$$\text{TRIMP} = t_{min} \times \text{HRR} \times 0.64 \times e^{1.92 \times \text{HRR}}$$

ATL and CTL are exponentially weighted averages of daily TRIMP load:

$$\text{ATL}_t = \text{ATL}_{t-1} + (L_t - \text{ATL}_{t-1})(1 - e^{-1/7})$$

$$\text{CTL}_t = \text{CTL}_{t-1} + (L_t - \text{CTL}_{t-1})(1 - e^{-1/42})$$

$$\text{TSB}_t = \text{CTL}_{t-1} - \text{ATL}_{t-1}$$

HR rest and max are taken from Polar profile settings (resting HR 55 bpm, max HR 171 bpm).

---

## Known gaps and future work

- **Sleep scoring**: The `sleep` table is a placeholder. The Polar v4 API returns raw sleep-wake state vectors rather than summary scores. Computing total sleep time and fragmentation from these vectors is planned.
- **Cardio load**: Polar's own TRIMP values are not returned by the training session list endpoint. Fetching per-session detail to retrieve these and compare against computed TRIMP is planned.
- **Visualisation**: Plotting CTL/ATL/TSB and recovery trends over time using matplotlib or R/ggplot2.
- **Automation**: Scheduling `main.py` as a daily cron job.

---

## Notes

- The API only provides data from the 90 days prior to client registration. Historical data beyond that window is not accessible via the API.
- The Polar AccessLink v4 API requires datetime strings (`2026-06-01T00:00:00`) for training sessions but plain dates (`2026-06-01`) for nightly recharge and tests.
- Sport codes: 15 = strength training, 18 = road/indoor cycling, 177 = e-bike, 83 = auto-detected activity (Loop Gen 2).