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

# --- Data loading ---

@st.cache_data(ttl=300)  # cache for 5 minutes
def load_training_load(days=90):
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
def load_nightly_recharge(days=90):
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
def load_recent_sessions(n=10):
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("""
        SELECT date, sport_id, duration_sec / 60 AS duration_min,
               hr_avg, hr_max, calories,
               ROUND(distance_m / 1000.0, 1) AS distance_km,
               training_benefit
        FROM training_sessions
        WHERE duration_sec > 300
        ORDER BY date DESC
        LIMIT ?
    """, conn, params=[n])
    conn.close()
    return df

@st.cache_data(ttl=300)
def load_orthostatic(days=90):
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

# --- Sport name mapping ---
SPORT_NAMES = {
    "15":  "Strength",
    "18":  "Indoor cycling",
    "177": "E-bike",
    "83":  "Auto-detected",
}

def sport_name(code):
    return SPORT_NAMES.get(str(code), f"Sport {code}")

# --- Load data ---
df_load     = load_training_load()
df_recovery = load_nightly_recharge()
df_sessions = load_recent_sessions()
df_ortho    = load_orthostatic()

# --- Today's snapshot ---
today_str = date.today().isoformat()
yesterday_str = (date.today() - timedelta(days=1)).isoformat()

today_load = df_load[df_load["date"].dt.date == date.today()]
today_rec  = df_recovery[df_recovery["date"].dt.date == date.today()]
yest_rec   = df_recovery[df_recovery["date"].dt.date == date.today() - timedelta(days=1)]

# Use yesterday's recovery if today's not yet available
rec_row = today_rec.iloc[0] if not today_rec.empty else (yest_rec.iloc[0] if not yest_rec.empty else None)
load_row = today_load.iloc[0] if not today_load.empty else df_load.iloc[-1] if not df_load.empty else None

# --- Header ---
st.title("🏋️ Polar Training Dashboard")
st.caption(f"Last updated: {date.today().strftime('%A, %B %d %Y')}")

# --- Today's status metrics ---
st.subheader("Today's status")
col1, col2, col3, col4, col5 = st.columns(5)

if load_row is not None:
    tsb = load_row["tsb"]
    atl = load_row["atl"]
    ctl = load_row["ctl"]
    tsb_color = "normal" if tsb >= -5 else ("inverse" if tsb < -15 else "off")
    col1.metric("TSB (form)", f"{tsb:.1f}", help="Positive = fresh, negative = fatigued")
    col2.metric("CTL (fitness)", f"{ctl:.1f}", help="42-day training load average")
    col3.metric("ATL (fatigue)", f"{atl:.1f}", help="7-day training load average")

if rec_row is not None:
    col4.metric("Recovery", f"{int(rec_row['recovery_indicator'])}/6",
                help="Polar nightly recharge recovery indicator")
    col5.metric("Nocturnal RMSSD", f"{int(rec_row['rmssd'])} ms",
                help="HRV during sleep")

st.divider()

# --- CTL / ATL / TSB chart ---
st.subheader("Training load — fitness, fatigue & form")

fig = go.Figure()

# Daily load bars
fig.add_trace(go.Bar(
    x=df_load["date"], y=df_load["daily_load"],
    name="Daily TRIMP", marker_color="#90CAF9", opacity=0.6,
    yaxis="y2"
))

# CTL line
fig.add_trace(go.Scatter(
    x=df_load["date"], y=df_load["ctl"],
    name="CTL — fitness", line=dict(color="#1565C0", width=2.5)
))

# ATL line
fig.add_trace(go.Scatter(
    x=df_load["date"], y=df_load["atl"],
    name="ATL — fatigue", line=dict(color="#C62828", width=2.5)
))

# TSB as filled area
fig.add_trace(go.Scatter(
    x=df_load["date"], y=df_load["tsb"],
    name="TSB — form",
    line=dict(color="#2E7D32", width=1.5),
    fill="tozeroy",
    fillcolor="rgba(46,125,50,0.15)"
))

fig.update_layout(
    height=400,
    legend=dict(orientation="h", y=1.08),
    yaxis=dict(title="Load / Form"),
    yaxis2=dict(title="Daily TRIMP", overlaying="y", side="right", showgrid=False),
    hovermode="x unified",
    margin=dict(t=20, b=20)
)
st.plotly_chart(fig, width='stretch')

st.divider()

# --- Recovery & HRV ---
st.subheader("Recovery & HRV")

col_left, col_right = st.columns(2)

with col_left:
    st.caption("Nightly recharge — recovery indicator & ANS status")
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=df_recovery["date"], y=df_recovery["recovery_indicator"],
        name="Recovery (1–6)", marker_color="#FF9800", opacity=0.8
    ))
    fig2.add_trace(go.Scatter(
        x=df_recovery["date"], y=df_recovery["ans_status"],
        name="ANS status", line=dict(color="#7B1FA2", width=1.5),
        yaxis="y2"
    ))
    fig2.add_hline(y=0, line_dash="dash", line_color="grey", line_width=0.8, yref="y2")
    fig2.update_layout(
        height=300,
        yaxis=dict(title="Recovery indicator", range=[0, 7]),
        yaxis2=dict(title="ANS status", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10)
    )
    st.plotly_chart(fig2, width='stretch')

with col_right:
    st.caption("Nocturnal RMSSD & orthostatic RMSSD supine")
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=df_recovery["date"], y=df_recovery["rmssd"],
        name="Nocturnal RMSSD", line=dict(color="#00796B", width=2),
        fill="tozeroy", fillcolor="rgba(0,121,107,0.1)"
    ))
    if not df_ortho.empty:
        fig3.add_trace(go.Scatter(
            x=df_ortho["date"], y=df_ortho["rmssd_supine"],
            name="Morning RMSSD (supine)",
            mode="markers+lines",
            line=dict(color="#F57F17", width=1.5, dash="dot"),
            marker=dict(size=6)
        ))
    fig3.update_layout(
        height=300,
        yaxis=dict(title="RMSSD (ms)"),
        legend=dict(orientation="h", y=1.1),
        margin=dict(t=10, b=10)
    )
    st.plotly_chart(fig3, width='stretch')

st.divider()

# --- Recent sessions table ---
st.subheader("Recent training sessions")

df_sessions["Sport"] = df_sessions["sport_id"].apply(sport_name)
df_display = df_sessions[[
    "date", "Sport", "duration_min", "hr_avg", "hr_max", "calories", "distance_km"
]].rename(columns={
    "date":         "Date",
    "duration_min": "Duration (min)",
    "hr_avg":       "HR avg",
    "hr_max":       "HR max",
    "calories":     "Calories",
    "distance_km":  "Distance (km)"
})

st.dataframe(df_display, width='stretch', hide_index=True)