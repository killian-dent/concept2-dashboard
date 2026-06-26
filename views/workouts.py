"""
Workouts tab — list + drill-down detail.

Replaces section 2 (recent table + inline detail). Changes:
  • Filter bar at the top (search + last-N selector).
  • Workouts rendered as styled rows, not st.dataframe (mobile-friendly).
  • Detail view stays linked to the same session_state.selected_workout_id
    that existed in the original, so external code that reads it still works.
  • Hero stats reduced from 9 to 2 + 4; everything else is in the splits chart.
"""
import altair as alt
import pandas as pd
import streamlit as st

import api
import config
import ui
from data import format_duration, zone_name
from data_extras import time_in_zone_from_strokes, decoupling


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workout data available.")
        return

    # ── Filter row ───────────────────────────────────────────────────────
    cL, cR = st.columns([3, 2])
    with cL:
        q = st.text_input(
            "Search", placeholder="Filter by label · e.g. 5000m",
            label_visibility="collapsed",
        )
    with cR:
        n = st.selectbox(
            "Show", options=[20, 50, 100, 200], index=0,
            label_visibility="collapsed",
            format_func=lambda x: f"Last {x}",
        )

    work = df.head(n).copy()
    if q:
        work = work[work["label"].str.contains(q, case=False, na=False)]

    # ── Open-workout selector ────────────────────────────────────────────
    # Drives the inline detail panel below. We keep the same session-state
    # key (selected_workout_id) the old app used, but renamed for clarity.
    options = {
        f"{r['date'].strftime('%b %d')}  ·  {r['label']}  ·  "
        f"{r['duration']}  ·  {r['pace']}/500": int(r["id"])
        for _, r in work.iterrows()
    }
    label = st.selectbox(
        "Open workout",
        options=["—"] + list(options.keys()),
        label_visibility="collapsed",
    )
    if label != "—":
        st.session_state.selected_workout_id = options[label]

    st.caption(f"{len(work)} workouts")
    _render_list(work)

    # ── Detail panel ─────────────────────────────────────────────────────
    sid = st.session_state.get("selected_workout_id")
    if sid is not None and (df["id"] == sid).any():
        st.divider()
        _render_detail(df[df["id"] == sid].iloc[0])


# ── Helpers ──────────────────────────────────────────────────────────────

def _render_list(work: pd.DataFrame):
    rows = []
    for _, r in work.iterrows():
        rows.append(
            f"""
            <div style="display:grid;
                        grid-template-columns:54px 1fr 1fr auto;
                        gap:10px;align-items:center;padding:10px 4px;
                        border-bottom:1px solid {ui.LINE};font-size:12.5px;">
              <div style="color:{ui.INK_2};font-variant-numeric:tabular-nums;">
                {r['date'].strftime('%b %d')}
              </div>
              <div style="font-weight:500;">{r['label']}</div>
              <div style="font-variant-numeric:tabular-nums;">
                {int(r['distance_m']):,}m · {r['duration']}
              </div>
              <div style="font-variant-numeric:tabular-nums;color:{ui.INK_1};">
                {r['pace']}/500
              </div>
            </div>
            """
        )
    st.html("<div>" + "".join(rows) + "</div>")


def _render_detail(row: pd.Series):
    splits = row["splits"]
    has_rest = (row.get("rest_distance_m", 0) or 0) > 0

    # Hero stats: only TIME and PACE get top billing. Everything else is
    # secondary. Original code put 9 numbers up here; that's the clutter.
    c1, c2 = st.columns(2)
    c1.metric("Time", row["duration"])
    c2.metric("Avg /500m", row["pace"])

    drag = int(row["drag_factor"]) if row.get("drag_factor") else None
    cols = st.columns(5 if drag else 4)
    cols[0].metric("Distance", f"{int(row['distance_m']):,} m")
    cols[1].metric("Avg watts", f"{row['watts']:.0f} W")
    cols[2].metric("Avg SPM", int(row["spm"]) if row["spm"] else "—")
    cols[3].metric("Calories", f"{int(row['calories']):,}")
    if drag:
        cols[4].metric("Drag factor", drag)

    if has_rest:
        rest = int(row["rest_distance_m"])
        rt = format_duration(row["rest_time_s"])
        st.caption(
            f"ℹ️ Interval session — adds **{rest:,} m** of rest distance and "
            f"**{rt}** of rest time. Concept2 counts both toward lifetime "
            "totals; rankings use work figures only."
        )

    _render_hr_analysis(row)

    if splits:
        st.html(
            f"<div style='font-size:10px;color:{ui.INK_2};letter-spacing:0.12em;"
            f"text-transform:uppercase;font-weight:600;margin:18px 0 6px;'>"
            f"Splits</div>"
        )
        splits_df = pd.DataFrame([{
            "split": str(s["split_number"]),
            "pace":  s["pace"],
            "fmt":   s["pace_formatted"],
        } for s in splits])

        fastest = splits_df["pace"].min()
        y_min = fastest - 2
        y_max = splits_df["pace"].max() + 2
        splits_df["y_base"] = y_max  # bar anchor at chart bottom
        splits_df["type"] = splits_df["pace"].apply(
            lambda p: "fastest" if p == fastest else "normal"
        )

        pace_expr = (
            "floor(datum.value/60)+':'+"
            "(floor(datum.value%60)<10?'0'+floor(datum.value%60):''+floor(datum.value%60))"
        )
        y_enc = alt.Y(
            "pace:Q",
            scale=alt.Scale(domain=[y_min, y_max], reverse=True),
            axis=alt.Axis(labelExpr=pace_expr, title=None),
        )
        color_enc = alt.Color(
            "type:N",
            scale=alt.Scale(
                domain=["fastest", "normal"],
                range=[ui.ACCENT_PR, ui.ACCENT_SEL],
            ),
            legend=None,
        )
        x_enc = alt.X("split:O", axis=alt.Axis(title="Split", labelAngle=0))

        bars = (
            alt.Chart(splits_df)
            .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
            .encode(x=x_enc, y=y_enc, y2=alt.Y2("y_base:Q"), color=color_enc,
                    tooltip=[alt.Tooltip("split:O", title="Split"),
                              alt.Tooltip("fmt:N", title="Pace")])
        )
        labels = (
            alt.Chart(splits_df)
            .mark_text(baseline="bottom", dy=-3, fontSize=10)
            .encode(x=x_enc, y=y_enc, text="fmt:N", color=color_enc)
        )
        chart = (bars + labels).properties(height=280)
        st.altair_chart(ui.altair_theme(chart), use_container_width=True)


# ── Per-stroke HR analysis (trace · time-in-zone · decoupling) ───────────

def _render_hr_analysis(row: pd.Series):
    """HR-over-time trace, time-in-zone split, and aerobic-decoupling readout
    for the open workout, using the per-stroke series (fetched + cached)."""
    uid = st.session_state.get("user_id", "me")
    with st.spinner("Loading stroke data…"):
        strokes = api.cached_strokes(uid, int(row["id"]))

    # Easy-day cap verdict (uses session avg HR; works even without strokes).
    _render_cap_verdict(row)

    if not strokes:
        st.caption("No per-stroke data available for this workout.")
        return

    pts = pd.DataFrame(
        [{"t_min": s.get("t", 0) / 60.0, "hr": s.get("hr", 0)}
         for s in strokes if s.get("hr", 0) > 0]
    )
    if pts.empty:
        st.caption("No heart-rate samples recorded for this workout.")
        return

    ui.section_label("Heart rate", margin="18px 0 6px")

    # Faint zone bands behind the HR trace, clipped to the data's HR range.
    hr_lo, hr_hi = pts["hr"].min() - 4, pts["hr"].max() + 4
    bands = []
    for z, name, lo, hi in config.HR_ZONES:
        b_lo, b_hi = max(lo, hr_lo), min(hi, hr_hi)
        if b_hi > b_lo:
            bands.append({"lo": b_lo, "hi": b_hi, "zone": f"Z{z}",
                          "color": ui.zone_color(z)})
    band_df = pd.DataFrame(bands)

    band_layer = (
        alt.Chart(band_df).mark_rect(opacity=0.13).encode(
            y=alt.Y("lo:Q", scale=alt.Scale(domain=[hr_lo, hr_hi]),
                    axis=alt.Axis(title="bpm")),
            y2="hi:Q",
            color=alt.Color("color:N", scale=None, legend=None),
        )
    ) if not band_df.empty else None

    line = (
        alt.Chart(pts).mark_line(color=ui.INK_0, strokeWidth=1.4).encode(
            x=alt.X("t_min:Q", axis=alt.Axis(title="min")),
            y=alt.Y("hr:Q", scale=alt.Scale(domain=[hr_lo, hr_hi]),
                    axis=alt.Axis(title="bpm")),
            tooltip=[alt.Tooltip("t_min:Q", format=".1f", title="Min"),
                     alt.Tooltip("hr:Q", title="HR")],
        )
    )
    layers = [l for l in (band_layer, line) if l is not None]
    chart = alt.layer(*layers).properties(height=200)
    st.altair_chart(ui.altair_theme(chart), use_container_width=True)

    _render_time_in_zone(strokes)
    _render_decoupling(row, strokes)


def _render_cap_verdict(row: pd.Series):
    """For an easy aerobic steady piece, was the HR held under the plan cap?"""
    cap = config.EASY_HR_CAP
    hr = int(row.get("hr_avg", 0) or 0)
    steady = row.get("category", "SteadyState") != "Interval"
    long_enough = (row.get("time_s", 0) or 0) >= 15 * 60
    if not (hr and steady and long_enough and hr <= 126):
        return
    ok = hr <= cap
    color = ui.ACCENT_PR if ok else ui.ACCENT_WARN
    mark = "✓" if ok else "✗"
    verb = f"held under {cap}" if ok else f"over the {cap} cap"
    st.html(
        f"<div style='margin:10px 0;font-size:12.5px;color:{color};"
        f"font-weight:500;'>{mark} Easy-day check: avg HR {hr} — {verb} bpm.</div>"
    )


def _render_time_in_zone(strokes: list):
    tiz = time_in_zone_from_strokes(strokes)
    if not tiz:
        return
    total = sum(tiz.values()) or 1
    ui.section_label("Time in zone", margin="14px 0 6px")
    rows = []
    for z in (1, 2, 3, 4, 5):
        secs = tiz.get(z, 0)
        if secs <= 0:
            continue
        pct = secs / total * 100
        mins = secs / 60.0
        rows.append({"zone": f"Z{z} {zone_name(z)}", "minutes": mins,
                     "pct": pct, "color": ui.zone_color(z)})
    if not rows:
        return
    tz_df = pd.DataFrame(rows)
    chart = (
        alt.Chart(tz_df).mark_bar().encode(
            x=alt.X("minutes:Q", axis=alt.Axis(title="min")),
            y=alt.Y("zone:N", sort=[r["zone"] for r in rows],
                    axis=alt.Axis(title=None)),
            color=alt.Color("color:N", scale=None, legend=None),
            tooltip=[alt.Tooltip("zone:N", title="Zone"),
                     alt.Tooltip("minutes:Q", format=".1f", title="Minutes"),
                     alt.Tooltip("pct:Q", format=".0f", title="%")],
        ).properties(height=24 * len(rows) + 20)
    )
    st.altair_chart(ui.altair_theme(chart), use_container_width=True)


def _render_decoupling(row: pd.Series, strokes: list):
    """Cardiac-drift readout for steady pieces (skip interval sessions)."""
    if row.get("category", "SteadyState") == "Interval":
        return
    dec = decoupling(strokes)
    if not dec:
        return
    pct = dec["pct"]
    good = pct < 5.0
    color = ui.ACCENT_PR if good else ui.ACCENT_WARN
    note = ("aerobically sound — pace held without HR drifting up"
            if good else "HR drifted up in the back half (cardiac drift)")
    st.html(
        f"<div style='margin:12px 0 2px;font-size:12.5px;'>"
        f"<span style='color:{ui.INK_2};'>Aerobic decoupling</span> "
        f"<span style='color:{color};font-weight:600;font-variant-numeric:"
        f"tabular-nums;'>{pct:+.1f}%</span></div>"
        f"<div style='font-size:11px;color:{ui.INK_3};'>{note} · "
        f"&lt;5% is well-coupled</div>"
    )
