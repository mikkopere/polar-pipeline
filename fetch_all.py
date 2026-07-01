import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from polar_client import PolarClient

DB_FILE = Path(__file__).parent / "polar.db"
CHUNK = timedelta(days=28)
VECTOR_CHUNK = timedelta(days=6)  # sleep-wake-vectors endpoint allows max 7 days

# --- Helpers ---

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def date_chunks(from_date, to_date, chunk=CHUNK):
    """Split a date range into chunks (28 days by default)."""
    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    while start <= end:
        yield start.isoformat(), min(start + chunk, end).isoformat()
        start = start + chunk + timedelta(days=1)

def iso_duration_to_sec(s):
    """Parse an ISO 8601 duration like PT12M25S or PT3044.396S into seconds."""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", s or "")
    if not m:
        return None
    h, mi, sec = m.groups()
    return int(float(h or 0) * 3600 + float(mi or 0) * 60 + float(sec or 0))

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

# --- Sleep-wake vectors ---

def fetch_sleep_wake_vectors(client, from_date, to_date):
    """Raw sleep/wake state transitions per night and device."""
    print("\nFetching sleep-wake vectors...")
    vectors = []
    for chunk_from, chunk_to in date_chunks(from_date, to_date, chunk=VECTOR_CHUNK):
        print(f"  Fetching {chunk_from} to {chunk_to}...")
        data = client.get("data/sleep-wake-vectors",
                          params={"from": chunk_from, "to": chunk_to})
        if data:
            vectors.extend(data.get("sleepWakeVectors", []))

    conn = db()
    inserted = skipped = 0
    for v in vectors:
        device = v["deviceReference"]["uuid"]
        for change in v["sleepWakeStateChanges"]:
            # store the state as SLEEP / WAKE without the enum prefix
            state = change["newState"].rsplit("_", 1)[-1]
            cur = conn.execute("""
                INSERT OR IGNORE INTO sleep_wake_events (
                    date, device_uuid, start_offset_ms, state
                ) VALUES (?, ?, ?, ?)
            """, (v["date"], device, change["startOffset"], state))
            if cur.rowcount == 1:
                inserted += 1
            else:
                skipped += 1
    conn.commit()
    conn.close()
    print(f"  Done: {inserted} events inserted, {skipped} already existed.")

# --- 24/7 heart rate (v4 continuous samples) ---

def fetch_continuous_hr(client, from_date, to_date):
    """All-day heart rate at ~10 s resolution from continuous samples."""
    print("\nFetching 24/7 heart rate...")
    rows = []
    for chunk_from, chunk_to in date_chunks(from_date, to_date, chunk=VECTOR_CHUNK):
        print(f"  Fetching {chunk_from} to {chunk_to}...")
        data = client.get("data/continuous-samples",
                          params={"from": chunk_from, "to": chunk_to,
                                  "features": "heart-rate-samples"})
        for day in (data or {}).get("heartRateSamplesPerDay", []):
            device = day["deviceRef"]["deviceId"]
            for s in day.get("samples", []):
                # entries missing hr or offset mark recording gaps/triggers only
                if s.get("heartRate") is not None and s.get("offsetMillis") is not None:
                    rows.append((day["date"], device, s["offsetMillis"], s["heartRate"]))

    conn = db()
    cur = conn.executemany("""
        INSERT OR IGNORE INTO continuous_hr (date, device_uuid, offset_ms, hr)
        VALUES (?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    print(f"  Done: {cur.rowcount} inserted, {len(rows) - cur.rowcount} already existed.")

# --- Daytime HRV from PPI samples (v4) ---

PPI_WINDOW_MS = 5 * 60 * 1000
PPI_MAX_GAP_MS = 2500      # successive beats farther apart than this are not consecutive
PPI_MIN_DIFFS = 20         # minimum beat-to-beat diffs for a valid RMSSD window

def ppi_to_hrv_windows(samples):
    """Aggregate raw PPI samples into 5-minute (window_start_ms, rmssd,
    mean_hr, n_samples) windows using only good-quality beats."""
    good = [s for s in samples
            if s.get("skinContact") and not s.get("movement")
            and s.get("errorEstimateMillis", 999) <= 30]
    good.sort(key=lambda s: s["offsetMillis"])

    buckets = {}
    prev = None
    for s in good:
        w = s["offsetMillis"] // PPI_WINDOW_MS * PPI_WINDOW_MS
        b = buckets.setdefault(w, {"pp": [], "sqdiffs": []})
        b["pp"].append(s["ppInterval"])
        if prev is not None and s["offsetMillis"] - prev["offsetMillis"] <= PPI_MAX_GAP_MS:
            b["sqdiffs"].append((s["ppInterval"] - prev["ppInterval"]) ** 2)
        prev = s

    windows = []
    for w, b in sorted(buckets.items()):
        if len(b["sqdiffs"]) >= PPI_MIN_DIFFS:
            rmssd = (sum(b["sqdiffs"]) / len(b["sqdiffs"])) ** 0.5
            mean_hr = 60000.0 / (sum(b["pp"]) / len(b["pp"]))
            windows.append((w, round(rmssd, 1), round(mean_hr, 1), len(b["pp"])))
    return windows

def fetch_ppi_hrv(client, from_date, to_date):
    """PPI samples, aggregated to 5-minute HRV windows. The endpoint serves
    one day per request when features=samples, so days already in the
    database are skipped (except the most recent, which may still grow)."""
    print("\nFetching PPI samples (daytime HRV)...")
    conn = db()
    have = {r[0] for r in conn.execute("SELECT DISTINCT date FROM hrv_windows")}

    start = date.fromisoformat(from_date)
    end = date.fromisoformat(to_date)
    refetch_from = end - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if d.isoformat() not in have or d >= refetch_from]

    fetched = windows_total = 0
    for d in days:
        data = client.get("data/ppi-samples",
                          params={"from": d.isoformat(),
                                  "to": (d + timedelta(days=1)).isoformat(),
                                  "features": "samples"})
        for day in (data or {}).get("dailyPpiSamples", []):
            if day["date"] != d.isoformat():
                continue  # the 2-day request window can spill into the next day
            for dev in day.get("ppiSamplesPerDevice", []):
                device = dev["recordingDevice"]["uuid"]
                windows = ppi_to_hrv_windows(dev.get("ppiSamples", []))
                conn.execute("DELETE FROM hrv_windows WHERE date = ? AND device_uuid = ?",
                             (d.isoformat(), device))
                conn.executemany("""
                    INSERT INTO hrv_windows (date, device_uuid, window_start_ms,
                                             rmssd, mean_hr, n_samples)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, [(d.isoformat(), device, *w) for w in windows])
                windows_total += len(windows)
        fetched += 1
        if fetched % 10 == 0:
            print(f"  ...{fetched}/{len(days)} days")
        conn.commit()
    conn.close()
    print(f"  Done: {fetched} days fetched, {windows_total} HRV windows stored.")

# --- Exercise samples, route & HR zones (v3 exercises API) ---

SAMPLE_TYPE_COLUMNS = {
    0: "hr",        # bpm
    1: "speed",     # km/h
    2: "cadence",   # rpm
    3: "altitude",  # m
}

def _parse_sample_data(raw):
    out = []
    for x in raw.split(","):
        try:
            out.append(float(x))
        except ValueError:
            out.append(None)
    return out

def fetch_exercise_details(client):
    """Per-second HR/speed/cadence/altitude samples, GPS route, HR zones and
    Polar's cardio load.

    This is the only fetcher on the v3 API — per the official v4 docs,
    /v4/data/training-sessions/list is the only training-session endpoint,
    with no per-exercise samples, zones, GPS track or cardio load anywhere
    in v4 (v4 "routes" are user-saved navigation routes, not session tracks,
    and continuous samples are ~5-minute 24/7 HR, far too sparse). The v3
    exercises API covers sessions synced within 30 days after registration.
    """
    print("\nFetching exercise details (samples, route, HR zones)...")
    listing = client.get("exercises", base_url=PolarClient.V3_URL) or []

    conn = db()
    # v3 exercise ids differ from v4 ids — match on identical start_time
    by_start = {row[1]: row[0] for row in
                conn.execute("SELECT exercise_id, start_time FROM exercises")}

    detailed = load_updated = 0
    for ex in listing:
        ex_id = by_start.get(ex.get("start_time"))
        if ex_id is None:
            continue

        cardio_load = (ex.get("training_load_pro") or {}).get("cardio-load")
        if cardio_load:
            conn.execute("UPDATE exercises SET cardio_load = ? WHERE exercise_id = ?",
                         (cardio_load, ex_id))
            load_updated += 1

        already = conn.execute(
            "SELECT COUNT(*) FROM exercise_samples WHERE exercise_id = ?",
            (ex_id,)).fetchone()[0]
        if already:
            continue

        print(f"  Fetching detail for {ex.get('start_time')} ({ex.get('detailed_sport_info')})...")
        detail = client.get(f"exercises/{ex['id']}",
                            params={"samples": "true", "zones": "true", "route": "true"},
                            base_url=PolarClient.V3_URL)
        if not detail:
            continue

        # samples: one value per second, keyed by sample type
        series = {}
        for s in detail.get("samples", []):
            col = SAMPLE_TYPE_COLUMNS.get(s["sample_type"])
            if col:
                series[col] = _parse_sample_data(s["data"])
        if series:
            n = max(len(v) for v in series.values())
            get = lambda col, i: (series.get(col) or [None] * n)[i] if i < len(series.get(col) or []) else None
            conn.executemany("""
                INSERT INTO exercise_samples (exercise_id, t_offset_sec,
                                              hr, speed, altitude, cadence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [(ex_id, i, get("hr", i), get("speed", i),
                   get("altitude", i), get("cadence", i)) for i in range(n)])

        for z in detail.get("heart_rate_zones", []):
            conn.execute("""
                INSERT INTO hr_zones (exercise_id, zone_number, seconds_in_zone,
                                      lower_limit, upper_limit)
                VALUES (?, ?, ?, ?, ?)
            """, (ex_id, z["index"] + 1, iso_duration_to_sec(z["in_zone"]),
                  z.get("lower_limit"), z.get("upper_limit")))

        conn.executemany("""
            INSERT INTO exercise_route (exercise_id, t_offset_sec, lat, lon, altitude)
            VALUES (?, ?, ?, ?, NULL)
        """, [(ex_id, iso_duration_to_sec(p["time"]), p["latitude"], p["longitude"])
              for p in detail.get("route", [])])

        detailed += 1

    conn.commit()
    conn.close()
    print(f"  Done: {detailed} exercises detailed, cardio load updated on {load_updated}.")

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
    fetch_sleep_wake_vectors(client, from_date, to_date)
    fetch_continuous_hr(client, from_date, to_date)
    fetch_ppi_hrv(client, from_date, to_date)
    fetch_training_sessions(client, from_date, to_date)
    fetch_exercise_details(client)
    fetch_tests(client, from_date, to_date)

    print("\nFetch complete.")

if __name__ == "__main__":
    run()