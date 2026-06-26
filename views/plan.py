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

import config
import ui
from data import format_pace
from data_extras import aerobic_efficiency, aerobic_efficiency_summary

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
    st.altair_chart(ui.altair_theme(chart), use_container_width=True)
