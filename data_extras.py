"""
Additions to data.py — pure helpers used by the refactored views.

Drop the contents of this file at the bottom of your existing data.py
(or import from here; both work). No new dependencies.

Functions:
  compute_period_kpis(df, days)    — trailing-window KPIs with deltas
  daily_meters(df, days)           — per-day meters for the heatmap
  pr_sparkline_series(df, dist)    — recent paces at one distance
  wod_percentile(rank, field)      — rank → top X%
  wod_summary(rows)                — aggregate stats across WOD attempts
"""
from datetime import timedelta
import pandas as pd


# ── KPI deltas: this period vs previous equal-length period ──────────────

def compute_period_kpis(df: pd.DataFrame, days: int = 30) -> dict:
    """
    Return KPIs for the trailing `days` window plus deltas vs the previous
    equal-length window. Used to populate the Overview KPI quadrant.
    """
    if df.empty:
        return {}
    now = pd.Timestamp.now(tz="UTC")
    df_date = (df["date"].dt.tz_convert("UTC")
               if df["date"].dt.tz is not None
               else df["date"].dt.tz_localize("UTC"))

    cur_start  = now - pd.Timedelta(days=days)
    prev_start = now - pd.Timedelta(days=days * 2)

    cur  = df[(df_date >= cur_start)  & (df_date <= now)]
    prev = df[(df_date >= prev_start) & (df_date <  cur_start)]

    def stat(sub):
        if sub.empty:
            return dict(meters=0, sessions=0, time_s=0, avg_pace_s=0)
        paces = sub["pace_s"].replace(0, pd.NA).dropna()
        return dict(
            meters=int((sub["distance_m"] + sub["rest_distance_m"]).sum()),
            sessions=len(sub),
            time_s=float((sub["time_s"] + sub["rest_time_s"]).sum()),
            avg_pace_s=float(paces.mean()) if len(paces) else 0.0,
        )

    a, b = stat(cur), stat(prev)
    # For pace, LOWER is better — flip sign so positive delta = improvement.
    pace_delta = -(a["avg_pace_s"] - b["avg_pace_s"]) if b["avg_pace_s"] > 0 else 0

    return {
        "meters":          a["meters"],
        "meters_delta":    a["meters"] - b["meters"],
        "sessions":        a["sessions"],
        "sessions_delta":  a["sessions"] - b["sessions"],
        "time_s":          a["time_s"],
        "time_s_delta":    a["time_s"] - b["time_s"],
        "avg_pace_s":      a["avg_pace_s"],
        "avg_pace_delta":  pace_delta,
    }


# ── Daily aggregates for the calendar heatmap ────────────────────────────

def daily_meters(df: pd.DataFrame, days: int = 84) -> pd.DataFrame:
    """
    One row per day for the trailing `days` days, with meters rowed
    (zero-filled where no workout). Returns: date, meters, dow (0=Mon), week.
    """
    now = pd.Timestamp.now(tz="UTC").normalize()
    start = now - pd.Timedelta(days=days - 1)

    df_date = (df["date"].dt.tz_convert("UTC")
               if df["date"].dt.tz is not None
               else df["date"].dt.tz_localize("UTC"))
    work = df.copy()
    work["day"] = df_date.dt.normalize()
    daily = (
        work.groupby("day")["distance_m"].sum()
            .reindex(pd.date_range(start, now, tz="UTC", freq="D"), fill_value=0)
            .reset_index()
            .rename(columns={"index": "date", "distance_m": "meters"})
    )
    daily["dow"]  = daily["date"].dt.dayofweek
    daily["week"] = (daily["date"] - daily["date"].min()).dt.days // 7
    return daily


# ── Sparkline series: per-event pace progression ─────────────────────────

def pr_sparkline_series(df: pd.DataFrame, distance: int, count: int = 12) -> list:
    """
    Pace values (s/500m) for the last `count` workouts at this distance
    (±2% tolerance). Oldest→newest. Empty list if no matches.
    """
    lo, hi = distance * 0.98, distance * 1.02
    sub = df[(df["distance_m"] >= lo) & (df["distance_m"] <= hi)]
    if sub.empty:
        return []
    return sub.sort_values("date").tail(count)["pace_s"].tolist()


# ── Workout-of-the-day percentile ────────────────────────────────────────

def wod_percentile(rank: int, field: int) -> int:
    """
    Convert rank-out-of-field to a percentile.
      rank=1, field=100   → 1   (top 1%)
      rank=50, field=100  → 50
      rank=100, field=100 → 100
    Used as `top = 100 - wod_percentile(...)` to get "you placed in the top X%".
    """
    if not field:
        return 0
    return max(1, round(100 * rank / field))


def wod_summary(wod_rows: list) -> dict:
    """Aggregate stats across logged WOD attempts. Rows need 'rank' and 'total'."""
    if not wod_rows:
        return {"count": 0, "avg_top": None, "best_top": None}
    tops = [100 - wod_percentile(r["rank"], r["total"]) for r in wod_rows]
    return {
        "count":    len(wod_rows),
        "avg_top":  round(sum(tops) / len(tops)),
        "best_top": max(tops),
    }
