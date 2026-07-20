import os


def _secret(key: str, default: str = "") -> str:
    """Read from st.secrets (Streamlit Cloud), then env vars, then default."""
    try:
        import streamlit as st
        return str(st.secrets.get(key, os.environ.get(key, default)))
    except Exception:
        return os.environ.get(key, default)


API_TOKEN = _secret("API_TOKEN", "YOUR_TOKEN_HERE")

_uid = _secret("USER_ID", "")
USER_ID = int(_uid) if _uid.isdigit() else None

API_BASE_URL = "https://log.concept2.com/api"
API_VERSION = "v1"

RESULTS_PER_PAGE = 250  # API max — fewer pages on the first full fetch

# ── Heart-rate zones ───────────────────────────────────────────────────────
# Zones are derived proportionally from MAX_HR (see rowing-plan-summary.md).
# The 180 below is a generic fallback, NOT the operating value: the real anchor
# is personal health data and lives in the MAX_HR secret/env var, because this
# repo is public. Set it from an observed stroke-level peak (observed peaks beat
# estimates); all zone boundaries shift with it. Verify a candidate peak is
# sustained across several strokes rather than a single-stroke HRM spike.
_max_hr = _secret("MAX_HR", "180")
MAX_HR = int(_max_hr) if _max_hr.isdigit() else 180

# (zone, name, %max low, %max high) — pyramidal plan zones.
_ZONE_PCTS = [
    (1, "Recovery",      0.50, 0.60),
    (2, "Aerobic base",  0.60, 0.70),
    (3, "Aerobic/tempo", 0.70, 0.80),
    (4, "Threshold",     0.80, 0.90),
    (5, "VO2max/max",    0.90, 1.00),
]
# Materialised as (zone, name, low_bpm, high_bpm). zone_for() in data.py maps a
# bpm to a zone number using these boundaries.
HR_ZONES = [(z, name, round(lo * MAX_HR), round(hi * MAX_HR))
            for z, name, lo, hi in _ZONE_PCTS]

# Day-1 easy-aerobic HR ceiling — a *training prescription*. Rises as the
# aerobic base develops (and shifts if MAX_HR is re-anchored); override via
# the EASY_HR_CAP secret/env var.
_easy_cap = _secret("EASY_HR_CAP", "120")
EASY_HR_CAP = int(_easy_cap) if _easy_cap.isdigit() else 120

# Reference HR for the success metric (easy-day split normalised to a fixed HR).
# Deliberately SEPARATE from EASY_HR_CAP despite starting at the same value:
# the cap is a prescription that moves, while this is a measurement baseline
# that must stay fixed or the whole trend loses comparability. Changing it
# rescales every historical split and manufactures phantom improvement — only
# change it if you intend to rebase the metric and discard the old trend.
_norm_hr = _secret("NORM_SPLIT_HR", "120")
NORM_SPLIT_HR = int(_norm_hr) if _norm_hr.isdigit() else 120

# Optional plan start date (YYYY-MM-DD, the Monday of week 1). When set, the
# Plan tab can show the 4-week cycle position and recovery-week markers. Leave
# unset to skip those annotations.
PLAN_START_DATE = _secret("PLAN_START_DATE", "").strip() or None

# The plan runs in repeating 4-week blocks: 3 build weeks + 1 recovery week
# (i.e. every 4th week is a recovery week — the evidence-backed 3:1 cadence).
# Blocks are the recurring unit; a *phase* (e.g. the ~12-week aerobic base =
# three blocks) is a higher-level, decoupling-gated concept tracked in the plan
# summary, not by week count here. See rowing-plan-summary.md "Training
# Structure" / "When to Advance to Phase 2".
PLAN_BLOCK_WEEKS = 4
PLAN_RECOVERY_EVERY = 4

# Intensity-distribution targets, by weekly minutes (pyramidal, not 80/20 —
# see rostrum's "Intensity Distribution Reconciliation" research note).
# Warmups, cooldowns, and interval recoveries count as easy minutes.
EASY_TARGET_PCT = 60
MODERATE_TARGET_PCT = 33
HARD_CEILING_PCT = 10

STANDARD_DISTANCES = {
    "100m":          100,
    "500m":          500,
    "1000m":         1000,
    "2000m":         2000,
    "5000m":         5000,
    "6000m":         6000,
    "10000m":        10000,
    "Half Marathon": 21097,
    "Marathon":      42195,
}

STANDARD_TIMED = {
    "30 min": 1800,
    "60 min": 3600,
}


def is_placeholder_token() -> bool:
    return API_TOKEN in ("YOUR_TOKEN_HERE", "", None)
