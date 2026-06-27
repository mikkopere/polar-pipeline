import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from polar_client import PolarClient

DB_FILE = Path(__file__).parent / "polar.db"
CHUNK = timedelta(days=28)

# --- Helpers ---

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def date_chunks(from_date, to_date):
    """Split a date range into 28-day chunks."""
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    while start <= end:
        yield start.isoformat(), min(start + CHUNK, end).isoformat()
        start = start + CHUNK + timedelta(days=1)

def fetch_chunked(client, endpoint, key, from_date, to_date):
    """Fetch data in 28-day chunks and merge results."""
    results = []
    for chunk_from, chunk_to in date_chunks(from_date, to_date):
        print(f"  Fetching {chunk_from} to {chunk_to}...")
        data = client.get(endpoint, params={"from": chunk_from, "to": chunk_to})
        if data:
            results.extend(data.get(key, []))
    return results

# --- Nightly recharge ---

def fetch_nightly_recharge(client, from_date, to_date):
    print("\nFetching nightly recharge...")
    rows = fetch_chunked(client,
                         "data/nightly-recharge-results",
                         "nightlyRechargeResults",
                         from_date, to_date)
    conn = db()
    inserted = skipped = 0
    for r in rows:
        cur = conn.execute("""
            INSERT OR IGNORE INTO nightly_recharge (
                sleep_result_date, ans_status, recovery_indicator,
                recovery_indicator_sub_level, ans_rate,
                mean_nightly_recovery_rri, mean_nightly_recovery_rmssd,
                mean_baseline_rri, sd_baseline_rri,
                mean_baseline_rmssd, sd_baseline_rmssd,
                mean_baseline_respiration_interval,
                sd_baseline_respiration_interval
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["sleepResultDate"], r["ansStatus"], r["recoveryIndicator"],
            r["recoveryIndicatorSubLevel"], r["ansRate"],
            r["meanNightlyRecoveryRri"], r["meanNightlyRecoveryRmssd"],
            r["meanBaselineRri"], r["sdBaselineRri"],
            r["meanBaselineRmssd"], r["sdBaselineRmssd"],
            r["meanBaselineRespirationInterval"],
            r["sdBaselineRespirationInterval"]
        ))
        if cur.rowcount == 1:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    conn.close()
    print(f"  Done: {inserted} inserted, {skipped} already existed.")

# --- Sleep ---

def fetch_sleep(client, from_date, to_date):
    print("\nFetching sleep...")
    rows = fetch_chunked(client, "data/sleeps", "sleeps", from_date, to_date)
    if not rows:
        print("  No sleep data returned.")
        return
    print(f"  Received {len(rows)} nights. First record sample:")
    print(json.dumps(rows[0], indent=2))
    # INSERT code will be added once we see the actual field names above

# --- Training sessions ---

def fetch_training_sessions(client, from_date, to_date):
    print("\nFetching training sessions...")
    results = []
    for chunk_from, chunk_to in date_chunks(from_date, to_date):
        from_dt = f"{chunk_from}T00:00:00"
        to_dt   = f"{chunk_to}T23:59:59"
        print(f"  Fetching {chunk_from} to {chunk_to}...")
        data = client.get("data/training-sessions/list",
                          params={"from": from_dt, "to": to_dt})
        if data:
            results.extend(data.get("trainingSessions", []))

    if not results:
        print("  No training sessions returned.")
        return

    conn = db()
    sess_ins = sess_skip = ex_ins = ex_skip = 0

    for r in results:
        # --- training_sessions: session-level wrapper ---
        cur = conn.execute("""
            INSERT OR IGNORE INTO training_sessions (
                session_id, start_time, stop_time, date,
                device_id, hr_avg, hr_max,
                training_benefit, recovery_time_sec
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["identifier"]["id"],
            r["startTime"],
            r.get("stopTime"),
            r["startTime"][:10],
            r.get("deviceId"),
            r.get("hrAvg"),
            r.get("hrMax"),
            r.get("trainingBenefit"),
            int(r["recoveryTimeMillis"]) // 1000
        ))
        if cur.rowcount == 1: sess_ins += 1
        else: sess_skip += 1

        # --- exercises: one or more per session ---
        for e in r.get("exercises", []):
            cur = conn.execute("""
                INSERT OR IGNORE INTO exercises (
                    exercise_id, session_id,
                    start_time, stop_time, date,
                    sport_id, duration_sec,
                    calories, distance_m,
                    ascent_m, descent_m
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(e["identifier"]["id"]),
                r["identifier"]["id"],
                e["startTime"],
                e.get("stopTime"),
                e["startTime"][:10],
                e["sport"]["id"],
                e["durationMillis"] // 1000,
                e.get("calories"),
                e.get("distanceMeters"),
                e.get("ascentMeters"),
                e.get("descentMeters")
            ))
            if cur.rowcount == 1: ex_ins += 1
            else: ex_skip += 1

    conn.commit()
    conn.close()
    print(f"  Sessions: {sess_ins} inserted, {sess_skip} existed.")
    print(f"  Exercises: {ex_ins} inserted, {ex_skip} existed.")

# --- Tests (orthostatic + fitness/VO2max) ---

def fetch_tests(client, from_date, to_date):
    print("\nFetching tests...")
    # endpoint needs datetime format like training sessions
    results = []
    for chunk_from, chunk_to in date_chunks(from_date, to_date):
        print(f"  Fetching {chunk_from} to {chunk_to}...")
        data = client.get("data/tests/list",
                          params={"from": chunk_from, "to": chunk_to})
        if data:
            results.extend(data.get("tests", []))

    orthostatic = [r for r in results if "orthostaticTestResult" in r]
    fitness     = [r for r in results if "fitnessTestResult" in r]
    print(f"  Received {len(orthostatic)} orthostatic, {len(fitness)} fitness tests.")

    conn = db()
    orth_ins = orth_skip = phys_ins = phys_skip = 0

    for r in orthostatic:
        o = r["orthostaticTestResult"]
        cur = conn.execute("""
            INSERT OR IGNORE INTO orthostatic_tests (
                start_time, date,
                rr_avg_supine, rr_min_standup, rr_avg_stand,
                rr_lt_avg_supine, rr_lt_avg_stand,
                rmssd_supine, rmssd_stand,
                rmssd_lt_avg_supine, rmssd_lt_avg_stand
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["startTime"],
            r["startTime"][:10],
            o["rrAvgSupine"], o["rrMinStandup"], o["rrAvgStand"],
            o["rrLtAvgSupine"], o["rrLtAvgStand"],
            o["rmssdSupine"], o["rmssdStand"],
            o["rmssdLtAvgSupine"], o["rmssdLtAvgStand"]
        ))
        if cur.rowcount == 1: orth_ins += 1
        else: orth_skip += 1

    for r in fitness:
        f = r["fitnessTestResult"]
        p = f.get("physicalInformation", {})
        cur = conn.execute("""
            INSERT OR IGNORE INTO daily_physical (
                date, weight_kg, resting_hr, vo2max
            ) VALUES (?, ?, ?, ?)
        """, (
            r["startTime"][:10],
            p.get("weight"),
            p.get("restingHeartRate"),
            p.get("vo2Max")
        ))
        if cur.rowcount == 1: phys_ins += 1
        else: phys_skip += 1

    conn.commit()
    conn.close()
    print(f"  Orthostatic: {orth_ins} inserted, {orth_skip} existed.")
    print(f"  Daily physical: {phys_ins} inserted, {phys_skip} existed.")

# --- Main ---

def run():
    from_date = (date.today() - timedelta(days=90)).isoformat()
    to_date = date.today().isoformat()
    print(f"Fetching all data from {from_date} to {to_date}")

    client = PolarClient()

    fetch_nightly_recharge(client, from_date, to_date)
    fetch_sleep(client, from_date, to_date)
    fetch_training_sessions(client, from_date, to_date)
    fetch_tests(client, from_date, to_date)

    print("\nFetch complete.")

if __name__ == "__main__":
    run()