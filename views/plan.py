"""
Plan tab — training-plan tracking, built around the pyramidal 80/20 HR-zone
program (see rostrum/resources/rowing-plan-summary.md).

The plan's whole purpose is to lower a chronically high heart rate by building
aerobic base, and its single success metric is the *easy-day split getting
faster at a fixed heart rate*. This tab leads with that.

Sections (added incrementally):
  • Aerobic efficiency — easy-day pace normalised to ~120 bpm, over time. ⭐
  • (later) Weekly zone distribution, plan adherence.
"""
import altair as alt
import pandas as pd
import streamlit as st

import api
import config
import ui
from data import format_pace, zone_name
from data_extras import (aerobic_efficiency, aerobic_efficiency_summary,
                          weekly_zone_minutes, easy_ratio,
                          weekly_plan, plan_week_label,
                          recent_easy_steady, readiness_from_decoupling,
                          decoupling, READINESS_READY_PCT,
                          READINESS_DEVELOPING_PCT)

# The plan's illustrative target band for the easy-day split at 120 bpm.
_TARGET_FAST_S = 150  # 2:30
_TARGET_SLOW_S = 165  # 2:45


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workout data yet — once you log easy aerobic rows, your "
                "progress against the plan shows up here.")
        return

    st.caption(
        f"Tracking the aerobic-base plan · easy HR ceiling {config.EASY_HR_CAP} bpm · "
        f"max HR {config.MAX_HR}"
    )

    _render_efficiency(df)
    _render_readiness(df)
    _render_zone_distribution(df)
    _render_adherence(df)


# ── Aerobic efficiency tracker (the headline metric) ─────────────────────

def _render_efficiency(df: pd.DataFrame):
    cap = config.EASY_HR_CAP
    eff = aerobic_efficiency(df, cap=cap)

    ui.section_label(f"Aerobic efficiency · easy pace at {cap} bpm")

    if eff.empty:
        st.info(
            "No easy aerobic sessions logged yet. The plan's Monday long-easy "
            f"row (35+ min with HR held in Zone 2, under ~{cap} bpm) is what "
            "feeds this chart — it's the most important workout of the week."
        )
        return

    s = aerobic_efficiency_summary(eff)

    # ── Hero: current normalised easy pace + trend since the start ───────
    improved = s["improved_s"]
    faster = improved > 0
    delta_color = ui.ACCENT_PR if faster else ui.ACCENT_WARN
    if abs(improved) >= 0.1:
        arrow = "↓" if faster else "↑"  # lower pace = faster = down
        delta_txt = f"{arrow} {abs(improved):.1f}s {'faster' if faster else 'slower'} since start"
    else:
        delta_color = ui.INK_2
        delta_txt = "holding steady"
    spark = ui.sparkline_html(eff["norm_pace_s"].tolist(), width=140, height=26,
                              color=delta_color, fill=True)

    st.html(
        f"<div style='padding:14px 16px;background:{ui.BG_1};"
        f"border:1px solid {ui.LINE};border-radius:10px;display:flex;"
        f"justify-content:space-between;align-items:center;'>"
        f"<div>"
        f"<div style='font-size:34px;font-weight:600;letter-spacing:-0.02em;"
        f"font-variant-numeric:tabular-nums;color:{ui.INK_0};'>"
        f"{s['latest_norm_pace']}<span style='font-size:13px;color:{ui.INK_2};"
        f"font-weight:400;'>/500</span></div>"
        f"<div style='font-size:12px;color:{delta_color};font-weight:500;"
        f"margin-top:3px;'>{delta_txt}</div></div>"
        f"<div style='text-align:right;'>{spark}"
        f"<div style='font-size:10px;color:{ui.INK_3};margin-top:5px;'>"
        f"{s['count']} easy rows · target ~2:30</div></div>"
        f"</div>"
    )
    st.caption(
        f"Each point is an easy Zone-2 row, pace normalised to {cap} bpm. "
        "**Lower is better** — the goal is the line drifting down over time."
    )

    # ── Chart: normalised easy pace over time ────────────────────────────
    plot = eff.copy()
    if plot["date"].dt.tz is not None:
        plot["date"] = plot["date"].dt.tz_convert("UTC").dt.tz_localize(None)
    plot["date"] = plot["date"].dt.normalize()

    pace_expr = (
        "floor(datum.value/60)+':'+"
        "(floor(datum.value%60)<10?'0'+floor(datum.value%60):''+floor(datum.value%60))"
    )

    y_min = min(plot["norm_pace_s"].min(), _TARGET_FAST_S) - 3
    y_max = max(plot["norm_pace_s"].max(), _TARGET_SLOW_S) + 3

    base = alt.Chart(plot).encode(
        x=alt.X("date:T", axis=alt.Axis(format="%b %d", title=None,
                                         labelAngle=-30, tickCount="day")),
        y=alt.Y(
            "norm_pace_s:Q",
            scale=alt.Scale(domain=[y_min, y_max], reverse=True),
            axis=alt.Axis(labelExpr=pace_expr, title=None),
        ),
        tooltip=[
            alt.Tooltip("date:T", format="%Y-%m-%d", title="Date"),
            alt.Tooltip("norm_pace:N", title="Pace @%d bpm" % cap),
            alt.Tooltip("pace:N", title="Actual pace"),
            alt.Tooltip("hr_avg:Q", title="Avg HR"),
            alt.Tooltip("duration:N", title="Duration"),
        ],
    )

    # Target band (illustrative ~2:30–2:45 at the cap).
    target = (
        alt.Chart(pd.DataFrame({"y": [_TARGET_FAST_S], "y2": [_TARGET_SLOW_S]}))
        .mark_rect(color=ui.ACCENT_PR, opacity=0.08)
        .encode(y=alt.Y("y:Q"), y2=alt.Y2("y2:Q"))
    )

    line = base.mark_line(color=ui.ACCENT_SEL, strokeWidth=1.5)
    pts = base.mark_point(color=ui.ACCENT_SEL, filled=True, size=55)

    # Trend line (linear regression) to make the direction unmistakable.
    trend = base.transform_regression("date", "norm_pace_s").mark_line(
        color=ui.ACCENT_PR, strokeDash=[4, 3], strokeWidth=2
    )

    chart = (target + line + pts + trend).properties(height=320)
    st.altair_chart(ui.altair_theme(chart), width="stretch")


# ── Phase readiness (the "should I advance to Phase 2?" gate) ─────────────

@st.cache_data(show_spinner=False, ttl=3600)
def _easy_decoupling_pcts(uid: str, ids: tuple) -> list:
    """Decoupling % for each easy session id, via the per-stroke series.

    Cached by (uid, ids) so flipping tabs doesn't re-fetch strokes. Sessions
    without enough usable stroke data are simply skipped.
    """
    out = []
    for rid in ids:
        dec = decoupling(api.cached_strokes(uid, int(rid)))
        if dec:
            out.append(dec["pct"])
    return out


def _render_readiness(df: pd.DataFrame):
    sessions = recent_easy_steady(df, n=3)
    if not sessions:
        return  # no qualifying easy steady rows yet — stay quiet

    uid = str(st.session_state.get("user_id", "me"))
    pcts = _easy_decoupling_pcts(uid, tuple(s["id"] for s in sessions))
    r = readiness_from_decoupling(pcts)
    if r["status"] == "unknown":
        return  # easy sessions exist but none had usable stroke data

    ui.section_label("Phase readiness · easy-day decoupling")

    status = r["status"]
    med = r["median_pct"]
    color, verdict, detail = {
        "ready": (
            ui.ACCENT_PR, "Ready to advance",
            "Aerobic base is solid — if the 120-bpm split has dropped too, "
            "advance to Phase 2.",
        ),
        "developing": (
            ui.ACCENT_WARN, "Base developing",
            "Coupling is improving. Hold another 4-week Zone-2 block, then "
            "re-check.",
        ),
        "base": (
            ui.ACCENT_SEL, "Keep building base",
            "Easy-day HR still drifts up — stay in Phase 1 and keep building.",
        ),
    }[status]

    # Zoned gate: green (ready) → amber (developing) → blue (keep building).
    # Lower drift is better, so green sits on the left and the marker shows
    # where this block's median decoupling lands across the gate.
    ready_pct = READINESS_READY_PCT
    dev_pct = READINESS_DEVELOPING_PCT
    bar_min = min(0.0, med)
    bar_max = max(dev_pct * 1.8, med * 1.15, ready_pct * 3)
    bar = ui.threshold_bar_html(
        med, bar_max,
        bands=[(ready_pct, ui.ACCENT_PR),
               (dev_pct, ui.ACCENT_WARN),
               (bar_max, ui.ACCENT_SEL)],
        vmin=bar_min, marker_color=ui.INK_0,
    )
    sess = f"last {r['n']} easy session{'s' if r['n'] != 1 else ''}"

    st.html(
        f"<div style='padding:14px 16px;background:{ui.BG_1};"
        f"border:1px solid {ui.LINE};border-radius:10px;'>"
        # verdict + headline drift number
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline;'>"
        f"<span style='font-size:15px;font-weight:600;color:{color};'>{verdict}</span>"
        f"<span style='font-size:20px;font-weight:600;color:{color};"
        f"font-variant-numeric:tabular-nums;letter-spacing:-0.02em;'>"
        f"{med:+.1f}%<span style='font-size:11px;color:{ui.INK_2};"
        f"font-weight:400;'> drift</span></span></div>"
        # the gate bar
        f"{bar}"
        # zone labels under the bar
        f"<div style='display:flex;justify-content:space-between;"
        f"font-size:9px;letter-spacing:0.06em;text-transform:uppercase;'>"
        f"<span style='color:{ui.ACCENT_PR};'>ready &lt;{ready_pct:.0f}%</span>"
        f"<span style='color:{ui.ACCENT_WARN};'>developing</span>"
        f"<span style='color:{ui.ACCENT_SEL};'>building &gt;{dev_pct:.0f}%</span>"
        f"</div>"
        # one trimmed line of guidance + session count
        f"<div style='margin-top:9px;font-size:11.5px;color:{ui.INK_1};"
        f"line-height:1.45;'>{detail}</div>"
        f"<div style='margin-top:3px;font-size:10px;color:{ui.INK_3};'>{sess}</div>"
        f"</div>"
    )


# ── Weekly intensity distribution (the 80/20 pyramid check) ──────────────

def _render_zone_distribution(df):
    ui.section_label("Intensity distribution · last 12 weeks")

    zm = weekly_zone_minutes(df, days=84)
    if zm.empty:
        st.info("No heart-rate data yet — once sessions log HR, your weekly "
                "easy/hard split shows up here.")
        return

    ratio = easy_ratio(zm)
    pct = round(ratio * 100)
    on_target = pct >= 80
    color = ui.ACCENT_PR if on_target else ui.ACCENT_WARN
    status = "on target" if on_target else "below 80%"

    # Track: muted below the 80% target, green at/above it; the marker is this
    # period's actual easy share, so the goal is to push the marker into green.
    bar = ui.threshold_bar_html(
        pct, 100, bands=[(80, ui.BG_2), (100, ui.ACCENT_PR)],
        vmin=0, marker_color=ui.INK_0,
    )
    st.html(
        f"<div style='padding:14px 16px;background:{ui.BG_1};"
        f"border:1px solid {ui.LINE};border-radius:10px;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:baseline;'>"
        f"<span style='font-size:10px;color:{ui.INK_2};letter-spacing:0.08em;"
        f"text-transform:uppercase;font-weight:600;'>Easy · Zones 1–2</span>"
        f"<span style='font-size:24px;font-weight:600;color:{color};"
        f"font-variant-numeric:tabular-nums;letter-spacing:-0.02em;'>{pct}%"
        f"<span style='font-size:11px;color:{ui.INK_2};font-weight:400;'> {status}"
        f"</span></span></div>"
        f"{bar}"
        f"<div style='display:flex;justify-content:space-between;font-size:9px;"
        f"letter-spacing:0.06em;text-transform:uppercase;'>"
        f"<span style='color:{ui.INK_3};'>below 80%</span>"
        f"<span style='color:{ui.ACCENT_PR};'>80%+ target</span></div>"
        f"</div>"
    )
    st.caption(
        "Pyramidal plan: keep ~**80%** of weekly minutes easy. The stacked bars "
        "below split each week by HR zone — warm colours (Z4–5) are the hard days."
    )

    zm = zm.copy()
    zm["zone_label"] = zm["hr_zone"].apply(lambda z: f"Z{z} {zone_name(z)}")
    domain = [f"Z{z} {zone_name(z)}" for z in (1, 2, 3, 4, 5)]
    rng = [ui.zone_color(z) for z in (1, 2, 3, 4, 5)]

    chart = (
        alt.Chart(zm)
        .mark_bar()
        .encode(
            x=alt.X("week:T", axis=alt.Axis(format="%b %d", title=None,
                                             labelAngle=-30, tickCount="week")),
            y=alt.Y("minutes:Q", stack="normalize",
                     axis=alt.Axis(title=None, format="%")),
            color=alt.Color("zone_label:N",
                            scale=alt.Scale(domain=domain, range=rng),
                            legend=alt.Legend(title=None, symbolType="square")),
            order=alt.Order("hr_zone:Q", sort="ascending"),
            tooltip=[
                alt.Tooltip("week:T", format="%b %d", title="Week of"),
                alt.Tooltip("zone_label:N", title="Zone"),
                alt.Tooltip("minutes:Q", format=".0f", title="Minutes"),
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(ui.altair_theme(chart), width="stretch")


# ── Weekly plan adherence (Mon/Wed/Fri checklist) ────────────────────────

# Session-type accents for the adherence grid (E/I/S), matching overview's
# _TYPE_ACCENT: easy = green, intervals (the hard day) = amber, steady = blue.
# Tints are the same hues at low opacity for the "done" cell background.
_ADHERENCE_CELLS = [
    ("E", "easy_done",      ui.ACCENT_PR,  "rgba(126,201,122,0.16)"),
    ("I", "intervals_done", ui.ACCENT_WARN, "rgba(230,184,106,0.16)"),
    ("S", "steady_done",    ui.ACCENT_SEL, "rgba(106,163,230,0.16)"),
]


def _session_cell(letter, color, tint, done, expected=True):
    """One M/W/F slot: filled in the session's colour when done, hollow when
    missed, a dash when not expected (intervals on a recovery week)."""
    if not expected:
        bg, col, txt = "transparent", ui.INK_3, "–"
    elif done:
        bg, col, txt = tint, color, letter
    else:
        bg, col, txt = ui.BG_2, ui.INK_3, letter
    return (
        f"<span style='display:inline-flex;align-items:center;"
        f"justify-content:center;width:22px;height:22px;border-radius:6px;"
        f"font-size:10px;font-weight:700;background:{bg};color:{col};'>{txt}</span>"
    )


def _adherence_week_row(w, ctx, is_current=False):
    recovery = ctx.get("recovery", False)
    when = pd.Timestamp(w["week"]).strftime("%b %d")
    wk_badge = f"wk {ctx['week_in_block']}" if ctx else ""

    dots = "".join(
        # intervals aren't expected on a recovery week (Zone 1 only)
        _session_cell(letter, color, tint, w[flag],
                      expected=not (recovery and letter == "I"))
        for letter, flag, color, tint in _ADHERENCE_CELLS
    )

    if not w["sessions"]:
        right = f"<span style='font-size:10px;color:{ui.INK_3};'>no sessions</span>"
    else:
        easy_pct = round(w["easy_pct"])
        ecol = ui.ACCENT_PR if easy_pct >= 80 else ui.INK_2
        rtag = (f"<span style='font-size:9px;color:{ui.ACCENT_WARN};'>recovery · </span>"
                if recovery else "")
        right = (f"{rtag}<span style='font-size:11px;color:{ecol};"
                 f"font-variant-numeric:tabular-nums;'>{easy_pct}% easy</span>")

    rowbg = "rgba(230,184,106,0.06)" if recovery else "transparent"
    accent = (f"border-left:2px solid {ui.ACCENT_SEL};padding-left:6px;"
              if is_current else "border-left:2px solid transparent;padding-left:6px;")
    return (
        f"<div style='display:grid;grid-template-columns:1fr auto;gap:8px;"
        f"align-items:center;padding:7px 8px;border-radius:8px;background:{rowbg};"
        f"margin-bottom:4px;{accent}'>"
        f"<div style='display:flex;align-items:center;gap:8px;'>"
        f"<span style='font-size:10px;color:{ui.INK_3};width:30px;"
        f"font-variant-numeric:tabular-nums;'>{wk_badge}</span>"
        f"<span style='font-size:11px;color:{ui.INK_2};width:44px;"
        f"font-variant-numeric:tabular-nums;'>{when}</span>"
        f"<span style='display:inline-flex;gap:4px;'>{dots}</span></div>"
        f"{right}</div>"
    )


def _render_adherence(df):
    ui.section_label("Weekly plan adherence")

    weeks = weekly_plan(df, weeks=8)  # two full 4-week blocks
    if not weeks or all(w["sessions"] == 0 for w in weeks):
        st.caption("No recent sessions to check against the Mon/Wed/Fri plan.")
        return

    if config.PLAN_START_DATE is None:
        st.caption(
            "Set `PLAN_START_DATE` in secrets to see 4-week block and "
            "recovery-week markers."
        )

    current_week = weeks[0]["week"]            # weekly_plan is newest-first
    ordered = list(reversed(weeks))            # render oldest → newest

    legend = (
        f"<div style='display:flex;gap:14px;margin:2px 8px 10px;font-size:9.5px;"
        f"letter-spacing:0.04em;text-transform:uppercase;color:{ui.INK_3};'>"
        f"<span><b style='color:{ui.ACCENT_PR};'>E</b> easy</span>"
        f"<span><b style='color:{ui.ACCENT_WARN};'>I</b> intervals</span>"
        f"<span><b style='color:{ui.ACCENT_SEL};'>S</b> steady</span></div>"
    )

    parts, cur_block = [legend], object()  # sentinel so first block always emits
    for w in ordered:
        ctx = plan_week_label(w["week"], config.PLAN_START_DATE)
        block = ctx.get("block") if ctx else None
        if block != cur_block:
            cur_block = block
            if block is not None:
                parts.append(
                    f"<div style='font-size:10px;letter-spacing:0.12em;"
                    f"text-transform:uppercase;font-weight:600;color:{ui.INK_2};"
                    f"margin:14px 4px 6px;'>Block {block}</div>"
                )
        parts.append(_adherence_week_row(w, ctx, is_current=(w["week"] == current_week)))

    st.html("<div>" + "".join(parts) + "</div>")
