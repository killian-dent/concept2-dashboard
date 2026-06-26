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
import re
from datetime import timedelta
import pandas as pd

import config


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


# ── Aerobic efficiency: easy-day pace at a fixed HR over time ─────────────

def aerobic_efficiency(
    df: pd.DataFrame,
    cap: int = None,
    hr_lo: int = 108,
    hr_hi: int = 126,
    min_minutes: int = 15,
) -> pd.DataFrame:
    """Easy aerobic (Zone-2) steady sessions with pace normalised to a fixed HR.

    This is the dashboard's headline metric: the training plan's success signal
    is the easy-day split getting *faster at the same heart rate*. We isolate
    genuinely-easy steady sessions (avg HR in roughly Zone 2, not interval work,
    at least `min_minutes` long) and project each one's pace to a reference HR
    (`cap`, default the plan's easy-HR ceiling) so sessions rowed at slightly
    different heart rates are comparable.

    Normalisation: pace scales inversely with HR (more effort → higher HR →
    faster pace), so pace_at_cap = pace * hr_avg / cap. Lower = better.

    Returns columns: date, pace_s, pace, hr_avg, spm, norm_pace_s, norm_pace,
    duration — oldest→newest. Empty frame if no qualifying sessions.
    """
    if cap is None:
        cap = config.EASY_HR_CAP
    if df.empty:
        return df
    sub = df[
        (df["hr_avg"] >= hr_lo)
        & (df["hr_avg"] <= hr_hi)
        & (df["category"] != "Interval")
        & (df["pace_s"] > 0)
        & (df["time_s"] >= min_minutes * 60)
    ].copy()
    if sub.empty:
        return sub
    from data import format_pace
    sub = sub.sort_values("date")
    sub["norm_pace_s"] = sub["pace_s"] * sub["hr_avg"] / cap
    sub["norm_pace"] = sub["norm_pace_s"].apply(format_pace)
    return sub[["date", "pace_s", "pace", "hr_avg", "spm",
                "norm_pace_s", "norm_pace", "duration"]]


def aerobic_efficiency_summary(eff: pd.DataFrame) -> dict:
    """Headline numbers for the efficiency tracker.

    Compares the average normalised pace of the earliest vs. latest sessions
    (up to 3 each) to express the trend as a pace delta. Positive `improved_s`
    means the easy-day pace at a fixed HR has gotten faster.
    """
    if eff is None or eff.empty:
        return {"count": 0}
    n = min(3, len(eff))
    early = eff.head(n)["norm_pace_s"].mean()
    late = eff.tail(n)["norm_pace_s"].mean()
    from data import format_pace
    return {
        "count": len(eff),
        "latest_norm_pace_s": float(eff.iloc[-1]["norm_pace_s"]),
        "latest_norm_pace": format_pace(float(eff.iloc[-1]["norm_pace_s"])),
        "early_norm_pace_s": float(early),
        "late_norm_pace_s": float(late),
        # Lower pace = faster, so improvement is early minus late.
        "improved_s": float(early - late),
    }


def _utc(series: pd.Series) -> pd.Series:
    """Coerce a datetime series to UTC (tz-aware), localising naive values."""
    return (series.dt.tz_convert("UTC")
            if series.dt.tz is not None else series.dt.tz_localize("UTC"))


# ── Session classification + weekly plan adherence ───────────────────────

def classify_session(row) -> str:
    """Bucket a session against the plan's three workout types.

    Returns one of: 'intervals' (the Wed hard day), 'easy' (Mon long-easy,
    Zone 1-2 steady), 'steady' (Fri Zone-3 steady), 'hard_steady' (a steady
    piece run too hard — Zone 4+), 'short' (warmup/cooldown/test, <12 min), or
    'other' (steady with no HR to classify).
    """
    cat = row.get("category", "SteadyState")
    dur = row.get("time_s", 0) or 0
    z = row.get("hr_zone", 0) or 0
    if cat == "Interval":
        return "intervals"
    if dur < 12 * 60:
        return "short"
    if z and z <= 2:
        return "easy"
    if z == 3:
        return "steady"
    if z >= 4:
        return "hard_steady"
    return "other"


def weekly_plan(df: pd.DataFrame, weeks: int = 8) -> list:
    """Per-week adherence to the Mon/Wed/Fri plan, newest week first.

    For each of the last `weeks` calendar weeks (Mon-start) returns a dict:
      week (Timestamp, Monday), sessions (int), easy_pct (float 0-100),
      easy_done / intervals_done / steady_done (bool), and `items` — the
      classified sessions (label, kind, duration, hr, day).
    Weeks with no sessions are still included so gaps are visible.
    """
    if df.empty:
        return []
    d = _utc(df["date"]).dt.tz_localize(None)
    work = df.copy()
    work["day"] = d
    work["wk"] = d.dt.to_period("W").apply(lambda p: p.start_time)
    work["kind"] = work.apply(classify_session, axis=1)

    this_week = pd.Timestamp.now().normalize().to_period("W").start_time
    week_starts = [this_week - pd.Timedelta(weeks=i) for i in range(weeks)]

    out = []
    for wk in week_starts:
        sub = work[work["wk"] == wk]
        kinds = set(sub["kind"])
        zone_min = (sub.assign(minutes=sub["time_s"] / 60.0)
                    [sub["hr_zone"] > 0])
        total_min = zone_min["minutes"].sum() if not zone_min.empty else 0
        easy_min = (zone_min[zone_min["hr_zone"] <= 2]["minutes"].sum()
                    if not zone_min.empty else 0)
        items = [
            {"label": r["label"], "kind": r["kind"],
             "duration": r["duration"], "hr": int(r["hr_avg"] or 0),
             "day": r["day"].strftime("%a")}
            for _, r in sub.sort_values("day").iterrows()
        ]
        out.append({
            "week": wk,
            "sessions": len(sub),
            "easy_pct": (easy_min / total_min * 100) if total_min else 0.0,
            "easy_done": "easy" in kinds,
            "intervals_done": "intervals" in kinds,
            "steady_done": "steady" in kinds,
            "items": items,
        })
    return out


def plan_week_label(week_start, plan_start) -> dict:
    """Block/recovery context for a week given the plan start date.

    Returns {} when plan_start is None. Otherwise {block, week_in_block,
    recovery}: 1-based week index within the current 6-week block and whether
    it's a recovery week (every 4th week).
    """
    if plan_start is None:
        return {}
    try:
        start = pd.Timestamp(plan_start).normalize().to_period("W").start_time
    except Exception:
        return {}
    delta_weeks = int((pd.Timestamp(week_start).normalize() - start).days // 7)
    if delta_weeks < 0:
        return {}
    import config
    block = delta_weeks // config.PLAN_BLOCK_WEEKS + 1
    week_in_block = delta_weeks % config.PLAN_BLOCK_WEEKS + 1
    recovery = (delta_weeks + 1) % config.PLAN_RECOVERY_EVERY == 0
    return {"block": block, "week_in_block": week_in_block, "recovery": recovery}


# ── Weekly HR-zone distribution (the 80/20 pyramid check) ─────────────────

def weekly_zone_minutes(df: pd.DataFrame, days: int = 84) -> pd.DataFrame:
    """Minutes per HR zone per ISO week (session-level, by each row's avg HR).

    This is the practical default for the whole-history view: it classifies a
    session by its average heart rate rather than fetching per-stroke data for
    every workout. Sessions without HR (hr_zone 0) are dropped. Returns columns:
    week, hr_zone, minutes.
    """
    if df.empty:
        return df
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(days=days)
    d = _utc(df["date"])
    sub = df[(d >= start) & (df["hr_zone"] > 0)].copy()
    if sub.empty:
        return sub
    sub["week"] = _utc(sub["date"]).dt.tz_localize(None).dt.to_period("W").dt.start_time
    sub["minutes"] = sub["time_s"] / 60.0
    return (sub.groupby(["week", "hr_zone"])["minutes"].sum()
               .reset_index())


def easy_ratio(zone_minutes: pd.DataFrame) -> float:
    """Share of total minutes spent easy (Zones 1-2) across the given frame.

    The plan targets ~80% — keeping two-thirds to four-fifths of weekly minutes
    genuinely easy. Returns 0.0 when there's no data.
    """
    if zone_minutes is None or zone_minutes.empty:
        return 0.0
    total = zone_minutes["minutes"].sum()
    if total <= 0:
        return 0.0
    easy = zone_minutes[zone_minutes["hr_zone"] <= 2]["minutes"].sum()
    return float(easy / total)


# ── Aerobic decoupling within a single session ────────────────────────────

def decoupling(strokes: list) -> dict:
    """Pace:HR drift between the first and second half of a steady piece.

    Efficiency = speed / HR. If the aerobic system is holding up, efficiency
    stays roughly constant; if HR drifts up (or pace fades) in the back half,
    efficiency drops — that's cardiac drift. Returns
    {pct, first_ef, second_ef} where a positive pct = efficiency dropped in the
    second half. <5% is generally considered well-coupled / aerobically sound.
    Returns {} when there isn't enough usable data.
    """
    pts = [s for s in (strokes or [])
           if s.get("hr", 0) > 0 and s.get("pace", 0) > 0]
    if len(pts) < 6:
        return {}
    mid = pts[len(pts) // 2].get("t", 0)

    def ef(group):
        if not group:
            return 0.0
        # speed (m/s) = 500 / pace_s; efficiency = speed / HR.
        speeds = [500.0 / s["pace"] for s in group]
        hrs = [s["hr"] for s in group]
        return (sum(speeds) / len(speeds)) / (sum(hrs) / len(hrs))

    first = ef([s for s in pts if s.get("t", 0) <= mid])
    second = ef([s for s in pts if s.get("t", 0) > mid])
    if first <= 0 or second <= 0:
        return {}
    return {
        "pct": (first - second) / first * 100.0,
        "first_ef": first,
        "second_ef": second,
    }


def time_in_zone_from_strokes(strokes: list) -> dict:
    """Seconds spent in each HR zone, from a per-stroke series.

    Each sample's duration is the gap to the next sample (the last sample
    inherits the previous gap). Returns {zone_number: seconds}; zones with no
    time are omitted. Empty dict if there's no stroke/HR data.
    """
    from data import zone_for
    if not strokes:
        return {}
    secs: dict = {}
    n = len(strokes)
    for i, s in enumerate(strokes):
        if i + 1 < n:
            dt = strokes[i + 1].get("t", 0) - s.get("t", 0)
        elif i > 0:
            dt = s.get("t", 0) - strokes[i - 1].get("t", 0)
        else:
            dt = 0
        dt = max(0.0, float(dt))
        z = zone_for(s.get("hr", 0))
        if z:
            secs[z] = secs.get(z, 0.0) + dt
    return secs


# ── Challenge progress (computed locally — API has no per-user progress) ──

def meters_in_window(df: pd.DataFrame, start, end) -> int:
    """Total meters rowed (work + rest) with date in [start, end] inclusive."""
    if df.empty:
        return 0
    d = _utc(df["date"])
    start = pd.Timestamp(start).tz_localize("UTC") if pd.Timestamp(start).tz is None else pd.Timestamp(start)
    end = pd.Timestamp(end).tz_localize("UTC") if pd.Timestamp(end).tz is None else pd.Timestamp(end)
    end = end + pd.Timedelta(days=1)  # make the end date inclusive
    sub = df[(d >= start) & (d < end)]
    if sub.empty:
        return 0
    return int((sub["distance_m"] + sub["rest_distance_m"]).sum())


_GOAL_PAT = re.compile(r"([\d][\d,]*)\s*(m\b|meters|metres|k\b)", re.IGNORECASE)


def parse_goal_meters(*texts) -> int:
    """Best-effort meters goal parsed from a challenge name/description.

    The challenges API returns no explicit target, but meter goals are usually
    stated in the text (e.g. "Row 200,000m" / "2,000,000 meters" / "100k").
    Returns the largest meters-like number found, or 0 if none.
    """
    best = 0
    for t in texts:
        if not t:
            continue
        for num, unit in _GOAL_PAT.findall(str(t)):
            try:
                val = int(num.replace(",", ""))
            except ValueError:
                continue
            if unit.lower().startswith("k"):
                val *= 1000
            best = max(best, val)
    return best


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
