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
import plan_spec


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
    hr_lo: int = None,
    hr_hi: int = None,
    min_minutes: int = 15,
) -> pd.DataFrame:
    """Easy aerobic (Zone-2) steady sessions with pace normalised to a fixed HR.

    This is the dashboard's headline metric: the training plan's success signal
    is the easy-day split getting *faster at the same heart rate*. We isolate
    genuinely-easy steady sessions (avg HR in roughly Zone 2, not interval work,
    at least `min_minutes` long) and project each one's pace to a reference HR
    (`cap`, default config.NORM_SPLIT_HR) so sessions rowed at slightly
    different heart rates are comparable. The reference is deliberately a fixed
    baseline, NOT the live EASY_HR_CAP — if it tracked the moving training cap,
    raising the cap would rescale every past split and fake an improvement.

    Normalisation: pace scales inversely with HR (more effort → higher HR →
    faster pace), so pace_at_cap = pace * hr_avg / cap. Lower = better.

    Returns columns: date, pace_s, pace, hr_avg, spm, norm_pace_s, norm_pace,
    duration — oldest→newest. Empty frame if no qualifying sessions.
    """
    if cap is None:
        cap = config.NORM_SPLIT_HR
    # Session-selection window = live Zone 2. Was hardcoded 108-126 (Zone 2 at
    # the old MAX_HR of 180) and silently went stale on re-anchor; deriving it
    # keeps "genuinely easy" meaning what the zone model says it means.
    if hr_lo is None:
        hr_lo = config.HR_ZONES[1][2]
    if hr_hi is None:
        hr_hi = config.HR_ZONES[1][3]
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


def weekly_plan(df: pd.DataFrame, weeks: int = 8, uid: str = None) -> list:
    """Per-week adherence to the Mon/Wed/Fri plan, newest week first.

    For each of the last `weeks` calendar weeks (Mon-start) returns a dict:
      week (Timestamp, Monday), sessions (int), easy_pct (float 0-100),
      easy_done / intervals_done / steady_done (bool), and `items` — the
      classified sessions (label, kind, duration, hr, day).
    Weeks with no sessions are still included so gaps are visible. `easy_pct`
    uses stroke-accurate time-in-zone when `uid` is given (see
    _session_zone_minutes) so an interval day's warmup/cooldown minutes count
    as easy rather than dragging the whole session into the hard bucket.
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
        zone_min = {}
        for _, r in sub[sub["hr_zone"] > 0].iterrows():
            for z, minutes in _session_zone_minutes(r, uid).items():
                zone_min[z] = zone_min.get(z, 0.0) + minutes
        total_min = sum(zone_min.values())
        easy_min = sum(m for z, m in zone_min.items() if z <= 2)
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

    Returns {} when plan_start is None or the week is pre-plan. Returns
    {"skipped": True} when the week is a configured skipped (paused) week —
    it has no block/week_in_block, it just doesn't count. Otherwise returns
    {block, week_in_block, recovery}: 1-based week index within the current
    4-week cycle (computed from the plan week number, i.e. skipped weeks
    don't consume a slot) and whether it's a recovery week (the cycle's 4th
    week). Blocks end on recovery weeks.
    """
    if plan_start is None:
        return {}
    try:
        pw = plan_spec.plan_week_of(week_start, plan_start)
    except Exception:
        return {}
    if pw is None:
        return {}
    if pw == "skipped":
        return {"skipped": True}
    block = (pw - 1) // config.PLAN_BLOCK_WEEKS + 1
    week_in_block = (pw - 1) % config.PLAN_BLOCK_WEEKS + 1
    recovery = pw % config.PLAN_RECOVERY_EVERY == 0
    return {"block": block, "week_in_block": week_in_block, "recovery": recovery}


# ── "Next up" — the next planned session for the Overview card ─────────────
# Mon/Wed/Fri plan: day-of-week (Mon=0) → session type.
_PLAN_DOW = {0: "easy", 2: "interval", 4: "steady"}
# Optional strength pairing on rest days (from the plan's Tonal section).
_REST_STRENGTH = {1: "upper-body push/pull", 3: "core + lower body"}

# Friday steady-session target band: a sub-range inside Zone 3, narrower than
# the full zone. Derived from MAX_HR so it tracks a re-anchor — these were once
# hardcoded 130-140, which silently went stale when MAX_HR moved 180 -> 187.
_STEADY_HR_LO = round(0.72 * config.MAX_HR)
_STEADY_HR_HI = round(0.776 * config.MAX_HR)


def _zone_bounds() -> dict:
    """Zone number → (low_bpm, high_bpm) from the live config HR model."""
    return {zz[0]: (zz[2], zz[3]) for zz in config.HR_ZONES}


def _build_session(d, plan_start) -> dict:
    """Structured spec for the planned session on a Mon/Wed/Fri date `d`.

    Durations, reps, and the recovery-week template come from plan_spec
    (BLOCKS / RECOVERY_WEEK); HR targets still come from the live config zone
    model so they track MAX_HR / EASY_HR_CAP. On the Monday of the gate test
    week (plan_spec.GATE['test_week']) this returns the dedicated drift-test
    session instead of the regular Mon/Wed/Fri prescription — that Monday's
    session is the gate reading, not another training day. On any day inside
    a configured skipped (vacation) week, this returns a paused card instead
    — the plan pauses honestly rather than showing a missed session.
    """
    if plan_start is not None and plan_spec.is_skipped_week(d):
        resume = plan_spec.next_active_week_monday(d)
        resume_ctx = plan_week_label(resume, plan_start)
        block_phrase = (f" with block {resume_ctx['block']}"
                        if resume_ctx.get("block") else "")
        return {"type": "paused", "title": "Plan paused · vacation",
                "lines": ["No scheduled session this week",
                          "Anything easy counts — walk, hike, yoga",
                          "Keep effort conversational (Zone 1–2 feel)"],
                "goal": f"Plan resumes {resume.strftime('%a %b %d')}"
                        f"{block_phrase} — repeat the week, never compress.",
                "block_label": None, "recovery": False}

    test_date = plan_spec.gate_test_date(plan_start)
    if test_date is not None and d.normalize() == test_date:
        return {"type": "test", "title": "Drift test · gate decision",
                "lines": ["~15 min warmup",
                          "45–60 min steady · top of Zone 2 (HR ≈ upper-120s)",
                          "taken fresh — skip if carrying fatigue"],
                "goal": "Split-half drift on the steady portion decides the "
                        "Phase-2 gate and re-derives the Day-1 cap.",
                "block_label": None, "recovery": False}

    stype = _PLAN_DOW[d.weekday()]
    ctx = plan_week_label(d, plan_start)
    block = ctx.get("block")
    wib = ctx.get("week_in_block")
    recovery = ctx.get("recovery", False)
    block_label = (f"Block {block} · wk {wib}" if block else None)

    cap = config.EASY_HR_CAP
    z = _zone_bounds()

    if recovery:
        rw = plan_spec.RECOVERY_WEEK
        return {"type": "recovery", "title": "Recovery · Zone 1",
                "lines": [f"{rw['session_min']} min · no rest",
                          f"HR < {z[1][1]} bpm (Zone 1)", f"{rw['spm']} spm"],
                "goal": "Active recovery only — smooth and easy. No intervals, "
                        "no Zone 3. Arrive at next block genuinely fresh.",
                "block_label": (block_label + " · recovery") if block_label else None,
                "recovery": True}

    # Blocks beyond 3 are Phase 2 territory (not yet specified) — continue on
    # block 3's numbers as a placeholder; no plan start falls back to block 1.
    spec = plan_spec.BLOCKS[min(block, 3)] if block else plan_spec.BLOCKS[1]

    if stype == "easy":
        dur = spec["easy_min"]
        return {"type": "easy", "title": "Easy · Long Aerobic",
                "lines": [f"{dur} min · no rest",
                          f"HR < {cap} bpm (Zone 2)", "20–22 spm"],
                "goal": f"Hold HR under {cap} the whole row — don't chase a "
                        "split. The week's most important session.",
                "block_label": block_label, "recovery": False}

    if stype == "steady":
        dur = spec["steady_min"]
        return {"type": "steady", "title": "Steady Aerobic",
                "lines": [f"{dur} min · no rest",
                          f"HR {_STEADY_HR_LO}–{_STEADY_HR_HI} bpm (Zone 3)",
                          "22–24 spm"],
                "goal": "Conversational but firm — short sentences only. "
                        "Controlled, not hard.",
                "block_label": block_label, "recovery": False}

    # interval
    reps = spec["intervals"]
    return {"type": "interval", "title": "Intervals",
            "lines": ["5 min warmup", f"{reps} (work / 1:30 easy)",
                      "5 min cooldown", f"Work HR Zone 4 ({z[4][0]}–{z[4][1]})"],
            "goal": "Work intervals genuinely hard (Zone 4 tagging 5); back off "
                    "fully on recoveries. The one hard day — make it count.",
            "block_label": block_label, "recovery": False}


def _next_plan_day(from_date, inclusive: bool):
    """Next date that is a Mon/Wed/Fri, searching from `from_date`."""
    for i in range(0 if inclusive else 1, 8):
        cand = from_date + pd.Timedelta(days=i)
        if cand.weekday() in _PLAN_DOW:
            return cand
    return from_date  # unreachable (a plan day occurs within any 7-day window)


def next_workout(df: pd.DataFrame, now=None) -> dict:
    """What the Overview 'Next up' card should show.

    Returns {mode, today_label, is_today, today_done, when_label, session,
    rest_strength}:
      • mode "session" — today is a workout day not yet logged (is_today=True),
        or the next upcoming session if today's is already done / today is past.
      • mode "rest" — today is a rest day; `session` is the next erg session
        shown beneath the rest note, and `rest_strength` is the optional pairing.
    Schedule-aware via config.PLAN_START_DATE; falls back to block-1 baselines
    (no recovery/block context) when no plan start date is set.
    """
    plan_start = config.PLAN_START_DATE
    today = (pd.Timestamp(now) if now is not None else pd.Timestamp.now()).normalize()
    dow = int(today.weekday())
    today_label = today.strftime("%a")

    # Done-detection at day granularity: did anything get logged today?
    today_done = False
    if df is not None and not df.empty:
        logged = set(_utc(df["date"]).dt.tz_localize(None).dt.normalize())
        today_done = today in logged

    is_workout_today = dow in _PLAN_DOW

    if is_workout_today and not today_done:
        return {"mode": "session", "today_label": today_label, "is_today": True,
                "today_done": False, "when_label": "Today",
                "session": _build_session(today, plan_start), "rest_strength": None}

    if is_workout_today and today_done:
        nxt = _next_plan_day(today, inclusive=False)
        return {"mode": "session", "today_label": today_label, "is_today": False,
                "today_done": True, "when_label": nxt.strftime("%a %b %d"),
                "session": _build_session(nxt, plan_start), "rest_strength": None}

    # Rest day.
    nxt = _next_plan_day(today, inclusive=False)
    return {"mode": "rest", "today_label": today_label, "is_today": False,
            "today_done": today_done, "when_label": nxt.strftime("%a %b %d"),
            "session": _build_session(nxt, plan_start),
            "rest_strength": _REST_STRENGTH.get(dow)}


# ── Weekly HR-zone distribution (the pyramidal distribution check, 60/33/<10) ──

def _session_zone_minutes(row, uid: str = None) -> dict:
    """Per-zone minutes for one session — stroke-accurate when possible.

    When `uid` is given and this session's strokes are cached (see
    api.cached_strokes), uses true per-sample time-in-zone so an interval
    session's warmup/recovery/cooldown minutes land in Zones 1-2 instead of
    the whole session being bucketed by its (high) average HR — the fix for
    the misclassification 2.1 exists to solve. Falls back to a single bucket
    at the session's average-HR zone when no strokes are cached or uid is
    None. Returns {} for sessions with no HR at all.
    """
    if uid is not None:
        import api  # local import: avoids a module-load-time cycle with api→data
        strokes = api.cached_strokes(uid, int(row["id"]))
        if strokes:
            secs = time_in_zone_from_strokes(strokes)
            if secs:
                return {z: s / 60.0 for z, s in secs.items()}
    z = row.get("hr_zone", 0) or 0
    if not z:
        return {}
    return {z: (row.get("time_s", 0) or 0) / 60.0}


def weekly_zone_minutes(df: pd.DataFrame, days: int = 84, uid: str = None) -> pd.DataFrame:
    """Minutes per HR zone per ISO week.

    Uses stroke-accurate time-in-zone (via _session_zone_minutes) when `uid`
    is given, falling back to session-average classification for any session
    with no cached strokes — the two mix transparently. Sessions without HR
    (hr_zone 0) are dropped. Returns columns: week, hr_zone, minutes.
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

    rows = []
    for _, r in sub.iterrows():
        for z, minutes in _session_zone_minutes(r, uid).items():
            rows.append({"week": r["week"], "hr_zone": z, "minutes": minutes})
    if not rows:
        return sub.iloc[0:0][["week"]].assign(hr_zone=pd.Series(dtype=int),
                                               minutes=pd.Series(dtype=float))
    return (pd.DataFrame(rows).groupby(["week", "hr_zone"])["minutes"].sum()
               .reset_index())


def easy_ratio(zone_minutes: pd.DataFrame) -> float:
    """Share of total minutes spent easy (Zones 1-2) across the given frame.

    The plan targets ~60% easy by minutes (pyramidal 60/33/<10); warmups,
    cooldowns, and interval recoveries count as easy. Returns 0.0 when there's
    no data.
    """
    return distribution(zone_minutes)["easy"]


def distribution(zone_minutes: pd.DataFrame) -> dict:
    """Easy/moderate/hard shares of total minutes across the given frame.

    Easy = Zones 1-2, moderate = Zone 3, hard = Zones 4-5 — the plan's
    pyramidal targets (config.EASY_TARGET_PCT ~60 / MODERATE_TARGET_PCT ~33 /
    HARD_CEILING_PCT <10). Returns {"easy", "moderate", "hard"} as 0-1
    fractions that sum to ~1.0; all zero when there's no data.
    """
    zero = {"easy": 0.0, "moderate": 0.0, "hard": 0.0}
    if zone_minutes is None or zone_minutes.empty:
        return zero
    total = zone_minutes["minutes"].sum()
    if total <= 0:
        return zero

    def share(zones):
        return float(zone_minutes[zone_minutes["hr_zone"].isin(zones)]["minutes"].sum() / total)

    return {"easy": share([1, 2]), "moderate": share([3]), "hard": share([4, 5])}


# ── Aerobic decoupling within a single session ────────────────────────────

def decoupling(strokes: list, skip_s: float = 0.0) -> dict:
    """Pace:HR drift between the first and second half of a steady piece.

    Efficiency = speed / HR. If the aerobic system is holding up, efficiency
    stays roughly constant; if HR drifts up (or pace fades) in the back half,
    efficiency drops — that's cardiac drift. Returns
    {pct, first_ef, second_ef, analyzed_s} where a positive pct = efficiency
    dropped in the second half and analyzed_s is the time span of the samples
    used. <5% is generally considered well-coupled / aerobically sound.
    `skip_s` drops samples before that mark (ramp-in trim); default 0 keeps
    the whole piece. Returns {} when there isn't enough usable data.
    """
    pts = [s for s in (strokes or [])
           if s.get("hr", 0) > 0 and s.get("pace", 0) > 0
           and s.get("t", 0) >= skip_s]
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
        "analyzed_s": pts[-1].get("t", 0) - pts[0].get("t", 0),
    }


# ── Phase readiness (the "should I advance to Phase 2?" gate) ──────────────
# Easy-day decoupling thresholds from the plan summary's "When to Advance to
# Phase 2": <5% = aerobic base solid (ready); 5-10% = developing (hold a block);
# >10% = aerobic deficiency (stay in base). See rowing-plan-summary.md.
# The in-plan Monday rows only trend this signal; the formal gate is a
# dedicated drift test (15 min warmup + 45-60 min steady Zone 2) taken fresh
# after the block-3 recovery week — see the constants below.
READINESS_READY_PCT = 5.0
READINESS_DEVELOPING_PCT = 10.0
DRIFT_SKIP_S = 600.0       # trim ramp-in so in-plan reads compare to the test protocol
DRIFT_FULL_TEST_S = 2400.0  # 40 min analyzed; below this a reading is provisional
# Schedule (block/phase structure) lives in plan_spec now; GATE_OPEN_WEEK is
# kept here too since existing callers import it from data_extras.
GATE_OPEN_WEEK = plan_spec.GATE["open_week"]  # phase gate opens at the block-3 recovery week


def recent_easy_steady(df: pd.DataFrame, n: int = 3, hr_lo: int = None,
                       hr_hi: int = None, min_minutes: int = 30) -> list:
    """Most-recent easy Zone-2 steady sessions suitable for a drift check.

    Same easy-session filter as aerobic_efficiency (avg HR ~Zone 2, not interval
    work) but requires enough duration to show meaningful cardiac drift, is
    restricted to the current plan period (sessions before
    config.PLAN_START_DATE are excluded, when set), and returns the newest `n`
    as [{id, date}] newest-first. These are the sessions whose decoupling
    feeds the phase-readiness gate.
    """
    if df is None or df.empty:
        return []
    # Live Zone 2 — see the note in aerobic_efficiency; these must stay in step.
    if hr_lo is None:
        hr_lo = config.HR_ZONES[1][2]
    if hr_hi is None:
        hr_hi = config.HR_ZONES[1][3]
    sub = df[
        (df["hr_avg"] >= hr_lo)
        & (df["hr_avg"] <= hr_hi)
        & (df["category"] != "Interval")
        & (df["pace_s"] > 0)
        & (df["time_s"] >= min_minutes * 60)
    ]
    if config.PLAN_START_DATE:
        try:
            start = pd.Timestamp(config.PLAN_START_DATE).tz_localize("UTC")
            sub = sub[_utc(sub["date"]) >= start]
        except Exception:
            pass
    if sub.empty:
        return []
    sub = sub.sort_values("date", ascending=False).head(n)
    return [{"id": int(r["id"]), "date": r["date"]} for _, r in sub.iterrows()]


def readiness_from_decoupling(pcts: list) -> dict:
    """Classify aerobic-base readiness from recent easy-day decoupling values.

    `pcts`: decoupling percentages (positive = efficiency dropped in the back
    half). Uses the median so a single ragged session doesn't flip the verdict.
    Returns {status, median_pct, n} with status in
    {ready, developing, base, unknown}.
    """
    vals = [p for p in (pcts or []) if p is not None]
    if not vals:
        return {"status": "unknown", "median_pct": None, "n": 0}
    import statistics
    med = statistics.median(vals)
    if med < READINESS_READY_PCT:
        status = "ready"
    elif med < READINESS_DEVELOPING_PCT:
        status = "developing"
    else:
        status = "base"
    return {"status": status, "median_pct": med, "n": len(vals)}


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
