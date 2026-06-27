import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB_FILE = Path(__file__).parent / "polar.db"

# --- Physical parameters (from fitness test data in daily_physical) ---
HR_REST = 55   # bpm (from Polar profile)
HR_MAX  = 171  # bpm (from fitness test)

# --- Time constants ---
# ATL: 7-day exponential weighted average  → "acute fatigue"
# CTL: 42-day exponential weighted average → "chronic fitness"
ATL_ALPHA = 1 - math.exp(-1 / 7)   # ≈ 0.1331
CTL_ALPHA = 1 - math.exp(-1 / 42)  # ≈ 0.0235

def trimp(duration_sec, hr_avg):
    """
    Banister TRIMP (male weighting).
    Returns 0 if either input is missing.
    """
    if not duration_sec or not hr_avg:
        return 0.0
    duration_min = duration_sec / 60
    hrr = (hr_avg - HR_REST) / (HR_MAX - HR_REST)
    hrr = max(0.0, min(1.0, hrr))   # clamp to valid range
    return duration_min * hrr * 0.64 * math.exp(1.92 * hrr)

def compute():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    # Step 1: compute daily TRIMP load from exercises
    # Filter out very short sessions (accidental button presses etc.)
    sessions = conn.execute("""
        SELECT e.date, e.duration_sec, s.hr_avg
        FROM exercises e
        JOIN training_sessions s ON s.session_id = e.session_id
        WHERE e.duration_sec > 300
        ORDER BY e.date
    """).fetchall()

    daily_load = {}
    for s in sessions:
        load = trimp(s["duration_sec"], s["hr_avg"])
        daily_load[s["date"]] = daily_load.get(s["date"], 0.0) + load

    # Step 2: build date range covering all our data
    first = conn.execute("""
        SELECT MIN(d) FROM (
            SELECT MIN(sleep_result_date) AS d FROM nightly_recharge
            UNION ALL
            SELECT MIN(date) AS d FROM exercises WHERE duration_sec > 300
        )
    """).fetchone()[0]

    start_date = date.fromisoformat(first)
    end_date   = date.today()

    # Step 3: iterate day by day and compute ATL, CTL, TSB
    # TSB is computed BEFORE updating ATL/CTL — represents "form going into today"
    atl, ctl = 0.0, 0.0
    rows = []

    current = start_date
    while current <= end_date:
        d = current.isoformat()
        L   = daily_load.get(d, 0.0)
        tsb = ctl - atl                        # yesterday's fitness minus fatigue
        atl = atl + (L - atl) * ATL_ALPHA      # update fatigue
        ctl = ctl + (L - ctl) * CTL_ALPHA      # update fitness
        rows.append((d, round(L, 2), round(atl, 2), round(ctl, 2), round(tsb, 2)))
        current += timedelta(days=1)

    # Step 4: store results (full recompute each run)
    conn.execute("DELETE FROM daily_training_load")
    conn.executemany("""
        INSERT INTO daily_training_load (date, daily_load, atl, ctl, tsb)
        VALUES (?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"Computed {len(rows)} days of ATL/CTL/TSB.")

    # Step 5: show the last 3 weeks
    print(f"\n{'Date':<12} {'Load':>6} {'ATL':>6} {'CTL':>6} {'TSB':>6}")
    print("-" * 44)
    recent = conn.execute("""
        SELECT date, daily_load, atl, ctl, tsb
        FROM daily_training_load
        ORDER BY date DESC
        LIMIT 21
    """).fetchall()
    for r in reversed(recent):
        marker = " ◀" if r["daily_load"] > 0 else ""
        print(f"{r['date']:<12} {r['daily_load']:>6.1f} "
              f"{r['atl']:>6.1f} {r['ctl']:>6.1f} {r['tsb']:>6.1f}{marker}")

    conn.close()

def run():
    compute()

if __name__ == "__main__":
    run()