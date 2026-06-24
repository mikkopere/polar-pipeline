import sqlite3
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
from pathlib import Path

DB_FILE = Path(__file__).parent / "polar.db"

def load_data():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    load = conn.execute("""
        SELECT date, daily_load, atl, ctl, tsb
        FROM daily_training_load
        ORDER BY date
    """).fetchall()

    recovery = conn.execute("""
        SELECT sleep_result_date AS date, recovery_indicator, ans_status
        FROM nightly_recharge
        WHERE recovery_indicator > 0
        ORDER BY date
    """).fetchall()

    conn.close()
    return load, recovery

def parse_dates(rows, key="date"):
    return [datetime.fromisoformat(r[key]) for r in rows]

def main():
    load_rows, rec_rows = load_data()

    dates      = parse_dates(load_rows)
    daily_load = [r["daily_load"] for r in load_rows]
    atl        = [r["atl"]        for r in load_rows]
    ctl        = [r["ctl"]        for r in load_rows]
    tsb        = [r["tsb"]        for r in load_rows]

    rec_dates  = parse_dates(rec_rows)
    rec_ind    = [r["recovery_indicator"] for r in rec_rows]

    # --- figure with 3 panels ---
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(14, 9), sharex=True,
        gridspec_kw={"height_ratios": [1, 2, 1]}
    )
    fig.suptitle("Training Load & Recovery", fontsize=14, fontweight="bold")

    # Panel 1 — daily training load bars
    ax1.bar(dates, daily_load, color="#4a90d9", alpha=0.7, width=0.8, label="Daily TRIMP")
    ax1.set_ylabel("TRIMP")
    ax1.set_title("Daily Training Load", fontsize=10)
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", alpha=0.3)

    # Panel 2 — CTL and ATL lines
    ax2.plot(dates, ctl, color="#2196F3", linewidth=2.0, label="CTL — fitness (42d)")
    ax2.plot(dates, atl, color="#F44336", linewidth=2.0, label="ATL — fatigue (7d)")
    ax2.set_ylabel("Load")
    ax2.set_title("Fitness (CTL) vs Fatigue (ATL)", fontsize=10)
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)

    # Panel 3 — TSB as filled area
    ax3.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax3.fill_between(dates, tsb, 0,
                     where=[v >= 0 for v in tsb],
                     color="#4CAF50", alpha=0.6, label="Fresh (TSB ≥ 0)")
    ax3.fill_between(dates, tsb, 0,
                     where=[v < 0 for v in tsb],
                     color="#F44336", alpha=0.4, label="Fatigued (TSB < 0)")
    ax3.plot(dates, tsb, color="#333333", linewidth=1.2)

    # Overlay recovery indicator as dots
    ax3_twin = ax3.twinx()
    ax3_twin.scatter(rec_dates, rec_ind, color="#FF9800", s=25, zorder=5,
                     label="Recovery (1–6)", alpha=0.8)
    ax3_twin.set_ylabel("Recovery indicator", fontsize=8)
    ax3_twin.set_ylim(0, 8)

    ax3.set_ylabel("TSB (form)")
    ax3.set_title("Form (TSB) + Recovery Indicator", fontsize=10)

    # Combine legends from both axes in panel 3
    lines1, labels1 = ax3.get_legend_handles_labels()
    lines2, labels2 = ax3_twin.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, labels1 + labels2, fontsize=8, loc="lower left")
    ax3.grid(alpha=0.3)

    # X axis formatting
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax3.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
    plt.xticks(rotation=45, ha="right")

    plt.tight_layout()
    output = Path(__file__).parent / "training_load.png"
    plt.savefig(output, dpi=150, bbox_inches="tight")
    print(f"Saved to {output}")
    plt.show()

if __name__ == "__main__":
    main()