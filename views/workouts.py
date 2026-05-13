"""
Workouts tab — list + drill-down detail.

Replaces section 2 (recent table + inline detail). Changes:
  • Filter bar at the top (search + last-N selector).
  • Workouts rendered as styled rows, not st.dataframe (mobile-friendly).
  • Detail view stays linked to the same session_state.selected_workout_id
    that existed in the original, so external code that reads it still works.
  • Hero stats reduced from 9 to 2 + 4; everything else is in the splits chart.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from data import format_pace, format_duration


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
        pace_vals = [s["pace"] for s in splits]
        y_min, y_max = min(pace_vals) - 2, max(pace_vals) + 2
        fastest_idx = pace_vals.index(min(pace_vals))
        colors = [
            ui.ACCENT_PR if i == fastest_idx else ui.ACCENT_SEL
            for i in range(len(splits))
        ]
        fig = go.Figure(go.Bar(
            x=[str(s["split_number"]) for s in splits],
            y=pace_vals,
            marker_color=colors,
            text=[s["pace_formatted"] for s in splits],
            textposition="outside",
            textfont=dict(size=10, color=ui.INK_1),
        ))
        fig.update_yaxes(
            range=[y_max, y_min],
            tickvals=list(range(int(y_min), int(y_max) + 1, 2)),
            ticktext=[format_pace(v) for v in range(int(y_min), int(y_max) + 1, 2)],
            gridcolor=ui.LINE, color=ui.INK_2,
        )
        fig.update_xaxes(showgrid=False, color=ui.INK_2,
                         title=dict(text="Split", font=dict(size=10, color=ui.INK_2)))
        fig.update_layout(
            height=280, margin=dict(t=20, l=6, r=6, b=6),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})
