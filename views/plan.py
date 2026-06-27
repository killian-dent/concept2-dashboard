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

    # ── KPIs: current normalised pace + trend since the start ────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("Easy pace @ %d bpm" % cap, s["latest_norm_pace"])
    improved = s["improved_s"]
    if abs(improved) >= 0.1:
        faster = improved > 0
        c2.metric(
            "Trend",
            f"{'−' if faster else '+'}{abs(improved):.1f}s",
            delta=("faster" if faster else "slower"),
            delta_color="normal" if faster else "inverse",
        )
    else:
        c2.metric("Trend", "flat")
    c3.metric("Easy sessions", s["count"])

    st.caption(
        "Each point is an easy Zone-2 row; pace is normalised to "
        f"{cap} bpm so sessions at slightly different heart rates compare "
        "fairly. **Lower is better** — the goal is this line drifting down "
        "(toward ~2:30) over the coming weeks."
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

    c1, c2 = st.columns([1, 2])
    c1.metric("Easy (Z1–2)", f"{pct}%",
              delta=("on target" if on_target else "below 80%"),
              delta_color="normal" if on_target else "inverse")
    with c2:
        st.caption(
            "The plan is **pyramidal**: keep ~**80%** of weekly minutes easy "
            "(Zones 1–2, green/blue). Bars classify each session by its average "
            "heart rate. If the warm colours (Z4–5) dominate, the easy days "
            "aren't easy enough."
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

def _render_adherence(df):
    ui.section_label("Weekly plan adherence")

    weeks = weekly_plan(df, weeks=6)
    if not weeks or all(w["sessions"] == 0 for w in weeks):
        st.caption("No recent sessions to check against the Mon/Wed/Fri plan.")
        return

    if config.PLAN_START_DATE is None:
        st.caption(
            "Easy · Intervals · Steady — the week's three target sessions. "
            "Set `PLAN_START_DATE` in secrets to also see 4-week cycle and "
            "recovery-week markers."
        )

    for w in weeks:
        _render_week_row(w)


def _render_week_row(w):
    ctx = plan_week_label(w["week"], config.PLAN_START_DATE)
    recovery = ctx.get("recovery", False)

    when = pd.Timestamp(w["week"]).strftime("%b %d")
    block_txt = ""
    if ctx:
        block_txt = (f" · Block {ctx['block']} wk {ctx['week_in_block']}"
                     + (" · recovery" if recovery else ""))

    def chip(label, done, expected=True):
        if not expected:
            bg, col, txt = ui.BG_2, ui.INK_3, f"{label} n/a"
        elif done:
            bg, col, txt = "rgba(126,201,122,0.15)", ui.ACCENT_PR, f"✓ {label}"
        else:
            bg, col, txt = ui.BG_2, ui.INK_3, f"○ {label}"
        return (f"<span style='display:inline-block;padding:2px 8px;"
                f"border-radius:99px;background:{bg};color:{col};"
                f"font-size:10.5px;font-weight:500;margin-right:4px;'>{txt}</span>")

    # Intervals aren't expected on a recovery week (Zone 1 only).
    chips = (
        chip("Easy", w["easy_done"])
        + chip("Intervals", w["intervals_done"], expected=not recovery)
        + chip("Steady", w["steady_done"])
    )

    easy_pct = round(w["easy_pct"])
    easy_col = ui.ACCENT_PR if easy_pct >= 80 else ui.INK_2
    easy_badge = (
        f"<span style='font-size:10.5px;color:{easy_col};"
        f"font-variant-numeric:tabular-nums;'>{easy_pct}% easy</span>"
        if w["sessions"] else ""
    )

    detail = " · ".join(
        f"{it['day']} {it['label']}" for it in w["items"]
    ) or "no sessions"

    st.html(f"""
    <div style="padding:10px 4px;border-bottom:1px solid {ui.LINE};">
      <div style="display:flex;justify-content:space-between;align-items:baseline;">
        <div style="font-size:11px;color:{ui.INK_2};font-weight:600;
                    letter-spacing:0.04em;">Week of {when}{block_txt}</div>
        {easy_badge}
      </div>
      <div style="margin-top:6px;">{chips}</div>
      <div style="margin-top:5px;font-size:10.5px;color:{ui.INK_3};">{detail}</div>
    </div>
    """)
