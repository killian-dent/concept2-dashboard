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
import altair as alt
import pandas as pd
import streamlit as st

import ui


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workouts to compare.")
        return

    st.caption(
        "Pick two workouts of the same event to overlay their splits "
        "and see exactly where the difference came from."
    )

    # ── Event picker — only show events with ≥2 attempts ─────────────────
    counts = df["label"].value_counts()
    eligible = counts[counts >= 2].index.tolist()
    if not eligible:
        st.info("Need at least two workouts of the same type to compare.")
        return

    event = st.selectbox("Event", eligible, index=0)
    sub = (df[df["label"] == event]
              .sort_values("date", ascending=False)
              .head(50)                      # cap to keep selectboxes fast
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

    if st.button("Compare", type="primary"):
        st.session_state["compare_ready"] = (event, idx_a, idx_b)

    ready = st.session_state.get("compare_ready")
    if not ready or ready != (event, idx_a, idx_b):
        st.caption("Select two workouts and click Compare.")
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

    label_a = f"A · {a['date'].strftime('%b %d')}"
    label_b = f"B · {b['date'].strftime('%b %d')}"

    rows = (
        [{"split": s["split_number"], "pace": s["pace"],
          "fmt": s["pace_formatted"], "workout": label_a} for s in sa]
        + [{"split": s["split_number"], "pace": s["pace"],
            "fmt": s["pace_formatted"], "workout": label_b} for s in sb]
    )
    df = pd.DataFrame(rows)

    all_paces = df["pace"].tolist()
    y_min = min(all_paces) - 2
    y_max = max(all_paces) + 2

    pace_expr = (
        "floor(datum.value/60)+':'+"
        "(floor(datum.value%60)<10?'0'+floor(datum.value%60):''+floor(datum.value%60))"
    )

    base = alt.Chart(df).encode(
        x=alt.X("split:O", axis=alt.Axis(title="Split", labelAngle=0)),
        y=alt.Y(
            "pace:Q",
            scale=alt.Scale(domain=[y_min, y_max], reverse=True),
            axis=alt.Axis(labelExpr=pace_expr, title=None),
        ),
        color=alt.Color(
            "workout:N",
            scale=alt.Scale(
                domain=[label_a, label_b],
                range=[ui.ACCENT_SEL, ui.ACCENT_WARN],
            ),
            legend=alt.Legend(title=None),
        ),
        tooltip=[
            alt.Tooltip("split:O", title="Split"),
            alt.Tooltip("workout:N", title="Workout"),
            alt.Tooltip("fmt:N", title="Pace"),
        ],
    )

    chart = (
        base.mark_line(strokeWidth=2)
        + base.mark_point(filled=True, size=50)
    ).properties(height=290)

    st.altair_chart(ui.altair_theme(chart), use_container_width=True)


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
