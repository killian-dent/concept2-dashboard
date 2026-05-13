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

import ui
from data import format_duration


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

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Distance", f"{int(row['distance_m']):,} m")
    s2.metric("Avg watts", f"{row['watts']:.0f} W")
    s3.metric("Avg SPM", int(row["spm"]) if row["spm"] else "—")
    s4.metric("Calories", f"{int(row['calories']):,}")

    if has_rest:
        rest = int(row["rest_distance_m"])
        rt = format_duration(row["rest_time_s"])
        st.caption(
            f"ℹ️ Interval session — adds **{rest:,} m** of rest distance and "
            f"**{rt}** of rest time. Concept2 counts both toward lifetime "
            "totals; rankings use work figures only."
        )

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
