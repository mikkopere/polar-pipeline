DROP TABLE IF EXISTS hr_zones;
DROP TABLE IF EXISTS training_sessions;
DROP TABLE IF EXISTS nightly_recharge;
DROP TABLE IF EXISTS sleep;
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
    sport_id TEXT,
    duration_sec INTEGER,
    hr_avg INTEGER,
    hr_max INTEGER,
    cardio_load REAL,
    calories INTEGER,
    distance_m REAL,
    training_benefit TEXT,
    recovery_time_sec INTEGER
);

CREATE TABLE IF NOT EXISTS hr_zones
(
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    zone_number INTEGER,
    seconds_in_zone INTEGER,
    FOREIGN KEY (session_id) REFERENCES training_sessions(session_id)
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
