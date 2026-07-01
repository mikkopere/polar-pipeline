DROP TABLE IF EXISTS exercise_samples;
DROP TABLE IF EXISTS exercise_route;
DROP TABLE IF EXISTS hr_zones;
DROP TABLE IF EXISTS exercises;
DROP TABLE IF EXISTS training_sessions;
DROP TABLE IF EXISTS nightly_recharge;
DROP TABLE IF EXISTS sleep;
DROP TABLE IF EXISTS sleep_wake_events;
DROP TABLE IF EXISTS continuous_hr;
DROP TABLE IF EXISTS hrv_windows;
DROP TABLE IF EXISTS orthostatic_tests;
DROP TABLE IF EXISTS daily_physical;
DROP TABLE IF EXISTS daily_training_load;


PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS training_sessions
(
    session_id TEXT PRIMARY KEY,
    start_time TIMESTAMP,
    stop_time TIMESTAMP,
    date DATE,
    device_id TEXT,
    hr_avg INTEGER,
    hr_max INTEGER,
    training_benefit TEXT,
    recovery_time_sec INTEGER
);

CREATE TABLE IF NOT EXISTS exercises
(
    exercise_id TEXT PRIMARY KEY,
    session_id TEXT,
    start_time TIMESTAMP,
    stop_time TIMESTAMP,
    date DATE,
    sport_id TEXT,
    duration_sec INTEGER,
    cardio_load REAL,
    calories INTEGER,
    distance_m REAL,
    ascent_m REAL,
    descent_m REAL,
    FOREIGN KEY (session_id) REFERENCES training_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS hr_zones
(
    id INTEGER PRIMARY KEY,
    exercise_id TEXT,
    zone_number INTEGER,
    seconds_in_zone INTEGER,
    lower_limit INTEGER,
    upper_limit INTEGER,
    FOREIGN KEY (exercise_id) REFERENCES exercises(exercise_id)
);

CREATE TABLE IF NOT EXISTS nightly_recharge
(
    sleep_result_date DATE PRIMARY KEY,
    ans_status REAL,
    recovery_indicator INTEGER,
    recovery_indicator_sub_level INTEGER,
    ans_rate INTEGER,
    mean_nightly_recovery_rri INTEGER,
    mean_nightly_recovery_rmssd INTEGER,
    mean_baseline_rri INTEGER,
    sd_baseline_rri INTEGER,
    mean_baseline_rmssd INTEGER,
    sd_baseline_rmssd INTEGER,
    mean_baseline_respiration_interval INTEGER,
    sd_baseline_respiration_interval INTEGER
);

CREATE TABLE IF NOT EXISTS sleep
(
    sleep_date DATE PRIMARY KEY,
    sleep_score REAL,
    total_sleep_sec INTEGER,
    sleep_start TIMESTAMP,
    sleep_end TIMESTAMP
);

-- Raw sleep/wake state transitions from /v4/data/sleep-wake-vectors.
-- Offsets are milliseconds from the start of `date`; one vector per device.
CREATE TABLE IF NOT EXISTS sleep_wake_events
(
    date DATE,
    device_uuid TEXT,
    start_offset_ms INTEGER,
    state TEXT,
    PRIMARY KEY (date, device_uuid, start_offset_ms)
);

-- 24/7 heart rate from /v4/data/continuous-samples (~10 s resolution).
CREATE TABLE IF NOT EXISTS continuous_hr
(
    date DATE,
    device_uuid TEXT,
    offset_ms INTEGER,
    hr INTEGER,
    PRIMARY KEY (date, device_uuid, offset_ms)
);

-- 5-minute HRV windows aggregated from /v4/data/ppi-samples at fetch time.
-- Raw PPI (~80k samples/day) is not stored; only good-quality beats
-- (skin contact, no movement, error estimate <= 30 ms) enter the windows.
CREATE TABLE IF NOT EXISTS hrv_windows
(
    date DATE,
    device_uuid TEXT,
    window_start_ms INTEGER,
    rmssd REAL,
    mean_hr REAL,
    n_samples INTEGER,
    PRIMARY KEY (date, device_uuid, window_start_ms)
);

CREATE TABLE IF NOT EXISTS orthostatic_tests
(
    start_time TIMESTAMP PRIMARY KEY,
    date DATE,
    rr_avg_supine INTEGER,
    rr_min_standup INTEGER,
    rr_avg_stand INTEGER,
    rr_lt_avg_supine INTEGER,
    rr_lt_avg_stand INTEGER,
    rmssd_supine REAL,
    rmssd_stand REAL,
    rmssd_lt_avg_supine REAL,
    rmssd_lt_avg_stand REAL
);

CREATE TABLE IF NOT EXISTS daily_physical
(
    date DATE PRIMARY KEY,
    weight_kg REAL,
    resting_hr INTEGER,
    vo2max REAL
);

CREATE TABLE IF NOT EXISTS daily_training_load
(
    date DATE PRIMARY KEY,
    daily_load REAL,
    atl REAL,
    ctl REAL,
    tsb REAL
);

CREATE TABLE IF NOT EXISTS exercise_samples
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_id TEXT,
    t_offset_sec INTEGER,
    hr INTEGER,
    speed REAL,
    altitude REAL,
    cadence INTEGER,
    FOREIGN KEY (exercise_id) REFERENCES exercises(exercise_id)
);

CREATE TABLE IF NOT EXISTS exercise_route
(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exercise_id TEXT,
    t_offset_sec INTEGER,
    lat REAL,
    lon REAL,
    altitude REAL,
    FOREIGN KEY (exercise_id) REFERENCES exercises(exercise_id)
);

CREATE INDEX IF NOT EXISTS idx_samples_exercise
    ON exercise_samples(exercise_id);
CREATE INDEX IF NOT EXISTS idx_route_exercise
    ON exercise_route(exercise_id);