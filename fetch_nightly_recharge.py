import json
import sqlite3
import requests
from datetime import date, timedelta
from pathlib import Path

# --- Paths ---
BASE_DIR = Path(__file__).parent
TOKENS_FILE = BASE_DIR / "tokens.json"
DB_FILE = BASE_DIR / "polar.db"

# --- Token handling ---

def load_tokens():
    with open(TOKENS_FILE) as f:
        return json.load(f)

def save_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

def refresh_access_token(tokens):
    print("Refreshing access token...")
    response = requests.post(
        "https://auth.polar.com/oauth/token",
        auth=(tokens["client_id"], tokens["client_secret"]),
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"]
        }
    )
    response.raise_for_status()
    new_tokens = response.json()
    tokens["access_token"] = new_tokens["access_token"]
    # Polar may return a new refresh token too — save it if present
    if "refresh_token" in new_tokens:
        tokens["refresh_token"] = new_tokens["refresh_token"]
    save_tokens(tokens)
    print("Token refreshed and saved.")
    return tokens

# --- API call ---

def fetch_chunk(tokens, from_date, to_date):
    """Fetch one chunk of nightly recharge data. Refreshes token if needed."""
    url = "https://www.polaraccesslink.com/v4/data/nightly-recharge-results"
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "Accept": "application/json"
    }
    params = {"from": from_date, "to": to_date}

    response = requests.get(url, headers=headers, params=params)

    # If token expired, refresh and retry once
    if response.status_code == 401:
        tokens = refresh_access_token(tokens)
        headers["Authorization"] = f"Bearer {tokens['access_token']}"
        response = requests.get(url, headers=headers, params=params)

    response.raise_for_status()
    return response.json(), tokens

def fetch_nightly_recharge(tokens, from_date, to_date):
    """Fetch nightly recharge in 28-day chunks to stay within API limits."""
    from_dt = date.fromisoformat(from_date)
    to_dt = date.fromisoformat(to_date)
    chunk_days = timedelta(days=28)

    all_results = []
    chunk_start = from_dt

    while chunk_start <= to_dt:
        chunk_end = min(chunk_start + chunk_days, to_dt)
        print(f"  Fetching {chunk_start} to {chunk_end}...")
        data, tokens = fetch_chunk(tokens, chunk_start.isoformat(), chunk_end.isoformat())
        all_results.extend(data.get("nightlyRechargeResults", []))
        chunk_start = chunk_end + timedelta(days=1)

    return {"nightlyRechargeResults": all_results}, tokens

# --- Database insert ---

def insert_nightly_recharge(results):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    inserted = 0
    skipped = 0

    for r in results.get("nightlyRechargeResults", []):
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO nightly_recharge (
                    sleep_result_date,
                    ans_status,
                    recovery_indicator,
                    recovery_indicator_sub_level,
                    ans_rate,
                    mean_nightly_recovery_rri,
                    mean_nightly_recovery_rmssd,
                    mean_baseline_rri,
                    sd_baseline_rri,
                    mean_baseline_rmssd,
                    sd_baseline_rmssd,
                    mean_baseline_respiration_interval,
                    sd_baseline_respiration_interval
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r["sleepResultDate"],
                r["ansStatus"],
                r["recoveryIndicator"],
                r["recoveryIndicatorSubLevel"],
                r["ansRate"],
                r["meanNightlyRecoveryRri"],
                r["meanNightlyRecoveryRmssd"],
                r["meanBaselineRri"],
                r["sdBaselineRri"],
                r["meanBaselineRmssd"],
                r["sdBaselineRmssd"],
                r["meanBaselineRespirationInterval"],
                r["sdBaselineRespirationInterval"]
            ))
            if cursor.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"Error inserting {r.get('sleepResultDate')}: {e}")

    conn.commit()
    conn.close()
    print(f"Done: {inserted} rows inserted, {skipped} already existed.")

# --- Main ---

if __name__ == "__main__":
    # Fetch the last 90 days (full window available at registration)
    to_date = date.today().isoformat()
    from_date = (date.today() - timedelta(days=90)).isoformat()
    print(f"Fetching nightly recharge from {from_date} to {to_date}...")

    tokens = load_tokens()
    results, tokens = fetch_nightly_recharge(tokens, from_date, to_date)

    count = len(results.get("nightlyRechargeResults", []))
    print(f"Received {count} nights of data.")

    insert_nightly_recharge(results)