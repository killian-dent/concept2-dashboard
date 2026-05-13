"""
Compare tab — pick two workouts of the same event, see overlaid splits + deltas.

This is the FLAGSHIP NEW FEATURE of the Direction-B refactor. It leans hard
into Streamlit's strengths (two selectboxes + a Plotly overlay) and gives
you something most rowing apps don't have.

The UX is:
  1. Pick an event (filtered to ones you've done ≥2 times)
  2. Pick A and B (defaults to most-recent and previous)
  3. Look at the delta band, the overlaid pace curves, and the per-split
     table to see exactly where the difference came from.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from data import format_pace


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workouts to compare.")
        return

    st.caption(
        "Pick two workouts of the same event to overlay their splits "
        "and see exactly where the difference came from."
    )

    # ── Event picker — only show events with ≥2 attempts ─────────────────
    event_counts = df["label"].value_counts()
    eligible = event_counts[event_counts >= 2].index.tolist()
    if not eligible:
        st.info("Need at least two workouts of the same type to compare.")
        return

    event = st.selectbox("Event", eligible, index=0)
    sub = (df[df["label"] == event]
              .sort_values("date", ascending=False)
              .reset_index(drop=True))

    # Two pickers, side by side. Default to most-recent and previous.
    cA, cB = st.columns(2)
    with cA:
        idx_a = st.selectbox(
            "A", range(len(sub)), index=0,
            format_func=lambda i: (
                f"{sub.iloc[i]['date'].strftime('%b %d, %Y')} · "
                f"{sub.iloc[i]['pace']}/500"
            ),
        )
    with cB:
        default_b = 1 if len(sub) > 1 else 0
        idx_b = st.selectbox(
            "B", range(len(sub)), index=default_b,
            format_func=lambda i: (
                f"{sub.iloc[i]['date'].strftime('%b %d, %Y')} · "
                f"{sub.iloc[i]['pace']}/500"
            ),
        )

    if idx_a == idx_b:
        st.warning("Pick two different workouts to compare.")
        return

    a, b = sub.iloc[idx_a], sub.iloc[idx_b]

    _render_deltas(a, b)
    _render_overlay(a, b)
    _render_split_table(a, b)


# ── Sub-renderers ────────────────────────────────────────────────────────

def _render_deltas(a, b):
    """4-cell aggregate delta band. Color = "is A better than B?"
    For time/pace, smaller is better; for watts/SPM, bigger is better."""
    def cell(label, val, lower_is_better=True):
        sign = "+" if val > 0 else ("−" if val < 0 else "·")
        good = (val <= 0) if lower_is_better else (val >= 0)
        if val == 0:
            color = ui.INK_2
        else:
            color = ui.ACCENT_PR if good else ui.ACCENT_WARN
        return (
            f"<div>"
            f"<div style='font-size:9px;color:{ui.INK_2};"
            f"letter-spacing:0.08em;text-transform:uppercase;'>{label}</div>"
            f"<div style='font-size:15px;font-weight:600;color:{color};"
            f"font-variant-numeric:tabular-nums;margin-top:3px;'>"
            f"{sign}{abs(val):.1f}</div></div>"
        )

    dt = a["time_s"]  - b["time_s"]
    dp = a["pace_s"]  - b["pace_s"]
    dw = a["watts"]   - b["watts"]
    ds = (a["spm"] or 0) - (b["spm"] or 0)

    cells = [
        cell("Δ time (s)", dt),
        cell("Δ pace (s)", dp),
        cell("Δ avg W", dw, lower_is_better=False),
        cell("Δ SPM",   ds, lower_is_better=False),
    ]
    st.html(
        f"<div style='display:grid;grid-template-columns:repeat(4,1fr);"
        f"gap:10px;padding:12px 14px;background:{ui.BG_1};"
        f"border:1px solid {ui.LINE};border-radius:8px;margin:14px 0;'>"
        f"{''.join(cells)}</div>"
    )


def _render_overlay(a, b):
    """Two overlaid pace lines + per-split dots. Y-axis reversed so lower
    (=faster) is up."""
    sa, sb = a["splits"], b["splits"]
    if not sa or not sb:
        st.caption("One of these workouts has no split data.")
        return

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=[s["split_number"] for s in sa],
        y=[s["pace"] for s in sa],
        mode="lines+markers",
        name=f"A · {a['date'].strftime('%b %d')}",
        line=dict(color=ui.ACCENT_SEL, width=2),
        marker=dict(size=6, color=ui.ACCENT_SEL),
    ))
    fig.add_trace(go.Scatter(
        x=[s["split_number"] for s in sb],
        y=[s["pace"] for s in sb],
        mode="lines+markers",
        name=f"B · {b['date'].strftime('%b %d')}",
        line=dict(color=ui.ACCENT_WARN, width=2),
        marker=dict(size=6, color=ui.ACCENT_WARN),
    ))
    all_paces = [s["pace"] for s in sa] + [s["pace"] for s in sb]
    y_min, y_max = min(all_paces) - 2, max(all_paces) + 2
    fig.update_yaxes(
        range=[y_max, y_min],
        tickvals=list(range(int(y_min), int(y_max) + 1, 2)),
        ticktext=[format_pace(v) for v in range(int(y_min), int(y_max) + 1, 2)],
        gridcolor=ui.LINE, color=ui.INK_2,
    )
    fig.update_xaxes(
        showgrid=False, color=ui.INK_2,
        title=dict(text="Split", font=dict(size=10, color=ui.INK_2)),
    )
    fig.update_layout(
        height=290, margin=dict(t=10, l=6, r=6, b=6),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(size=11, color=ui.INK_1)),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False})


def _render_split_table(a, b):
    """Per-split A/B comparison with delta column."""
    sa, sb = a["splits"], b["splits"]
    if not sa or not sb:
        return
    n = min(len(sa), len(sb))

    header = (
        f"<div style='display:grid;grid-template-columns:30px 1fr 1fr 60px;"
        f"gap:6px;padding:6px 8px;font-size:9px;color:{ui.INK_2};"
        f"letter-spacing:0.08em;text-transform:uppercase;"
        f"border-bottom:1px solid {ui.LINE};'>"
        f"<div>#</div><div>A</div><div>B</div>"
        f"<div style='text-align:right;'>Δ</div></div>"
    )

    rows = []
    for i in range(n):
        pa, pb = sa[i]["pace"], sb[i]["pace"]
        d = pa - pb
        if d == 0:
            color, sign = ui.INK_2, "·"
        elif d < 0:
            color, sign = ui.ACCENT_PR, "−"
        else:
            color, sign = ui.ACCENT_WARN, "+"
        rows.append(
            f"<div style='display:grid;grid-template-columns:30px 1fr 1fr 60px;"
            f"gap:6px;padding:6px 8px;border-bottom:1px solid {ui.LINE};"
            f"font-size:12px;font-variant-numeric:tabular-nums;'>"
            f"<div style='color:{ui.INK_2};'>{i + 1}</div>"
            f"<div>{sa[i]['pace_formatted']}</div>"
            f"<div>{sb[i]['pace_formatted']}</div>"
            f"<div style='text-align:right;color:{color};font-weight:500;'>"
            f"{sign}{abs(d):.1f}s</div></div>"
        )

    st.html(f"<div style='margin-top:18px;'>{header}{''.join(rows)}</div>")
