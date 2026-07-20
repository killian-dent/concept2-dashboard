"""
plan_spec.py — declarative spec for the pyramidal aerobic-base rowing plan.

Narrative source of truth: rostrum's outputs/rowing-plan-summary.md
("Training Structure — Blocks and Phases" / "When to Advance to Phase 2" /
"Recovery Week"). This module holds the plan's *structure* — durations,
reps, gate criteria, schedule math — as data. Live HR numbers still come
from config's zone model (MAX_HR / EASY_HR_CAP) so they track overrides;
this module only knows shapes and dates. No streamlit import — safe to
import from any layer, including data_extras and the views.

Public API:
  PHASE1, PHASE2      — phase-level metadata
  BLOCKS              — per-block build-week prescriptions (1, 2, 3)
  RECOVERY_WEEK       — the recovery-week template (every 4th week)
  GATE                — the Phase 1 -> Phase 2 gate: open week, test week,
                         protocol, and the criteria checklist
  SKIPPED_WEEKS       — calendar weeks that pause the plan (vacation, etc.)
  skipped_week_starts()        — normalized set of skipped week Mondays
  is_skipped_week(date)        — whether a date falls in a skipped week
  plan_week_of(date, plan_start) — 1-based plan week for a date, or None
                                    (pre-plan) or "skipped" (a paused week)
  gate_test_date(plan_start)  — Monday of the gate drift-test week
  milestones(plan_start)      — chronological schedule milestones
"""
import config
import pandas as pd


# ── Skipped weeks — vacations/pauses that don't count toward plan weeks ────
# List Mondays (any date in the week is tolerated — everything here
# normalizes to the week's Monday via to_period("W").start_time). A skipped
# week doesn't advance the plan: it doesn't get a block/week_in_block label,
# it isn't a training week, and every week/milestone/gate date after it
# shifts right by one calendar week per skipped week.
SKIPPED_WEEKS = ["2026-07-27"]  # vacation — plan pauses, week doesn't count


def skipped_week_starts() -> set:
    """Normalized (Monday) timestamps for every configured skipped week."""
    return {pd.Timestamp(d).normalize().to_period("W").start_time
            for d in SKIPPED_WEEKS}


def is_skipped_week(date) -> bool:
    """Whether `date` falls in a configured skipped (paused) week."""
    wk = pd.Timestamp(date).normalize().to_period("W").start_time
    return wk in skipped_week_starts()


# ── Phase 1: Aerobic base (~12 weeks = three 4-week blocks) ───────────────
PHASE1 = {
    "name": "Aerobic base",
    "weeks": 12,
    "n_blocks": 3,
    "variable": "volume",  # the only thing allowed to progress in Phase 1
    "focus": "Build aerobic base; hold intensity discipline constant across "
             "the whole phase and let only volume creep up, one variable "
             "at a time.",
}

# ── Phase 2: Build (intensity) — deliberately not fully specified ────────
# Numbers get set from Phase-1 data at the gate (see GATE below); what
# follows is a sketch, not a prescription.
PHASE2 = {
    "name": "Build (intensity)",
    "tbd": True,
    "weeks_estimate": "6-8",
    "sketch": [
        "Day-1 cap rises to ~126 bpm, re-derived from the drift test",
        "Day-2 intervals lengthen toward 4-5 × 2:00",
        "Day-3 steady stays Zone 3",
        "Optional 4th day: a second easy Zone-2 row, only if fatigue is "
        "consistently well managed",
    ],
}

# ── Build-week prescriptions by block (Mon easy / Wed intervals / Fri steady) ──
# Recovery weeks (every 4th) override all of this — see RECOVERY_WEEK.
BLOCKS = {
    1: {
        "easy_min": 35,
        "steady_min": 30,
        "intervals": "6 × 1:00",
        "focus": "Establish zones. Learn to hold HR under the cap. Don't "
                 "progress anything.",
        "change_label": "establish zones, hold HR under cap",
    },
    2: {
        "easy_min": 40,
        "steady_min": 35,
        "intervals": "6 × 1:00",
        "focus": f"Extend easy days +5 min. Leave intervals alone. Watch "
                 f"the {config.NORM_SPLIT_HR}-bpm split.",
        "change_label": "Mondays go to 40 min",
    },
    3: {
        "easy_min": 40,
        "steady_min": 35,
        "intervals": "7 × 1:00 or 6 × 1:15",
        "focus": "Add to the hard day only (one change, not both).",
        "change_label": "hard day gets longer (7 × 1:00 or 6 × 1:15)",
    },
}

# ── Recovery week (every 4th week, all three blocks share this template) ──
RECOVERY_WEEK = {
    "session_min": 20,
    "spm": "18–20",
    # Derived, not hardcoded — tracks a MAX_HR override (see module docstring).
    "notes": f"All three days Zone 1 (cap = Zone 1 top, "
             f"{config.HR_ZONES[0][3]} bpm at MAX_HR {config.MAX_HR}). "
             f"No intervals, no Zone 3.",
}

# ── The Phase 1 -> Phase 2 gate ───────────────────────────────────────────
# A measurement, not a date: the gate *opens* at week 12 (the block-3
# recovery week), but the formal reading is a dedicated drift test taken
# fresh on the Monday of week 13. ALL criteria must hold to advance.
GATE = {
    "open_week": 12,
    "test_week": 13,
    "protocol": "~15 min warmup, then 45-60 min steady at the top of Zone "
                "2, split-half drift on the steady portion",
    "criteria": [
        {"key": "drift", "label": "Easy-day decoupling",
         "target": "< 5% on a full-length (>=40 min analyzed) test"},
        {"key": "pace", "label": f"{config.NORM_SPLIT_HR}-bpm normalized split",
         "target": "inside ~2:30-2:45, trending toward 2:30"},
        {"key": "week", "label": "Plan week",
         "target": "week 12 or later"},
        # Freshness (no elevated resting HR, good sleep, no dread) is also
        # required to advance, but it's self-judged with no dashboard data —
        # it lives in the plan doc and the readiness verdict copy, not here.
    ],
}


def _week_monday(plan_start, week_num: int) -> pd.Timestamp:
    """Monday of the given 1-based plan week — same normalization
    plan_week_label uses (period('W').start_time), so schedule math here
    stays consistent with the block/recovery labeling in data_extras.

    Walks forward one calendar week at a time from plan_start, but only
    counts weeks that aren't in SKIPPED_WEEKS — so a skipped (vacation) week
    doesn't consume a plan-week number, and everything after it (labels,
    milestones, the gate) lands one calendar week later automatically.
    """
    current = pd.Timestamp(plan_start).normalize().to_period("W").start_time
    count = 0
    while True:
        if not is_skipped_week(current):
            count += 1
            if count == week_num:
                return current
        current += pd.Timedelta(weeks=1)


def next_active_week_monday(date) -> pd.Timestamp:
    """Monday of the next non-skipped week at or after `date`'s week.

    For a date inside a skipped week, this is the plan-resume date. For a
    date in a normal week, it's just that week's Monday.
    """
    wk = pd.Timestamp(date).normalize().to_period("W").start_time
    while is_skipped_week(wk):
        wk += pd.Timedelta(weeks=1)
    return wk


def plan_week_of(date, plan_start):
    """1-based plan week number containing `date`, counting only non-skipped
    weeks since plan_start.

    Returns None when `date` is before plan_start (pre-plan) or plan_start
    is unset. Returns the string "skipped" when `date` falls in a configured
    skipped (paused) week — it isn't a counted plan week at all. This is the
    single source of truth for plan-week math; data_extras.plan_week_label
    and views/plan.py's gate signals both route through it.
    """
    if plan_start is None:
        return None
    start = pd.Timestamp(plan_start).normalize().to_period("W").start_time
    wk = pd.Timestamp(date).normalize().to_period("W").start_time
    if wk < start:
        return None
    if is_skipped_week(wk):
        return "skipped"
    count = 0
    current = start
    while current <= wk:
        if not is_skipped_week(current):
            count += 1
        current += pd.Timedelta(weeks=1)
    return count


def gate_test_date(plan_start):
    """Monday of the gate drift-test week, or None if plan_start is unset."""
    if plan_start is None:
        return None
    return _week_monday(plan_start, GATE["test_week"])


def milestones(plan_start) -> list:
    """Chronological schedule milestones: [{date, label, kind}, ...].

    kind is one of {block_start, recovery, gate_open, drift_test, skipped}.
    Covers each block's start (with what changes), each block's recovery
    week, the gate opening (week 12, same date as block 3's recovery week),
    the dedicated drift test (Monday of week 13), and one entry per
    configured skipped week (labeled honestly as a pause, not a missed
    week). Empty list when plan_start is None.
    """
    if plan_start is None:
        return []
    block_weeks = config.PLAN_BLOCK_WEEKS
    out = []
    for skipped in skipped_week_starts():
        out.append({
            "date": skipped,
            "label": "Vacation — plan paused (week doesn't count)",
            "kind": "skipped",
        })
    for b in range(1, PHASE1["n_blocks"] + 1):
        block_start_week = (b - 1) * block_weeks + 1
        out.append({
            "date": _week_monday(plan_start, block_start_week),
            "label": f"Block {b} — {BLOCKS[b]['change_label']}",
            "kind": "block_start",
        })
        recovery_week = block_start_week + block_weeks - 1
        rdate = _week_monday(plan_start, recovery_week)
        out.append({
            "date": rdate,
            "label": "Recovery week (all Zone 1, 20 min)",
            "kind": "recovery",
        })
        if b == PHASE1["n_blocks"]:
            out.append({
                "date": rdate,
                "label": f"Gate opens (wk {GATE['open_week']})",
                "kind": "gate_open",
            })
    out.append({
        "date": gate_test_date(plan_start),
        "label": "Drift test — gate decision",
        "kind": "drift_test",
    })
    out.sort(key=lambda m: m["date"])
    return out
