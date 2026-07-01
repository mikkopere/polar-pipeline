import math
import sqlite3
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import date, timedelta
from pathlib import Path

DB_FILE = Path(__file__).parent / "polar.db"

# --- Page config ---
st.set_page_config(
    page_title="Polar Dashboard",
    page_icon="🏋️",
    layout="wide"
)

# --- Colors & naming ---
COL_CTL    = "#1565C0"   # fitness
COL_ATL    = "#C62828"   # fatigue
COL_TSB    = "#2E7D32"   # form
COL_TRIMP  = "#90CAF9"
COL_RMSSD  = "#00796B"
COL_ORTHO  = "#F57F17"

SPORT_NAMES = {
    "15":  "Strength",
    "18":  "Indoor cycling",
    "38":  "Road cycling",
    "83":  "Auto-detected",
    "177": "E-bike",
}
SPORT_COLORS = {
    "Strength":        "#7B1FA2",
    "Indoor cycling":  "#1565C0",
    "Road cycling":    "#2E7D32",
    "E-bike":          "#00838F",
    "Auto-detected":   "#9E9E9E",
}
HR_ZONE_COLORS = {1: "#90A4AE", 2: "#42A5F5", 3: "#66BB6A", 4: "#FFA726", 5: "#EF5350"}
RECOVERY_COLORS = {
    1: "#D32F2F", 2: "#F57C00", 3: "#FBC02D",
    4: "#9CCC65", 5: "#66BB6A", 6: "#2E7D32",
}

def sport_name(code):
    return SPORT_NAMES.get(str(code), f"Sport {code}")

def pretty_benefit(tb):
    if not tb or not isinstance(tb, str):
        return ""
    s = tb.replace("TRAINING_BENEFIT_", "")
    plus = s.endswith("_PLUS")
    if plus:
        s = s[:-len("_PLUS")]
    return s.replace("_", " ").capitalize() + (" +" if plus else "")

def fmt_duration(seconds):
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"

# --- Sleep vector analysis ---

SLEEP_GAP_MS = 45 * 60 * 1000  # sleep blocks closer than this belong to the same night

def night_analysis(events):
    """Turn one night's state changes (single device) into segments and
    main-sleep-period stats. Returns None if the record contains no sleep."""
    offs = events["start_offset_ms"].tolist()
    states = events["state"].tolist()
    segs = [(offs[i], offs[i + 1], states[i]) for i in range(len(offs) - 1)]
    sleep = [(s, e) for s, e, st in segs if st == "SLEEP" and e > s]
    if not sleep:
        return None
    clusters, cur = [], [sleep[0]]
    for s, e in sleep[1:]:
        if s - cur[-1][1] <= SLEEP_GAP_MS:
            cur.append((s, e))
        else:
            clusters.append(cur)
            cur = [(s, e)]
    clusters.append(cur)
    main = max(clusters, key=lambda cl: sum(e - s for s, e in cl))
    start, end = main[0][0], main[-1][1]
    asleep = sum(e - s for s, e in main)
    return {
        "segments": segs,
        "start_ms": start,
        "end_ms": end,
        "asleep_ms": asleep,
        "wake_ms": (end - start) - asleep,
        "awakenings": len(main) - 1,
        "longest_ms": max(e - s for s, e in main),
        "efficiency": asleep / (end - start) if end > start else 0.0,
    }

def best_night_record(df_night):
    """Several devices can record the same night; pick the one whose main
    sleep period looks most like a real night (closest to 7.5 h asleep)."""
    best = None
    for device, ev in df_night.groupby("device_uuid"):
        a = night_analysis(ev)
        if a is None:
            continue
        score = abs(a["asleep_ms"] / 3.6e6 - 7.5)
        if best is None or score < best[2]:
            best = (device, a, score)
    return best

# --- Data loading ---

@st.cache_data(ttl=300)
def load_training_load(days):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, daily_load, atl, ctl, tsb
        FROM daily_training_load
        WHERE date >= ?
        ORDER BY date
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df

@st.cache_data(ttl=300)
def load_nightly_recharge(days):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT sleep_result_date AS date,
               recovery_indicator, ans_status,
               mean_nightly_recovery_rmssd AS rmssd,
               recovery_indicator_sub_level AS sub_level
        FROM nightly_recharge
        WHERE sleep_result_date >= ?
          AND recovery_indicator > 0
        ORDER BY sleep_result_date
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df

@st.cache_data(ttl=300)
def load_sessions(days):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT e.exercise_id, e.date, e.sport_id, e.duration_sec,
               s.hr_avg, s.hr_max, e.calories, e.cardio_load,
               e.distance_m / 1000.0 AS distance_km,
               s.training_benefit, s.recovery_time_sec,
               EXISTS (SELECT 1 FROM exercise_samples es
                       WHERE es.exercise_id = e.exercise_id) AS has_samples
        FROM exercises e
        JOIN training_sessions s ON s.session_id = e.session_id
        WHERE e.duration_sec > 300
          AND e.date >= ?
        ORDER BY e.date DESC
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    df["Sport"] = df["sport_id"].apply(sport_name)
    return df

@st.cache_data(ttl=300)
def load_exercise_samples(exercise_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT t_offset_sec, hr, speed, altitude, cadence
        FROM exercise_samples
        WHERE exercise_id = ?
        ORDER BY t_offset_sec
    """, conn, params=[exercise_id])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_exercise_route(exercise_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT t_offset_sec, lat, lon
        FROM exercise_route
        WHERE exercise_id = ?
        ORDER BY t_offset_sec
    """, conn, params=[exercise_id])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_hr_zones(exercise_id):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT zone_number, seconds_in_zone, lower_limit, upper_limit
        FROM hr_zones
        WHERE exercise_id = ?
        ORDER BY zone_number
    """, conn, params=[exercise_id])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_daily_hr(days):
    """Per-day summary of 24/7 heart rate."""
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, MIN(hr) AS min_hr, ROUND(AVG(hr), 0) AS avg_hr,
               MAX(hr) AS max_hr, COUNT(*) AS n_samples,
               COUNT(DISTINCT offset_ms / 60000) AS minutes_covered
        FROM continuous_hr
        WHERE date >= ?
        GROUP BY date
        ORDER BY date
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df

@st.cache_data(ttl=300)
def load_day_hr(day):
    """All 24/7 HR samples for one date, best-covered device only."""
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT offset_ms, hr FROM continuous_hr
        WHERE date = ?
          AND device_uuid = (SELECT device_uuid FROM continuous_hr
                             WHERE date = ? GROUP BY device_uuid
                             ORDER BY COUNT(*) DESC LIMIT 1)
        ORDER BY offset_ms
    """, conn, params=[day, day])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_hrv_windows(days):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, device_uuid, window_start_ms, rmssd, mean_hr, n_samples
        FROM hrv_windows
        WHERE date >= ?
        ORDER BY date, window_start_ms
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_sleep_events(days):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, device_uuid, start_offset_ms, state
        FROM sleep_wake_events
        WHERE date >= ?
        ORDER BY date, device_uuid, start_offset_ms
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_orthostatic(days):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, rmssd_supine, rmssd_stand,
               ROUND(60000.0 / rr_avg_supine, 0) AS hr_supine,
               ROUND(60000.0 / rr_avg_stand, 0) AS hr_stand,
               ROUND(60000.0 / rr_avg_stand - 60000.0 / rr_avg_supine, 0) AS hr_rise
        FROM orthostatic_tests
        WHERE date >= ?
        ORDER BY date
    """, conn, params=[(date.today() - timedelta(days=days)).isoformat()])
    conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df

@st.cache_data(ttl=300)
def load_physical():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, weight_kg, resting_hr, vo2max
        FROM daily_physical
        ORDER BY date
    """, conn)
    conn.close()
    return df

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Controls")
    days = st.radio("Time range", [30, 60, 90], index=2,
                    format_func=lambda d: f"Last {d} days", horizontal=True)
    if st.button("🔄 Refresh data", width="stretch"):
        st.cache_data.clear()
        st.rerun()

df_load     = load_training_load(days)
df_recovery = load_nightly_recharge(days)
df_sessions = load_sessions(days)
df_ortho    = load_orthostatic(days)
df_physical = load_physical()
df_sleep_ev = load_sleep_events(days)
df_daily_hr = load_daily_hr(days)
df_hrv_win  = load_hrv_windows(days)

# daily median of 5-min PPI RMSSD windows
df_ppi_daily = pd.DataFrame()
if not df_hrv_win.empty:
    df_ppi_daily = (df_hrv_win.groupby("date")["rmssd"].median()
                    .reset_index().rename(columns={"rmssd": "rmssd_median"}))
    df_ppi_daily["date"] = pd.to_datetime(df_ppi_daily["date"])

with st.sidebar:
    st.divider()
    st.subheader("Physical")
    if not df_physical.empty:
        latest_phys = df_physical.iloc[-1]
        st.metric("Weight", f"{latest_phys['weight_kg']:.0f} kg")
        st.metric("Resting HR", f"{int(latest_phys['resting_hr'])} bpm")
        st.caption(f"Last fitness test: {latest_phys['date']}")
    st.divider()
    st.subheader("Data freshness")
    if not df_load.empty:
        st.caption(f"Training load through **{df_load['date'].max():%d %b}**")
    if not df_recovery.empty:
        st.caption(f"Nightly recharge through **{df_recovery['date'].max():%d %b}**")
    if not df_sleep_ev.empty:
        st.caption(f"Sleep vectors through **{pd.to_datetime(df_sleep_ev['date'].max()):%d %b}**")
    if not df_daily_hr.empty:
        st.caption(f"24/7 HR & PPI through **{df_daily_hr['date'].max():%d %b}**")
    if not df_sessions.empty:
        st.caption(f"Last session **{pd.to_datetime(df_sessions['date'].max()):%d %b}**")

# --- Current status rows ---
# Latest recharge within the last 3 days (nights can be missing)
rec_row = None
if not df_recovery.empty:
    recent = df_recovery[df_recovery["date"].dt.date >= date.today() - timedelta(days=3)]
    if not recent.empty:
        rec_row = recent.iloc[-1]

load_row = df_load.iloc[-1] if not df_load.empty else None
prev_load_row = df_load.iloc[-2] if len(df_load) >= 2 else None
week_ago = df_load[df_load["date"].dt.date <= date.today() - timedelta(days=7)]
week_ago_row = week_ago.iloc[-1] if not week_ago.empty else None

rmssd_avg30 = None
if not df_recovery.empty:
    last30 = df_recovery[df_recovery["date"].dt.date >= date.today() - timedelta(days=30)]
    if not last30.empty:
        rmssd_avg30 = float(last30["rmssd"].mean())

# --- Header ---
st.title("🏋️ Polar Training Dashboard")
st.caption(f"{date.today():%A, %B %d %Y}")

# --- Readiness banner ---
if load_row is not None:
    tsb = float(load_row["tsb"])
    rec = int(rec_row["recovery_indicator"]) if rec_row is not None else None
    rmssd_now = float(rec_row["rmssd"]) if rec_row is not None else None

    signals = [f"TSB {tsb:+.0f}"]
    if rec is not None:
        signals.append(f"recovery {rec}/6")
    if rmssd_now is not None and rmssd_avg30:
        signals.append(f"RMSSD {rmssd_now:.0f} ms vs {rmssd_avg30:.0f} ms 30-day avg")
    detail = " · ".join(signals)

    low_hrv = rmssd_now is not None and rmssd_avg30 and rmssd_now < 0.85 * rmssd_avg30
    if tsb < -15 or (rec is not None and rec <= 2) or low_hrv:
        st.warning(f"**Take it easy** — fatigue or recovery signals are flagging. {detail}")
    elif tsb > 5 and (rec is None or rec >= 4):
        st.success(f"**Ready to train hard** — you're fresh and recovered. {detail}")
    else:
        st.info(f"**Normal training state** — moderate load is fine. {detail}")

# --- Status metrics ---
m1, m2, m3, m4, m5, m6 = st.columns(6)

if load_row is not None:
    tsb_delta = float(load_row["tsb"] - prev_load_row["tsb"]) if prev_load_row is not None else None
    ctl_delta = float(load_row["ctl"] - week_ago_row["ctl"]) if week_ago_row is not None else None
    atl_delta = float(load_row["atl"] - week_ago_row["atl"]) if week_ago_row is not None else None
    m1.metric("TSB · form", f"{load_row['tsb']:.1f}",
              delta=f"{tsb_delta:+.1f} vs yesterday" if tsb_delta is not None else None,
              help="Training stress balance: positive = fresh, below −15 = heavily fatigued")
    m2.metric("CTL · fitness", f"{load_row['ctl']:.1f}",
              delta=f"{ctl_delta:+.1f} vs last week" if ctl_delta is not None else None,
              help="Chronic training load — 42-day weighted average of daily TRIMP")
    m3.metric("ATL · fatigue", f"{load_row['atl']:.1f}",
              delta=f"{atl_delta:+.1f} vs last week" if atl_delta is not None else None,
              delta_color="inverse",
              help="Acute training load — 7-day weighted average of daily TRIMP")

if rec_row is not None:
    prev_rec = df_recovery.iloc[-2] if len(df_recovery) >= 2 else None
    rec_delta = int(rec_row["recovery_indicator"] - prev_rec["recovery_indicator"]) if prev_rec is not None else None
    m4.metric("Recovery", f"{int(rec_row['recovery_indicator'])}/6",
              delta=f"{rec_delta:+d} vs previous night" if rec_delta else None,
              help=f"Polar nightly recharge recovery indicator ({rec_row['date']:%d %b})")
    rmssd_delta = f"{rec_row['rmssd'] - rmssd_avg30:+.0f} ms vs 30-day avg" if rmssd_avg30 else None
    m5.metric("Nocturnal RMSSD", f"{int(rec_row['rmssd'])} ms",
              delta=rmssd_delta,
              help="HRV during sleep — higher than your average is a good sign")

if not df_physical.empty:
    vo2 = df_physical.dropna(subset=["vo2max"])
    if not vo2.empty:
        vo2_delta = float(vo2.iloc[-1]["vo2max"] - vo2.iloc[-2]["vo2max"]) if len(vo2) >= 2 else None
        m6.metric("VO₂max", f"{vo2.iloc[-1]['vo2max']:.0f}",
                  delta=f"{vo2_delta:+.0f} vs previous test" if vo2_delta else None,
                  help="Estimated from Polar fitness test")

st.divider()

# --- Tabs ---
tab_load, tab_recovery, tab_247, tab_sleep, tab_sessions = st.tabs(
    ["📈 Training load", "💤 Recovery & HRV", "🫀 24/7 HR & HRV", "😴 Sleep", "🏃 Sessions"]
)

# =====================================================================
# TAB 1 — Training load
# =====================================================================
with tab_load:
    st.subheader("Fitness, fatigue & form")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df_load["date"], y=df_load["daily_load"],
        name="Daily TRIMP", marker_color=COL_TRIMP, opacity=0.55,
        yaxis="y2",
        hovertemplate="TRIMP %{y:.0f}<extra></extra>"
    ))
    fig.add_trace(go.Scatter(
        x=df_load["date"], y=df_load["ctl"],
        name="CTL — fitness", line=dict(color=COL_CTL, width=2.5),
        hovertemplate="CTL %{y:.1f}<extra></extra>"
    ))
    fig.add_trace(go.Scatter(
        x=df_load["date"], y=df_load["atl"],
        name="ATL — fatigue", line=dict(color=COL_ATL, width=2.5),
        hovertemplate="ATL %{y:.1f}<extra></extra>"
    ))
    fig.add_trace(go.Scatter(
        x=df_load["date"], y=df_load["tsb"],
        name="TSB — form",
        line=dict(color=COL_TSB, width=1.5),
        fill="tozeroy", fillcolor="rgba(46,125,50,0.15)",
        hovertemplate="TSB %{y:.1f}<extra></extra>"
    ))
    # Form zones (apply to the TSB scale)
    tsb_floor = min(float(df_load["tsb"].min()) - 5, -20) if not df_load.empty else -30
    fig.add_hrect(y0=tsb_floor, y1=-15, fillcolor="rgba(198,40,40,0.06)", line_width=0,
                  annotation_text="overload", annotation_position="bottom left",
                  annotation_font=dict(size=11, color="#C62828"))
    fig.add_hrect(y0=5, y1=15, fillcolor="rgba(46,125,50,0.06)", line_width=0,
                  annotation_text="fresh", annotation_position="top left",
                  annotation_font=dict(size=11, color="#2E7D32"))
    fig.add_hline(y=0, line_dash="dot", line_color="grey", line_width=1)
    fig.update_layout(
        height=430,
        legend=dict(orientation="h", y=1.08),
        yaxis=dict(title="Load / Form"),
        yaxis2=dict(title="Daily TRIMP", overlaying="y", side="right", showgrid=False),
        hovermode="x unified",
        margin=dict(t=20, b=20)
    )
    st.plotly_chart(fig, width="stretch")

    col_vol, col_mix = st.columns([2, 1])

    with col_vol:
        st.subheader("Weekly training volume")
        if not df_sessions.empty:
            dfw = df_sessions.copy()
            dfw["week"] = pd.to_datetime(dfw["date"]).dt.to_period("W").apply(lambda p: p.start_time)
            weekly = (dfw.groupby(["week", "Sport"])["duration_sec"].sum() / 3600).reset_index()

            dfl = df_load.copy()
            dfl["week"] = dfl["date"].dt.to_period("W").apply(lambda p: p.start_time)
            weekly_trimp = dfl.groupby("week")["daily_load"].sum().reset_index()

            fig_w = go.Figure()
            for sport in weekly["Sport"].unique():
                sub = weekly[weekly["Sport"] == sport]
                fig_w.add_trace(go.Bar(
                    x=sub["week"], y=sub["duration_sec"],
                    name=sport, marker_color=SPORT_COLORS.get(sport),
                    hovertemplate=f"{sport}: %{{y:.1f}} h<extra></extra>"
                ))
            fig_w.add_trace(go.Scatter(
                x=weekly_trimp["week"], y=weekly_trimp["daily_load"],
                name="Weekly TRIMP", yaxis="y2",
                line=dict(color="#455A64", width=2, dash="dot"),
                mode="lines+markers", marker=dict(size=6),
                hovertemplate="TRIMP %{y:.0f}<extra></extra>"
            ))
            fig_w.update_layout(
                barmode="stack", height=340,
                yaxis=dict(title="Hours"),
                yaxis2=dict(title="TRIMP", overlaying="y", side="right", showgrid=False),
                legend=dict(orientation="h", y=1.12),
                hovermode="x unified",
                margin=dict(t=10, b=10)
            )
            st.plotly_chart(fig_w, width="stretch")
        else:
            st.info("No sessions in the selected range.")

    with col_mix:
        st.subheader("Sport mix")
        if not df_sessions.empty:
            mix = df_sessions.groupby("Sport")["duration_sec"].sum() / 3600
            fig_pie = go.Figure(go.Pie(
                labels=mix.index, values=mix.values, hole=0.55,
                marker=dict(colors=[SPORT_COLORS.get(s, "#9E9E9E") for s in mix.index]),
                textinfo="label+percent",
                hovertemplate="%{label}: %{value:.1f} h<extra></extra>"
            ))
            fig_pie.update_layout(
                height=340, showlegend=False,
                margin=dict(t=10, b=10, l=10, r=10),
                annotations=[dict(text=f"{mix.sum():.0f} h", showarrow=False,
                                  font=dict(size=22))]
            )
            st.plotly_chart(fig_pie, width="stretch")

# =====================================================================
# TAB 2 — Recovery & HRV
# =====================================================================
with tab_recovery:

    def hrv_gauge(series, current, label):
        """Semicircular gauge showing current HRV value within personal min/max range."""
        mn  = float(series.min())
        mx  = float(series.max())
        avg = float(series.mean())
        third = (mx - mn) / 3

        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=float(current),
            title={"text": label, "font": {"size": 13}},
            number={"suffix": " ms", "font": {"size": 28}, "valueformat": ".0f"},
            gauge={
                "axis": {
                    "range": [mn, mx],
                    "tickvals": [mn, avg, mx],
                    "ticktext": [f"{mn:.0f}", f"avg {avg:.0f}", f"{mx:.0f}"],
                    "tickwidth": 1,
                    "tickcolor": "darkgrey"
                },
                "bar": {"color": COL_CTL, "thickness": 0.25},
                "bgcolor": "rgba(0,0,0,0)",
                "borderwidth": 1,
                "bordercolor": "lightgrey",
                "steps": [
                    {"range": [mn,           mn + third],     "color": "#FFCDD2"},
                    {"range": [mn + third,   mn + 2 * third], "color": "#FFF9C4"},
                    {"range": [mn + 2*third, mx],             "color": "#C8E6C9"},
                ],
                "threshold": {
                    "line": {"color": "#555", "width": 2},
                    "thickness": 0.75,
                    "value": avg
                }
            }
        ))
        fig.update_layout(
            height=230,
            margin=dict(t=30, b=10, l=30, r=30),
            paper_bgcolor="rgba(0,0,0,0)"
        )
        return fig

    hrv_col1, hrv_col2 = st.columns(2)

    with hrv_col1:
        rmssd_series = df_recovery["rmssd"].dropna()
        if rec_row is not None and not rmssd_series.empty:
            st.caption(f"Nocturnal RMSSD — HRV during sleep ({days}-day personal range)")
            st.plotly_chart(
                hrv_gauge(rmssd_series, rec_row["rmssd"], "Nocturnal RMSSD (during sleep)"),
                width="stretch"
            )

    with hrv_col2:
        ortho_series = df_ortho["rmssd_supine"].dropna()
        latest_ortho = df_ortho.iloc[-1] if not df_ortho.empty else None
        if latest_ortho is not None and not ortho_series.empty:
            st.caption(f"Morning RMSSD supine — orthostatic test ({days}-day personal range)")
            st.plotly_chart(
                hrv_gauge(ortho_series, latest_ortho["rmssd_supine"],
                          "Morning RMSSD (supine, orthostatic test)"),
                width="stretch"
            )

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Nightly recharge")
        st.caption("Recovery indicator (colored by level) & ANS status")
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=df_recovery["date"], y=df_recovery["recovery_indicator"],
            name="Recovery (1–6)",
            marker_color=[RECOVERY_COLORS.get(int(v), "#9E9E9E")
                          for v in df_recovery["recovery_indicator"]],
            hovertemplate="Recovery %{y}/6<extra></extra>"
        ))
        fig2.add_trace(go.Scatter(
            x=df_recovery["date"], y=df_recovery["ans_status"],
            name="ANS status", line=dict(color="#7B1FA2", width=1.5),
            yaxis="y2",
            hovertemplate="ANS %{y:.1f}<extra></extra>"
        ))
        fig2.add_hline(y=0, line_dash="dash", line_color="grey", line_width=0.8, yref="y2")
        fig2.update_layout(
            height=320,
            yaxis=dict(title="Recovery indicator", range=[0, 7]),
            yaxis2=dict(title="ANS status", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", y=1.12),
            hovermode="x unified",
            margin=dict(t=10, b=10)
        )
        st.plotly_chart(fig2, width="stretch")

    with col_right:
        st.subheader("HRV trend")
        st.caption("Nocturnal RMSSD with 7-day rolling average, plus morning supine RMSSD")
        df_hrv = df_recovery.set_index("date")["rmssd"]
        rolling = df_hrv.rolling("7D").mean()

        fig3 = go.Figure()
        fig3.add_trace(go.Scatter(
            x=df_recovery["date"], y=df_recovery["rmssd"],
            name="Nocturnal RMSSD",
            mode="lines+markers",
            line=dict(color=COL_RMSSD, width=1), marker=dict(size=4),
            opacity=0.55,
            hovertemplate="RMSSD %{y} ms<extra></extra>"
        ))
        fig3.add_trace(go.Scatter(
            x=rolling.index, y=rolling.values,
            name="7-day average",
            line=dict(color=COL_RMSSD, width=3),
            hovertemplate="7d avg %{y:.0f} ms<extra></extra>"
        ))
        if not df_ortho.empty:
            fig3.add_trace(go.Scatter(
                x=df_ortho["date"], y=df_ortho["rmssd_supine"],
                name="Morning RMSSD (supine)",
                mode="markers+lines",
                line=dict(color=COL_ORTHO, width=1.5, dash="dot"),
                marker=dict(size=6),
                hovertemplate="Supine %{y} ms<extra></extra>"
            ))
        if not df_ppi_daily.empty:
            fig3.add_trace(go.Scatter(
                x=df_ppi_daily["date"], y=df_ppi_daily["rmssd_median"],
                name="Daytime RMSSD (PPI)",
                line=dict(color="#5C6BC0", width=1.5, dash="dash"),
                hovertemplate="Daytime %{y:.0f} ms<extra></extra>"
            ))
        fig3.update_layout(
            height=320,
            yaxis=dict(title="RMSSD (ms)"),
            legend=dict(orientation="h", y=1.12),
            hovermode="x unified",
            margin=dict(t=10, b=10)
        )
        st.plotly_chart(fig3, width="stretch")

    st.subheader("Orthostatic test — heart rate response")
    st.caption("Supine and standing HR from morning tests; a growing rise can indicate accumulating fatigue")
    if not df_ortho.empty:
        fig4 = go.Figure()
        fig4.add_trace(go.Bar(
            x=df_ortho["date"], y=df_ortho["hr_rise"],
            name="HR rise on standing", marker_color="#FFB74D", opacity=0.6,
            yaxis="y2",
            hovertemplate="Rise +%{y:.0f} bpm<extra></extra>"
        ))
        fig4.add_trace(go.Scatter(
            x=df_ortho["date"], y=df_ortho["hr_supine"],
            name="HR supine", mode="lines+markers",
            line=dict(color=COL_CTL, width=2), marker=dict(size=5),
            hovertemplate="Supine %{y:.0f} bpm<extra></extra>"
        ))
        fig4.add_trace(go.Scatter(
            x=df_ortho["date"], y=df_ortho["hr_stand"],
            name="HR standing", mode="lines+markers",
            line=dict(color=COL_ATL, width=2), marker=dict(size=5),
            hovertemplate="Standing %{y:.0f} bpm<extra></extra>"
        ))
        fig4.update_layout(
            height=320,
            yaxis=dict(title="Heart rate (bpm)"),
            yaxis2=dict(title="Rise (bpm)", overlaying="y", side="right",
                        showgrid=False, rangemode="tozero"),
            legend=dict(orientation="h", y=1.12),
            hovermode="x unified",
            margin=dict(t=10, b=10)
        )
        st.plotly_chart(fig4, width="stretch")
    else:
        st.info("No orthostatic tests in the selected range.")

# =====================================================================
# TAB 3 — 24/7 HR & HRV
# =====================================================================
with tab_247:
    if df_daily_hr.empty and df_hrv_win.empty:
        st.info("No 24/7 heart rate or PPI data yet — run `python main.py` "
                "(requires the continuous_samples:read and ppi_data:read scopes).")
    else:
        # latest-day summary vs 30-day baseline
        if not df_daily_hr.empty:
            latest_day = df_daily_hr.iloc[-1]
            min30 = df_daily_hr[df_daily_hr["date"] >=
                                pd.Timestamp(date.today() - timedelta(days=30))]["min_hr"].mean()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Lowest HR", f"{int(latest_day['min_hr'])} bpm",
                      delta=f"{latest_day['min_hr'] - min30:+.0f} vs 30-day avg",
                      delta_color="inverse",
                      help=f"Lowest 24/7 heart rate on {latest_day['date']:%d %b} — a rising trend can signal fatigue or illness")
            k2.metric("Average HR", f"{int(latest_day['avg_hr'])} bpm",
                      help=f"All-day average on {latest_day['date']:%d %b}")
            if not df_ppi_daily.empty:
                latest_ppi = df_ppi_daily.iloc[-1]
                ppi30 = df_ppi_daily["rmssd_median"].mean()
                k3.metric("Daily RMSSD (PPI)", f"{latest_ppi['rmssd_median']:.0f} ms",
                          delta=f"{latest_ppi['rmssd_median'] - ppi30:+.0f} ms vs {days}-day avg",
                          help=f"Median of 5-minute HRV windows on {latest_ppi['date']:%d %b}")
            k4.metric("HR coverage", f"{latest_day['minutes_covered'] / 60:.0f} h",
                      help="Hours of the latest day with 24/7 heart rate recorded")

        # single-day view
        day_options = sorted(df_daily_hr["date"].dt.date, reverse=True) if not df_daily_hr.empty else []
        if day_options:
            sel_col, _ = st.columns([1, 3])
            sel_day = sel_col.selectbox("Day", day_options,
                                        format_func=lambda d: f"{pd.Timestamp(d):%a %d %b %Y}")
            day_hr = load_day_hr(sel_day.isoformat())
            day_win = df_hrv_win[df_hrv_win["date"] == sel_day.isoformat()]
            midnight = pd.Timestamp(sel_day)

            fig_day = go.Figure()
            if not day_hr.empty:
                fig_day.add_trace(go.Scatter(
                    x=midnight + pd.to_timedelta(day_hr["offset_ms"], unit="ms"),
                    y=day_hr["hr"],
                    name="Heart rate", mode="lines",
                    line=dict(color="#C62828", width=1),
                    hovertemplate="%{y} bpm<extra></extra>"
                ))
            if not day_win.empty:
                fig_day.add_trace(go.Scatter(
                    x=midnight + pd.to_timedelta(day_win["window_start_ms"], unit="ms"),
                    y=day_win["rmssd"],
                    name="RMSSD (5-min windows)", yaxis="y2",
                    mode="markers", marker=dict(color=COL_RMSSD, size=4, opacity=0.7),
                    hovertemplate="RMSSD %{y:.0f} ms<extra></extra>"
                ))
            fig_day.update_layout(
                height=340,
                yaxis=dict(title="Heart rate (bpm)"),
                yaxis2=dict(title="RMSSD (ms)", overlaying="y", side="right",
                            showgrid=False, rangemode="tozero"),
                legend=dict(orientation="h", y=1.1),
                hovermode="x unified",
                margin=dict(t=10, b=10),
            )
            st.plotly_chart(fig_day, width="stretch")

        # history
        col_hr_hist, col_ppi_hist = st.columns(2)

        with col_hr_hist:
            st.subheader("Daily heart rate range")
            if not df_daily_hr.empty:
                fig_hr_hist = go.Figure()
                fig_hr_hist.add_trace(go.Scatter(
                    x=df_daily_hr["date"], y=df_daily_hr["max_hr"],
                    name="Max", line=dict(width=0), showlegend=False,
                    hovertemplate="max %{y} bpm<extra></extra>"
                ))
                fig_hr_hist.add_trace(go.Scatter(
                    x=df_daily_hr["date"], y=df_daily_hr["min_hr"],
                    name="Daily range", fill="tonexty",
                    fillcolor="rgba(198,40,40,0.12)", line=dict(width=0),
                    hovertemplate="min %{y} bpm<extra></extra>"
                ))
                fig_hr_hist.add_trace(go.Scatter(
                    x=df_daily_hr["date"], y=df_daily_hr["avg_hr"],
                    name="Average", line=dict(color="#C62828", width=2),
                    hovertemplate="avg %{y} bpm<extra></extra>"
                ))
                fig_hr_hist.add_trace(go.Scatter(
                    x=df_daily_hr["date"], y=df_daily_hr["min_hr"],
                    name="Lowest", line=dict(color="#1565C0", width=2),
                    hovertemplate="min %{y} bpm<extra></extra>"
                ))
                fig_hr_hist.update_layout(
                    height=300,
                    yaxis=dict(title="bpm"),
                    legend=dict(orientation="h", y=1.15),
                    hovermode="x unified",
                    margin=dict(t=10, b=10),
                )
                st.plotly_chart(fig_hr_hist, width="stretch")

        with col_ppi_hist:
            st.subheader("Daily RMSSD from PPI")
            if not df_ppi_daily.empty:
                rolling_ppi = (df_ppi_daily.set_index("date")["rmssd_median"]
                               .rolling("7D").mean())
                fig_ppi_hist = go.Figure()
                fig_ppi_hist.add_trace(go.Scatter(
                    x=df_ppi_daily["date"], y=df_ppi_daily["rmssd_median"],
                    name="Daily median", mode="lines+markers",
                    line=dict(color=COL_RMSSD, width=1), marker=dict(size=4),
                    opacity=0.55,
                    hovertemplate="%{y:.0f} ms<extra></extra>"
                ))
                fig_ppi_hist.add_trace(go.Scatter(
                    x=rolling_ppi.index, y=rolling_ppi.values,
                    name="7-day average",
                    line=dict(color=COL_RMSSD, width=3),
                    hovertemplate="7d avg %{y:.0f} ms<extra></extra>"
                ))
                fig_ppi_hist.update_layout(
                    height=300,
                    yaxis=dict(title="RMSSD (ms)"),
                    legend=dict(orientation="h", y=1.15),
                    hovermode="x unified",
                    margin=dict(t=10, b=10),
                )
                st.plotly_chart(fig_ppi_hist, width="stretch")
            else:
                st.info("No PPI data in the selected range.")

# =====================================================================
# TAB 4 — Sleep
# =====================================================================
with tab_sleep:
    if df_sleep_ev.empty:
        st.info("No sleep-wake vector data yet — run `python main.py` after syncing your watch.")
    else:
        nights = sorted(df_sleep_ev["date"].unique(), reverse=True)
        sel_col, _ = st.columns([1, 3])
        night = sel_col.selectbox(
            "Night", nights,
            format_func=lambda d: f"{pd.to_datetime(d):%a %d %b %Y}"
        )
        record = best_night_record(df_sleep_ev[df_sleep_ev["date"] == night])

        if record is None:
            st.info("No sleep detected in this night's recording.")
        else:
            device, a, _ = record
            midnight = pd.to_datetime(night)
            ms = lambda v: pd.Timedelta(milliseconds=int(v))

            n1, n2, n3, n4, n5 = st.columns(5)
            n1.metric("Time asleep", fmt_duration(a["asleep_ms"] / 1000),
                      help="Sleep within the main sleep period")
            n2.metric("Sleep window",
                      f"{midnight + ms(a['start_ms']):%H:%M} – {midnight + ms(a['end_ms']):%H:%M}",
                      help="Start and end of the main sleep period")
            n3.metric("Awakenings", a["awakenings"],
                      help="Number of wake episodes inside the sleep window")
            n4.metric("Longest stretch", fmt_duration(a["longest_ms"] / 1000))
            n5.metric("Efficiency", f"{a['efficiency'] * 100:.0f} %",
                      help="Share of the sleep window actually spent asleep")

            # Hypnogram: sleep/wake states over the whole recording
            xs, ys = [], []
            for s, e, state in a["segments"]:
                lvl = 1 if state == "SLEEP" else 0
                xs += [midnight + ms(s), midnight + ms(e)]
                ys += [lvl, lvl]
            fig_hyp = go.Figure(go.Scatter(
                x=xs, y=ys, line_shape="hv",
                line=dict(color="#5C6BC0", width=1.5),
                fill="tozeroy", fillcolor="rgba(92,107,192,0.35)",
                hoverinfo="x+y", showlegend=False
            ))
            fig_hyp.add_vrect(
                x0=midnight + ms(a["start_ms"]), x1=midnight + ms(a["end_ms"]),
                fillcolor="rgba(92,107,192,0.08)", line_width=0,
                annotation_text="main sleep period", annotation_position="top left",
                annotation_font=dict(size=11, color="#5C6BC0")
            )
            fig_hyp.update_layout(
                height=260,
                yaxis=dict(tickvals=[0, 1], ticktext=["Awake", "Asleep"],
                           range=[-0.08, 1.25]),
                margin=dict(t=20, b=10),
            )
            st.plotly_chart(fig_hyp, width="stretch")
            st.caption(f"Raw sleep-wake vector from device …{device[-8:]} — "
                       "Polar's public API exposes sleep/wake states only, not sleep stages or score.")

        st.subheader("Sleep history")
        history = []
        for d in sorted(df_sleep_ev["date"].unique()):
            r = best_night_record(df_sleep_ev[df_sleep_ev["date"] == d])
            if r is not None:
                history.append({"date": pd.to_datetime(d),
                                "hours": r[1]["asleep_ms"] / 3.6e6})
        if history:
            dfh = pd.DataFrame(history)
            bar_colors = ["#EF5350" if h < 6 else "#FBC02D" if h < 7 else "#66BB6A"
                          for h in dfh["hours"]]
            fig_hist = go.Figure(go.Bar(
                x=dfh["date"], y=dfh["hours"],
                marker_color=bar_colors,
                hovertemplate="%{y:.1f} h<extra></extra>"
            ))
            fig_hist.add_hrect(y0=7, y1=9, fillcolor="rgba(102,187,106,0.08)",
                               line_width=0,
                               annotation_text="7–9 h target",
                               annotation_position="top left",
                               annotation_font=dict(size=11, color="#66BB6A"))
            fig_hist.update_layout(
                height=280,
                yaxis=dict(title="Time asleep (h)"),
                margin=dict(t=20, b=10),
            )
            st.plotly_chart(fig_hist, width="stretch")

# =====================================================================
# TAB 5 — Sessions
# =====================================================================
with tab_sessions:
    if df_sessions.empty:
        st.info("No sessions in the selected range.")
    else:
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Sessions", len(df_sessions))
        s2.metric("Total time", fmt_duration(df_sessions["duration_sec"].sum()))
        s3.metric("Distance", f"{df_sessions['distance_km'].sum():.0f} km")
        s4.metric("Calories", f"{df_sessions['calories'].sum():,.0f} kcal")

        df_display = pd.DataFrame({
            "Date":       pd.to_datetime(df_sessions["date"]),
            "Sport":      df_sessions["Sport"],
            "Duration":   df_sessions["duration_sec"].apply(fmt_duration),
            "Avg HR":     df_sessions["hr_avg"],
            "Max HR":     df_sessions["hr_max"],
            "Distance":   df_sessions["distance_km"].round(1),
            "Calories":   df_sessions["calories"],
            "Cardio load": df_sessions["cardio_load"].round(0),
            "Training benefit": df_sessions["training_benefit"].apply(pretty_benefit),
        })
        st.dataframe(
            df_display,
            width="stretch",
            hide_index=True,
            height=min(38 * (len(df_display) + 1), 600),
            column_config={
                "Date": st.column_config.DateColumn(format="ddd DD MMM"),
                "Avg HR": st.column_config.ProgressColumn(
                    format="%d bpm", min_value=60, max_value=180),
                "Max HR": st.column_config.NumberColumn(format="%d bpm"),
                "Distance": st.column_config.NumberColumn(format="%.1f km"),
                "Calories": st.column_config.NumberColumn(format="%d kcal"),
                "Cardio load": st.column_config.NumberColumn(
                    format="%d", help="Polar's own TRIMP-style load (recent sessions only)"),
            },
        )

        # --- Session detail: HR curve, zones, GPS route ---
        st.divider()
        st.subheader("Session detail")
        detailed = df_sessions[df_sessions["has_samples"] == 1]
        if detailed.empty:
            st.info("No per-second sample data yet — HR and GPS detail is only "
                    "available for sessions synced after API registration.")
        else:
            def session_label(i):
                r = detailed.loc[i]
                return (f"{pd.to_datetime(r['date']):%a %d %b} — {r['Sport']} "
                        f"({fmt_duration(r['duration_sec'])})")

            sel_col, _ = st.columns([1, 2])
            sel = sel_col.selectbox("Session", detailed.index.tolist(),
                                    format_func=session_label)
            row = detailed.loc[sel]
            samples = load_exercise_samples(row["exercise_id"])
            route   = load_exercise_route(row["exercise_id"])
            zones   = load_hr_zones(row["exercise_id"])
            samples["minutes"] = samples["t_offset_sec"] / 60

            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("Duration", fmt_duration(row["duration_sec"]))
            d2.metric("Avg HR", f"{int(row['hr_avg'])} bpm")
            d3.metric("Max HR", f"{int(row['hr_max'])} bpm")
            if pd.notna(row["cardio_load"]):
                d4.metric("Cardio load", f"{row['cardio_load']:.0f}",
                          help="Polar's own cardio load (TRIMP)")
            if pd.notna(row["distance_km"]):
                d5.metric("Distance", f"{row['distance_km']:.1f} km")

            col_hr, col_zones = st.columns([2, 1])

            with col_hr:
                st.caption("Heart rate — shaded bands are your Polar HR zones")
                fig_hr = go.Figure()
                for _, z in zones.iterrows():
                    fig_hr.add_hrect(
                        y0=z["lower_limit"], y1=z["upper_limit"],
                        fillcolor=HR_ZONE_COLORS.get(int(z["zone_number"]), "#9E9E9E"),
                        opacity=0.10, line_width=0,
                    )
                fig_hr.add_trace(go.Scatter(
                    x=samples["minutes"], y=samples["hr"],
                    name="HR", line=dict(color="#C62828", width=1.5),
                    hovertemplate="%{y:.0f} bpm at %{x:.0f} min<extra></extra>"
                ))
                fig_hr.update_layout(
                    height=320, showlegend=False,
                    xaxis=dict(title="Minutes"),
                    yaxis=dict(title="bpm"),
                    margin=dict(t=10, b=10),
                )
                st.plotly_chart(fig_hr, width="stretch")

            with col_zones:
                st.caption("Time in HR zones")
                if not zones.empty:
                    fig_z = go.Figure(go.Bar(
                        y=[f"Z{int(z)}" for z in zones["zone_number"]],
                        x=zones["seconds_in_zone"] / 60,
                        orientation="h",
                        marker_color=[HR_ZONE_COLORS.get(int(z), "#9E9E9E")
                                      for z in zones["zone_number"]],
                        text=[fmt_duration(s) for s in zones["seconds_in_zone"]],
                        textposition="auto",
                        hovertemplate="%{x:.0f} min<extra></extra>"
                    ))
                    fig_z.update_layout(
                        height=320,
                        xaxis=dict(title="Minutes"),
                        yaxis=dict(autorange="reversed"),
                        margin=dict(t=10, b=10),
                    )
                    st.plotly_chart(fig_z, width="stretch")
                else:
                    st.info("No zone data for this session.")

            col_map, col_speed = st.columns(2)

            with col_map:
                st.caption("Route — colored by heart rate")
                if not route.empty:
                    track = route.merge(samples[["t_offset_sec", "hr"]],
                                        on="t_offset_sec", how="left")
                    lat_c, lon_c = track["lat"].mean(), track["lon"].mean()
                    span = max(track["lat"].max() - track["lat"].min(),
                               (track["lon"].max() - track["lon"].min())
                               * math.cos(math.radians(lat_c)),
                               1e-4)
                    zoom = min(15.0, max(8.0, 8.7 - math.log2(span)))
                    fig_map = go.Figure(go.Scattermap(
                        lat=track["lat"], lon=track["lon"],
                        mode="markers",
                        marker=dict(size=5, color=track["hr"],
                                    colorscale="Turbo",
                                    colorbar=dict(title="bpm", thickness=12)),
                        customdata=track["t_offset_sec"] / 60,
                        hovertemplate="%{marker.color:.0f} bpm at %{customdata:.0f} min<extra></extra>"
                    ))
                    fig_map.update_layout(
                        map=dict(style="open-street-map",
                                 center=dict(lat=lat_c, lon=lon_c),
                                 zoom=zoom),
                        height=380,
                        margin=dict(t=10, b=10, l=10, r=10),
                    )
                    st.plotly_chart(fig_map, width="stretch")
                else:
                    st.info("No GPS route for this session.")

            with col_speed:
                st.caption("Speed & altitude")
                fig_sp = go.Figure()
                if samples["altitude"].notna().any():
                    # rolling median tames barometric altitude glitches
                    alt_smooth = samples["altitude"].rolling(31, center=True,
                                                             min_periods=1).median()
                    fig_sp.add_trace(go.Scatter(
                        x=samples["minutes"], y=alt_smooth,
                        name="Altitude", yaxis="y2",
                        line=dict(color="#8D6E63", width=1),
                        fill="tozeroy", fillcolor="rgba(141,110,99,0.15)",
                        hovertemplate="%{y:.0f} m<extra></extra>"
                    ))
                if samples["speed"].notna().any():
                    fig_sp.add_trace(go.Scatter(
                        x=samples["minutes"],
                        y=samples["speed"].rolling(10, min_periods=1).mean(),
                        name="Speed", line=dict(color="#1565C0", width=1.5),
                        hovertemplate="%{y:.1f} km/h<extra></extra>"
                    ))
                fig_sp.update_layout(
                    height=380,
                    xaxis=dict(title="Minutes"),
                    yaxis=dict(title="km/h"),
                    yaxis2=dict(title="Altitude (m)", overlaying="y",
                                side="right", showgrid=False),
                    legend=dict(orientation="h", y=1.1),
                    hovermode="x unified",
                    margin=dict(t=10, b=10),
                )
                st.plotly_chart(fig_sp, width="stretch")
