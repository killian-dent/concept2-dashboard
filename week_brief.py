#!/usr/bin/env python3
"""
Weekly rowing-analysis brief (CLI).

Produces the JSON *decision brief* that the `rowing-week` skill turns into a
training-log entry. This is deliberately NOT a data dump of the dashboard —
the dashboard already renders the raw state visually. The brief carries only
what a *decision* needs and a chart can't show at a glance:

  • deltas (vs the target, vs last week) — movement, not state
  • attribution — which session/zone owns a gap
  • threshold crossings (`flags`) — surfaced only when something newly trips
  • gate progress — decoupling readiness + split-at-cap trend toward Phase 2

All numbers come from the dashboard's own functions (data_extras / data), so
the brief is identical to what the app shows by construction — no re-derived
metrics. Per-session and per-rep detail are included only as drill-down
backing for a headline, never as the headline.

The heavy lifting (fetch + time-in-zone + decoupling) is reused from the app:
this module only adds the decision layer on top and prints JSON to stdout.

Usage
-----
    # analyse the most recently rowed week (default)
    python week_brief.py

    # analyse a specific week (any date inside it; ISO Mon-start week)
    python week_brief.py --week 2026-07-06

    # skip the API and use only what's already cached (offline / fast)
    python week_brief.py --no-fetch

Token / user id / plan config are read from .streamlit/secrets.toml (same as
the dashboard), so run this from the project root. Emits JSON to stdout and
human-readable progress to stderr, so `python week_brief.py > brief.json`
captures a clean brief.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone

import pandas as pd

import config
import db
import api
import data_extras as dx
import plan_spec
from data import load_results_df, format_pace, format_duration, zone_for


# ── Fetch: bring the local store up to full detail + strokes ──────────────
# (sync() is inherited from the old export_concept2.py; strokes backfill is
# new and required — decoupling and stroke-accurate time-in-zone both need
# the per-stroke series, or they silently degrade.)

def _detailed_key(uid: str) -> str:
    return f"export:detailed_ids:{uid}"


def _load_detailed_ids(uid: str) -> set:
    stored = db.cache_peek(_detailed_key(uid))
    return set(stored) if isinstance(stored, list) else set()


def _save_detailed_ids(uid: str, ids: set) -> None:
    db.cache_set(_detailed_key(uid), sorted(ids))


def sync(uid: str, full: bool, delay: float) -> None:
    """Bring the local store up to full-detail (splits) for this user."""
    detailed = _load_detailed_ids(uid)
    if full:
        _log("Full refresh: fetching the complete result list…")
        summaries = api.fetch_results(uid)
        targets = [r["id"] for r in summaries]
        detailed = set()
    else:
        newest = db.get_newest_id(uid)
        if newest is None:
            _log("Empty cache: fetching the complete result list…")
            summaries = api.fetch_results(uid)
        else:
            _log(f"Incremental: looking for workouts newer than id {newest}…")
            summaries = api.fetch_results_incremental(uid, since_id=newest)
        new_ids = [r["id"] for r in summaries]
        if new_ids:
            db.upsert(uid, summaries)
        cached_ids = [w["id"] for w in db.get_all(uid)]
        targets = [i for i in cached_ids if i not in detailed] + [
            i for i in new_ids if i not in detailed
        ]
        targets = list(dict.fromkeys(targets))

    if targets:
        _log(f"Fetching full detail for {len(targets)} workout(s)…")
        fetched = []
        for n, rid in enumerate(targets, 1):
            time.sleep(delay)
            detail = api.fetch_result_detail(rid, user_id=int(uid))
            if detail:
                fetched.append(detail)
                detailed.add(rid)
        if fetched:
            db.upsert(uid, fetched)
        _save_detailed_ids(uid, detailed)

    # Strokes: needed for stroke-accurate zones + decoupling. Idempotent and
    # cheap once cached — safe to call every run.
    ids = [w["id"] for w in db.get_all(uid)]
    n = api.backfill_strokes(uid, ids)
    if n:
        _log(f"Backfilled strokes for {n} workout(s).")
    db.set_synced(uid)


# ── Week selection + framing ──────────────────────────────────────────────

def _monday(ts) -> pd.Timestamp:
    return pd.Timestamp(ts).normalize().to_period("W").start_time


def _naive_dates(df: pd.DataFrame) -> pd.Series:
    d = df["date"]
    d = d.dt.tz_convert("UTC") if d.dt.tz is not None else d.dt.tz_localize("UTC")
    return d.dt.tz_localize(None)


def _target_monday(df: pd.DataFrame, week_arg: str) -> pd.Timestamp:
    """Monday of the week to analyse. Default: the week of the latest session
    (i.e. the week just rowed). --week accepts any date inside a target week."""
    if week_arg:
        return _monday(week_arg)
    if df.empty:
        return _monday(pd.Timestamp.now())
    return _monday(_naive_dates(df).max())


def _week_slice(df: pd.DataFrame, monday: pd.Timestamp) -> pd.DataFrame:
    d = _naive_dates(df)
    return df[(d >= monday) & (d < monday + pd.Timedelta(weeks=1))].copy()


# ── Plan context (block / prescription) ───────────────────────────────────

def _week_context(monday: pd.Timestamp) -> dict:
    ps = config.PLAN_START_DATE
    label = dx.plan_week_label(monday, ps)
    plan_week = plan_spec.plan_week_of(monday, ps) if ps else None
    ctx = {
        "start": monday.strftime("%Y-%m-%d"),
        "end": (monday + pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
        "plan_week": plan_week if isinstance(plan_week, int) else None,
    }
    if not label:
        ctx["label"] = "pre-plan" if ps else "no-plan-start-set"
        ctx["prescription"] = None
        return ctx
    if label.get("skipped"):
        ctx["label"] = "skipped (paused — doesn't count as a plan week)"
        ctx["prescription"] = None
        return ctx
    block, wib, recovery = label["block"], label["week_in_block"], label["recovery"]
    ctx["block"], ctx["week_in_block"], ctx["recovery"] = block, wib, recovery
    if recovery:
        ctx["label"] = f"Block {block}, recovery week"
        ctx["prescription"] = dict(plan_spec.RECOVERY_WEEK)
    else:
        ctx["label"] = f"Block {block}, build week {wib}"
        ctx["prescription"] = dict(plan_spec.BLOCKS.get(block, {}))
    return ctx


# ── Distribution + attribution ────────────────────────────────────────────

def _zone_frame(rows):
    """[(zone, minutes), …] → a DataFrame distribution() understands."""
    return pd.DataFrame(rows, columns=["hr_zone", "minutes"]) if rows else \
        pd.DataFrame(columns=["hr_zone", "minutes"])


def _session_zones(sub: pd.DataFrame, uid: str):
    """Per-session zone-minutes for a week slice, plus the aggregate frame.
    Reuses the app's stroke-accurate _session_zone_minutes for exact parity."""
    per_session, agg_rows = [], []
    for _, r in sub[sub["hr_zone"] > 0].sort_values("date").iterrows():
        zmin = dx._session_zone_minutes(r, uid)  # {zone: minutes}
        for z, m in zmin.items():
            agg_rows.append((z, m))
        per_session.append((r, zmin))
    return per_session, _zone_frame(agg_rows)


def _pct(frame):
    d = dx.distribution(frame)
    return {k: round(v * 100, 1) for k, v in d.items()}


# Which zones a session is *meant* to spend time in — lets attribution tell a
# prescribed Z3 (Friday steady) apart from a leaked one (Wed recoveries).
_EXPECTED_ZONES = {
    "easy":        {1, 2},
    "steady":      {3},
    "intervals":   {1, 2, 4, 5},   # work reps hard, recoveries/warmup easy —
                                   # Z3 on an interval day is a leak, not a target
    "recovery":    {1},
}


def _attribution(per_session, dist, targets):
    """For each off-target zone bucket, who contributed the minutes and was it
    expected. Pure data — the skill interprets it into a decision."""
    bucket_zones = {"easy": [1, 2], "moderate": [3], "hard": [4, 5]}
    out = {}
    for bucket, zones in bucket_zones.items():
        gap = round(dist[bucket] - targets[bucket], 1)
        contribs = []
        for r, zmin in per_session:
            m = round(sum(zmin.get(z, 0.0) for z in zones), 1)
            if m <= 0:
                continue
            kind = dx.classify_session(r)
            expected = bool(set(zones) & _EXPECTED_ZONES.get(kind, set()))
            contribs.append({
                "day": _naive_dates(pd.DataFrame([r])).iloc[0].strftime("%a"),
                "kind": kind, "label": r["label"],
                "minutes": m, "expected": expected,
            })
        contribs.sort(key=lambda c: c["minutes"], reverse=True)
        out[bucket] = {"vs_target": gap, "contributors": contribs}
    return out


# ── Interval-day drill-down ───────────────────────────────────────────────

def _interval_day(sub: pd.DataFrame):
    """Raw split table for the week's interval session, each split zone-tagged.
    No plan-band judgment here (the target split band lives in the plan/log,
    not in Concept2 data) — the skill applies that. `is_work` is a transparent
    short-and-fast heuristic the skill can override."""
    iv = sub[sub["category"] == "Interval"]
    if iv.empty:
        return None
    r = iv.sort_values("date").iloc[-1]
    splits = r.get("splits") or []
    if not splits:
        return {"label": r["label"], "day": _naive_dates(pd.DataFrame([r])).iloc[0].strftime("%a"),
                "splits": [], "note": "no per-split data cached"}
    paces = [s.get("pace", 0) for s in splits if s.get("pace", 0) > 0]
    avg_pace = sum(paces) / len(paces) if paces else 0
    rows = []
    for s in splits:
        t = s.get("time", 0) or 0
        pace_s = s.get("pace", 0) or 0
        hr = s.get("heart_rate", 0) or 0
        is_work = bool(t and t <= 120 and pace_s and avg_pace and pace_s < avg_pace)
        rows.append({
            "split": s.get("split_number", 0),
            "time": format_duration(t),
            "distance_m": s.get("distance", 0),
            "pace": format_pace(pace_s), "pace_s": round(pace_s, 1),
            "spm": s.get("spm", 0),
            "hr": hr, "zone": zone_for(hr) if hr else None,
            "is_work": is_work,
        })
    return {
        "label": r["label"],
        "day": _naive_dates(pd.DataFrame([r])).iloc[0].strftime("%a"),
        "prescription": None,  # filled from plan context by the caller
        "splits": rows,
    }


# ── Fitness-outcome layer: gate progress ──────────────────────────────────

def _gate(df: pd.DataFrame, uid: str, target_monday: pd.Timestamp) -> dict:
    """Decoupling readiness + split-at-cap trend + weeks-to-gate. Reuses the
    app's recent_easy_steady / decoupling / readiness_from_decoupling exactly."""
    out = {}
    rows = dx.recent_easy_steady(df, n=3)
    pcts, per = [], []
    for row in rows:
        strokes = api.cached_strokes(uid, row["id"])
        dec = dx.decoupling(strokes, skip_s=dx.DRIFT_SKIP_S) if strokes else {}
        if dec:
            pct = round(dec["pct"], 1)
            pcts.append(pct)
            per.append({
                "date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                "decoupling_pct": pct,
                "analyzed_min": round(dec["analyzed_s"] / 60, 1),
                "provisional": dec["analyzed_s"] < dx.DRIFT_FULL_TEST_S,
            })
    read = dx.readiness_from_decoupling(pcts)
    out["readiness"] = {"status": read["status"],
                        "median_decoupling_pct": (round(read["median_pct"], 1)
                                                  if read["median_pct"] is not None else None),
                        "n": read["n"], "target": "< 5% on a 40+ min test"}
    out["recent_easy_decoupling"] = per

    eff = dx.aerobic_efficiency(df)
    summ = dx.aerobic_efficiency_summary(eff)
    if summ.get("count"):
        out["split_at_cap"] = {
            "latest_norm_split": summ["latest_norm_pace"],
            "improved_s_over_span": round(summ["improved_s"], 1),
            "n_sessions": summ["count"],
            "target": "2:30–2:45, trending toward 2:30",
        }

    gate_date = plan_spec.gate_test_date(config.PLAN_START_DATE)
    if gate_date is not None:
        weeks = int((gate_date - target_monday).days // 7)
        out["gate_test"] = {"date": gate_date.strftime("%Y-%m-%d"),
                            "weeks_away": weeks}
    return out


# ── Threshold flags (empty when the week is clean) ────────────────────────

def _flags(sub: pd.DataFrame, ctx: dict, gate: dict, df: pd.DataFrame) -> list:
    flags = []
    for _, r in sub.iterrows():
        c = (r.get("comments") or "").lower()
        if "instability" in c or "hrm" in c:
            flags.append({"level": "warn", "kind": "hrm",
                          "msg": f"HRM instability flagged on {r['label']} — trust its HR/zones less."})
        if not r.get("hr_zone"):
            flags.append({"level": "warn", "kind": "missing_hr",
                          "msg": f"{r['label']} has no HR — excluded from the distribution."})

    # Recovery week that didn't stay easy.
    if ctx.get("recovery"):
        hot = sub[sub["hr_zone"] > 1]
        if not hot.empty:
            flags.append({"level": "warn", "kind": "recovery_hot",
                          "msg": f"Recovery week but {len(hot)} session(s) went above Zone 1."})

    # Drag-factor drift vs the recent norm (setup changed under you).
    recent = df.copy()
    recent = recent[recent["drag_factor"] > 0]
    if not recent.empty:
        norm = recent["drag_factor"].median()
        for _, r in sub[sub["drag_factor"] > 0].iterrows():
            if abs(r["drag_factor"] - norm) > 10:
                flags.append({"level": "info", "kind": "drag_drift",
                              "msg": f"{r['label']} drag {int(r['drag_factor'])} vs ~{int(norm)} norm — check the damper."})

    # Positive: an easy row *in the analysed week* already under the 5% gate.
    # (readiness/recent_easy_decoupling stay a recent global trend; the flag is
    # an event, so it must fall inside the week window — not just after its start.)
    wk_start, wk_end = pd.Timestamp(ctx["start"]), pd.Timestamp(ctx["end"])
    for row in gate.get("recent_easy_decoupling", []):
        if row["decoupling_pct"] < dx.READINESS_READY_PCT and \
           wk_start <= pd.Timestamp(row["date"]) <= wk_end:
            note = " (provisional, <40 min)" if row["provisional"] else ""
            flags.append({"level": "info", "kind": "gate_progress",
                          "msg": f"Easy row {row['date']} decoupling {row['decoupling_pct']}% — under the 5% gate{note}."})
    return flags


# ── Assemble ──────────────────────────────────────────────────────────────

def build_brief(df: pd.DataFrame, uid: str, week_arg: str) -> dict:
    monday = _target_monday(df, week_arg)
    ctx = _week_context(monday)
    sub = _week_slice(df, monday)

    per_session, agg = _session_zones(sub, uid)
    dist = _pct(agg)
    targets = {"easy": config.EASY_TARGET_PCT, "moderate": config.MODERATE_TARGET_PCT,
               "hard": config.HARD_CEILING_PCT}

    prev = _week_slice(df, monday - pd.Timedelta(weeks=1))
    _, prev_agg = _session_zones(prev, uid)
    prev_dist = _pct(prev_agg)
    vs_last = {k: round(dist[k] - prev_dist.get(k, 0.0), 1) for k in dist} \
        if not prev_agg.empty else None

    sessions = []
    for r, zmin in per_session:
        sessions.append({
            "day": _naive_dates(pd.DataFrame([r])).iloc[0].strftime("%a"),
            "kind": dx.classify_session(r),
            "label": r["label"], "duration": r["duration"],
            "hr_avg": int(r["hr_avg"] or 0),
            "zone_min": {int(z): round(m, 1) for z, m in sorted(zmin.items())},
        })

    interval = _interval_day(sub)
    if interval and ctx.get("prescription"):
        interval["prescription"] = ctx["prescription"].get("intervals")

    gate = _gate(df, uid, monday)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "week": ctx,
        "adherence": {
            "sessions": int(len(sub)),
            "kinds_present": sorted({dx.classify_session(r) for _, r in sub.iterrows()}),
        },
        "distribution": {
            "easy_pct": dist["easy"], "moderate_pct": dist["moderate"], "hard_pct": dist["hard"],
            "total_min": round(agg["minutes"].sum(), 1) if not agg.empty else 0.0,
            "targets": targets,
            "vs_target": {k: round(dist[k] - targets[k], 1) for k in dist},
            "vs_last_week": vs_last,
            "attribution": _attribution(per_session, dist, targets),
        },
        "sessions": sessions,
        "interval_day": interval,
        "gate": gate,
        "flags": _flags(sub, ctx, gate, df),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description="Weekly rowing-analysis brief (JSON to stdout).")
    p.add_argument("--week", default="", help="Any date inside the target week (default: most recent rowed week).")
    p.add_argument("--no-fetch", action="store_true", help="Use only cached data; skip the API.")
    p.add_argument("--full", action="store_true", help="Ignore the detail cache and re-fetch everything.")
    p.add_argument("--delay", type=float, default=0.2, help="Seconds between detail API calls (default 0.2).")
    args = p.parse_args()

    if config.is_placeholder_token():
        _log("ERROR: no API token configured. Set API_TOKEN in .streamlit/secrets.toml "
             "(run from the project root).")
        return 1
    uid = str(config.USER_ID) if config.USER_ID else "me"

    if not args.no_fetch:
        sync(uid, full=args.full, delay=args.delay)

    raw = db.get_all(uid)
    if not raw:
        _log("No workouts in the local store — nothing to analyse.")
        return 0
    df = load_results_df(tuple(raw))

    brief = build_brief(df, uid, args.week)
    json.dump(brief, sys.stdout, indent=2, default=str, ensure_ascii=False)
    sys.stdout.write("\n")
    _log(f"\nBrief for {brief['week']['label']} ({brief['week']['start']}) — "
         f"{brief['distribution']['easy_pct']}/{brief['distribution']['moderate_pct']}/"
         f"{brief['distribution']['hard_pct']} easy/mod/hard, {len(brief['flags'])} flag(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
