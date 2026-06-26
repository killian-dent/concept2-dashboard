"""
Data loading, caching, sample generation, and derived-field calculations.
"""

import random
import math
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# Pace / watts helpers
# ---------------------------------------------------------------------------

def pace_from_time_distance(time_seconds: float, distance_meters: float) -> float:
    """Return pace in seconds per 500 m."""
    if distance_meters == 0:
        return 0.0
    return (time_seconds / distance_meters) * 500.0


def format_pace(pace_seconds: float) -> str:
    """Format pace seconds as M:SS.s  e.g. 2:05.3"""
    if pace_seconds <= 0:
        return "—"
    mins = int(pace_seconds // 60)
    secs = pace_seconds % 60
    return f"{mins}:{secs:04.1f}"


def format_duration(total_seconds: float) -> str:
    """Format seconds as H:MM:SS."""
    total_seconds = int(total_seconds)
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def watts_from_pace(pace_seconds: float) -> float:
    """Convert pace (s/500m) to watts using the standard Concept2 formula."""
    if pace_seconds <= 0:
        return 0.0
    return 2.80 / (pace_seconds ** 3) * (500 ** 3)


def calories_from_watts_time(watts: float, time_seconds: float) -> int:
    """Approximate kcal from average watts and duration."""
    # Concept2 formula: kcal/hr = watts * 4 * 0.8604
    return int(watts * 4 * 0.8604 * (time_seconds / 3600))


# ---------------------------------------------------------------------------
# Heart-rate zones (training-plan model)
# ---------------------------------------------------------------------------

def zone_for(bpm: float) -> int:
    """Map a heart rate to a zone number 1-5 using config.HR_ZONES.

    Returns 0 when no/zero HR. A bpm below the Zone-1 floor still counts as
    Zone 1 (very easy); at/above the top counts as the max zone. Boundaries are
    inclusive at the lower edge (e.g. 126 bpm → Zone 3, not Zone 2).
    """
    if not bpm or bpm <= 0:
        return 0
    from config import HR_ZONES
    for z, _name, _lo, hi in HR_ZONES:
        if bpm < hi:
            return z
    return HR_ZONES[-1][0]


def zone_name(zone: int) -> str:
    """Human label for a zone number (e.g. 2 → 'Aerobic base'). '' for 0."""
    from config import HR_ZONES
    for z, name, _lo, _hi in HR_ZONES:
        if z == zone:
            return name
    return ""


# ---------------------------------------------------------------------------
# Sample data generation
# ---------------------------------------------------------------------------

_WORKOUT_TEMPLATES = [
    # (label, distance_m, time_duration_seconds, type)  — type: 'distance' or 'time'
    ("2000m",         2000,   None,  "distance"),
    ("5000m",         5000,   None,  "distance"),
    ("10000m",       10000,   None,  "distance"),
    ("30 min piece",  None,   1800,  "time"),
    ("6000m",         6000,   None,  "distance"),
    ("1000m",         1000,   None,  "distance"),
    ("500m",           500,   None,  "distance"),
    ("Half Marathon", 21097,  None,  "distance"),
]

# Realistic pace ranges (s/500m) per workout type
_PACE_RANGES = {
    "500m":        (95,  108),
    "1000m":       (100, 113),
    "2000m":       (105, 118),
    "5000m":       (110, 125),
    "6000m":       (112, 127),
    "10000m":      (115, 130),
    "30 min piece":(115, 128),
    "Half Marathon":(120, 138),
}


def _random_splits(distance_m: Optional[float], time_s: float, pace_s: float, spm: int) -> list[dict]:
    """Generate per-500m splits with slight variation around the mean pace."""
    if distance_m:
        n_splits = max(1, int(distance_m / 500))
        split_dist = distance_m / n_splits
    else:
        n_splits = int(time_s / 500)  # rough
        split_dist = 500.0

    splits = []
    for i in range(n_splits):
        jitter = random.uniform(-1.5, 1.5)
        split_pace = pace_s + jitter
        split_time = split_pace * (split_dist / 500)
        split_watts = watts_from_pace(split_pace)
        splits.append({
            "split_number": i + 1,
            "distance": round(split_dist),
            "time": round(split_time, 1),
            "pace": split_pace,
            "pace_formatted": format_pace(split_pace),
            "spm": spm + random.randint(-1, 1),
            "watts": round(split_watts, 1),
            "heart_rate": random.randint(140, 178),
        })
    return splits


def generate_sample_results() -> list[dict]:
    """Generate 65 realistic fake Concept2 workouts over the last 6 months."""
    random.seed(42)
    results = []
    base_date = datetime(2025, 11, 11)  # 6 months before today (2026-05-11)
    result_id = 1000

    # Slightly improve pace over time (training effect)
    improvement_per_workout = 0.03  # seconds faster per 500m per workout

    workout_number = 0
    current_date = base_date

    while current_date < datetime(2026, 5, 11) and len(results) < 65:
        # 4-6 workouts per week, skip some days
        template = random.choice(_WORKOUT_TEMPLATES)
        label, dist, duration, wtype = template

        pace_min, pace_max = _PACE_RANGES[label]
        base_pace = random.uniform(pace_min, pace_max)
        # Apply gradual improvement
        pace = base_pace - (workout_number * improvement_per_workout)
        pace = max(pace, pace_min - 5)  # floor

        spm = random.randint(20, 28)
        # Span easy→hard so zone-based views have a realistic spread to show.
        hr_avg = random.randint(112, 172)

        if wtype == "distance":
            time_s = pace * (dist / 500.0)
            distance_m = dist
        else:
            # timed piece — calculate distance from pace
            distance_m = round((duration / pace) * 500)
            time_s = duration

        watts = watts_from_pace(pace)
        kcal = calories_from_watts_time(watts, time_s)
        splits = _random_splits(distance_m if wtype == "distance" else None, time_s, pace, spm)

        hr_min = max(90, hr_avg - random.randint(8, 16))
        hr_max = min(185, hr_avg + random.randint(5, 14))
        result = {
            "id": result_id,
            "date": current_date.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "date_display": current_date.strftime("%Y-%m-%d"),
            "workout_type": "rower",
            "workout": {
                "type": wtype,
                "label": label,
                "category": "SteadyState",
                "raw_type": "FixedTimeRow" if wtype == "time" else "FixedDistRow",
                "distance": distance_m,
                "time": round(time_s * 10),  # tenths of seconds (API format)
                "time_seconds": round(time_s, 1),
                "spm": spm,
                "stroke_count": int(spm * (time_s / 60.0)),
                "heart_rate_average": hr_avg,
                "heart_rate_min": hr_min,
                "heart_rate_max": hr_max,
                "heart_rate_ending": hr_avg + random.randint(-3, 4),
                "heart_rate_recovery": max(80, hr_max - random.randint(18, 32)),
                "watts_average": round(watts, 1),
                "wattminutes": round(watts * (time_s / 60.0)),
                "calories": kcal,
                "comments": "",
                "verified": True,
                "ranked": True,
                "pace": pace,
                "pace_formatted": format_pace(pace),
                "splits": splits,
            },
        }
        results.append(result)

        result_id += 1
        workout_number += 1

        # Advance date: 2-4 days between workouts to spread 65 sessions over ~6 months
        gap = random.choices([1, 2, 3, 4, 5], weights=[10, 30, 30, 20, 10])[0]
        current_date += timedelta(days=gap)

    # Sort newest first (API convention)
    results.sort(key=lambda r: r["date"], reverse=True)
    # Re-assign IDs in order so they match iteration order
    for i, r in enumerate(results):
        r["id"] = 1000 + i

    return results


# ---------------------------------------------------------------------------
# DataFrame construction & derived fields
# ---------------------------------------------------------------------------

def load_results_df(raw_results: tuple) -> pd.DataFrame:
    """
    Convert raw API result list to a clean DataFrame with derived columns.
    Pass raw_results as a tuple (not list) to keep st.cache_data happy.
    """
    rows = []
    for r in raw_results:
        w = r.get("workout", {})
        time_s = w.get("time_seconds") or (w.get("time", 0) / 10.0)
        dist = w.get("distance", 0)
        pace_s = w.get("pace") or pace_from_time_distance(time_s, dist)
        watts = w.get("watts_average") or watts_from_pace(pace_s)
        hr_avg = w.get("heart_rate_average", 0)
        rows.append({
            "id":               r["id"],
            "date":             pd.to_datetime(r["date"]),
            "label":            w.get("label", "Workout"),
            "type":             w.get("type", "distance"),
            "category":         w.get("category", "SteadyState"),
            "raw_type":         w.get("raw_type", ""),
            "distance_m":       dist,
            "time_s":           time_s,
            "duration":         format_duration(time_s),
            "rest_distance_m":  w.get("rest_distance", 0) or 0,
            "rest_time_s":      w.get("rest_time_seconds", 0) or 0,
            "pace_s":           pace_s,
            "pace":             format_pace(pace_s),
            "spm":              w.get("spm", 0),
            "stroke_count":     w.get("stroke_count", 0) or 0,
            "hr_avg":           hr_avg,
            "hr_min":           w.get("heart_rate_min", 0) or 0,
            "hr_max":           w.get("heart_rate_max", 0) or 0,
            "hr_ending":        w.get("heart_rate_ending", 0) or 0,
            "hr_recovery":      w.get("heart_rate_recovery", 0) or 0,
            "hr_zone":          zone_for(hr_avg),
            "watts":            round(watts, 1),
            "wattminutes":      w.get("wattminutes", 0) or 0,
            "calories":         w.get("calories", 0),
            "drag_factor":      w.get("drag_factor", 0) or 0,
            "comments":         w.get("comments", "") or "",
            "verified":         bool(w.get("verified", False)),
            "ranked":           bool(w.get("ranked", False)),
            "splits":           w.get("splits", []),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date", ascending=False).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def compute_summary(df: pd.DataFrame) -> dict:
    today = pd.Timestamp.now(tz="UTC").normalize()
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    df_date = df["date"].dt.tz_localize("UTC") if df["date"].dt.tz is None else df["date"].dt.tz_convert("UTC")

    # Concept2 counts both work and rest distance/time toward lifetime totals
    total_dist = df["distance_m"] + df["rest_distance_m"]
    total_time = df["time_s"] + df["rest_time_s"]

    mask_month = df_date >= month_start
    mask_year  = df_date >= year_start

    this_month_m = total_dist[mask_month].sum()
    this_year_m  = total_dist[mask_year].sum()

    # Current streak: consecutive days with at least one workout
    workout_dates = sorted(df["date"].dt.date.unique(), reverse=True)
    streak = 0
    check = today.date()
    for d in workout_dates:
        if d == check or d == check - timedelta(days=1):
            streak += 1
            check = d - timedelta(days=1)
        else:
            break

    return {
        "total_meters":   int(total_dist.sum()),
        "total_workouts": len(df),
        "total_time_s":   float(total_time.sum()),
        "this_month_m":   int(this_month_m),
        "this_year_m":    int(this_year_m),
        "streak_days":    streak,
    }


# ---------------------------------------------------------------------------
# Personal Records
# ---------------------------------------------------------------------------

def compute_prs(df: pd.DataFrame) -> pd.DataFrame:
    from config import STANDARD_DISTANCES, STANDARD_TIMED

    distance_records = []
    for name, dist in STANDARD_DISTANCES.items():
        # Allow ±2% tolerance on distance
        lo, hi = dist * 0.98, dist * 1.02
        subset = df[(df["distance_m"] >= lo) & (df["distance_m"] <= hi)]
        if subset.empty:
            distance_records.append({"Event": name, "Best Time": "—", "Best Pace": "—", "Date": "—"})
        else:
            best_row = subset.loc[subset["pace_s"].idxmin()]
            distance_records.append({
                "Event":      name,
                "Best Time":  best_row["duration"],
                "Best Pace":  best_row["pace"],
                "Date":       best_row["date"].strftime("%Y-%m-%d"),
            })

    timed_records = []
    for name, dur in STANDARD_TIMED.items():
        lo, hi = dur * 0.98, dur * 1.02
        subset = df[(df["time_s"] >= lo) & (df["time_s"] <= hi) & (df["type"] == "time")]
        if subset.empty:
            timed_records.append({"Event": name, "Best Distance": "—", "Best Pace": "—", "Date": "—"})
        else:
            best_row = subset.loc[subset["distance_m"].idxmax()]
            timed_records.append({
                "Event":         name,
                "Best Distance": f"{int(best_row['distance_m']):,} m",
                "Best Pace":     best_row["pace"],
                "Date":          best_row["date"].strftime("%Y-%m-%d"),
            })

    return pd.DataFrame(distance_records), pd.DataFrame(timed_records)


# ---------------------------------------------------------------------------
# Chart data helpers
# ---------------------------------------------------------------------------

def weekly_meters(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate meters rowed per ISO week."""
    tmp = df.copy()
    tmp["week"] = tmp["date"].dt.to_period("W").dt.start_time
    return tmp.groupby("week")["distance_m"].sum().reset_index().rename(
        columns={"distance_m": "meters"}
    )


def pace_trend(df: pd.DataFrame, min_dist: int = 0, max_dist: int = 99999) -> pd.DataFrame:
    """Pace over time, filtered to workouts within a distance range."""
    subset = df[(df["distance_m"] >= min_dist) & (df["distance_m"] <= max_dist)].copy()
    subset = subset.sort_values("date")
    return subset[["date", "pace_s", "pace", "label", "distance_m"]]


# ---------------------------------------------------------------------------
# Sample challenge data
# ---------------------------------------------------------------------------

SAMPLE_CHALLENGES = [
    {
        "name": "May 2026 — Row 200,000m",
        "description": "Row 200,000 meters during May 2026.",
        "goal": 200_000,
        "progress": 143_500,
        "unit": "m",
        "ends": "2026-05-31",
    },
    {
        "name": "Spring 5K Challenge",
        "description": "Complete 10 x 5000m pieces in April–May.",
        "goal": 10,
        "progress": 7,
        "unit": "workouts",
        "ends": "2026-05-31",
    },
    {
        "name": "2026 Annual — Row 2,000,000m",
        "description": "Row 2,000,000 meters in 2026.",
        "goal": 2_000_000,
        "progress": 876_000,
        "unit": "m",
        "ends": "2026-12-31",
    },
]
