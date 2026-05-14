"""
Shared UI atoms for the Direction-B refactor.

Conventions:
  • All HTML helpers return strings; render with st.html(...).
  • Color tokens live here. Tweak in one place to re-theme the whole app.
  • No global state.

Public API:
  inject_styles()           — call once near top of app.py
  render_header(...)        — title + user-ID popover + refresh
  kpi_cell(label, value, …) — one tile of the Overview KPI quadrant
  kpi_grid(cells)           — wrap 4 cells in a 2×2 grid (HTML)
  percentile_bar_html(…)    — used by WOD list rows
  sparkline_html(values)    — inline SVG sparkline
"""
import streamlit as st


# ── Color tokens — Direction B palette ───────────────────────────────────
# These match the warm-neutral dark theme in the mockups. Edit here to
# re-theme everything (e.g. switch ACCENT_SEL to a different brand blue).
INK_0  = "#f1efe9"          # primary text
INK_1  = "#c8c2b5"          # secondary text
INK_2  = "#8c8678"          # tertiary / labels
INK_3  = "#5f5a4f"          # placeholder / disabled

BG_0   = "#1c1b18"          # page background
BG_1   = "#232220"          # card background
BG_2   = "#2c2a26"          # raised / chip
LINE   = "#3a3733"          # hairline

ACCENT_PR   = "#7ec97a"     # green — PRs, improvements
ACCENT_SEL  = "#6aa3e6"     # blue — selection, primary action
ACCENT_WARN = "#e6b86a"     # amber — attention, regressions


def inject_styles():
    """One-time CSS injection. Tightens padding and re-skins Streamlit
    primitives (metric, tabs, container) to match the mockup density."""
    st.markdown(
        f"""
        <style>
          .block-container {{
            padding-top: 1.2rem; padding-bottom: 4rem; max-width: 1100px;
          }}
          @media (max-width: 640px) {{
            .block-container {{ padding-left: 0.75rem; padding-right: 0.75rem; }}
          }}

          /* Hide Streamlit's default top bar — we render our own header */
          header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
          [data-testid="stToolbar"] {{ display: none; }}

          /* Tabs: tighter, segmented look */
          .stTabs [data-baseweb="tab-list"] {{
            gap: 4px; border-bottom: 1px solid {LINE};
          }}
          .stTabs [data-baseweb="tab"] {{
            padding: 8px 4px; font-size: 13px; font-weight: 500;
          }}
          .stTabs [aria-selected="true"] {{ color: {INK_0} !important; }}

          /* st.metric: smaller value, mono numerals, uppercase label */
          [data-testid="stMetricValue"] {{
            font-feature-settings: "tnum"; font-variant-numeric: tabular-nums;
            font-size: 22px; letter-spacing: -0.02em; color: {INK_0};
          }}
          [data-testid="stMetricLabel"] {{
            font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
            color: {INK_2}; font-weight: 500;
          }}
          [data-testid="stMetricDelta"] {{ font-size: 11px; }}

          /* Bordered container: matches card style */
          [data-testid="stVerticalBlockBorderWrapper"] {{
            background: {BG_1}; border-color: {LINE}; border-radius: 10px;
          }}

          /* Compact form controls */
          .stSelectbox, .stTextInput {{ font-size: 13px; }}
          .stButton button {{ font-size: 13px; padding: 4px 12px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ── Header with user-ID popover ──────────────────────────────────────────

def render_header(user_id: str, on_change, on_refresh, is_placeholder: bool):
    """
    Page header: brand on the left, sync status + user popover on the right.
    Replaces the old sidebar entirely.
    """
    cL, cR = st.columns([5, 1.4])
    with cL:
        kicker = "⚠ Sample data" if is_placeholder else "Synced"
        st.markdown(
            f"<div style='font-size:11px;color:{INK_2};letter-spacing:0.12em;"
            f"text-transform:uppercase;font-weight:600;'>{kicker}</div>"
            f"<div style='font-size:28px;font-weight:600;letter-spacing:-0.02em;"
            f"margin-top:2px;color:{INK_0};'>Concept2</div>",
            unsafe_allow_html=True,
        )
    with cR:
        # The popover button label includes the active user so you can tell
        # at a glance who you're viewing.
        with st.popover(f"👤  {user_id}", use_container_width=True):
            st.caption("Track a Concept2 user")
            new_id = st.text_input(
                "User ID",
                value=user_id,
                label_visibility="collapsed",
                placeholder='Concept2 ID or "me"',
            )
            if new_id and new_id != user_id:
                on_change(new_id)
            st.caption('Numeric ID, or `"me"` for your own account.')
            st.divider()
            if st.button("🔄  Refresh data", use_container_width=True,
                         help="Check Concept2 for new workouts now"):
                on_refresh()


# ── KPI cell + grid ──────────────────────────────────────────────────────

def kpi_cell(label: str, value: str, delta: str = "", up: bool = True,
             spark: list = None, color: str = None) -> str:
    """
    One tile of the KPI quadrant. Returns an HTML string so we can lay 4 of
    them out in a 2×2 grid that holds shape on narrow phones — Streamlit's
    `st.columns` row-stacks at <640px, which we don't want here.
    """
    delta_color = ACCENT_PR if up else ACCENT_WARN
    spark_html = (
        sparkline_html(spark, width=66, height=16, color=color or ACCENT_SEL)
        if spark else ""
    )
    delta_html = (
        f"<span style='color:{delta_color};font-weight:500;"
        f"font-variant-numeric:tabular-nums;font-size:11px;'>{delta}</span>"
        if delta else ""
    )
    return f"""
    <div style="padding:12px 14px;background:{BG_1};border:1px solid {LINE};
                border-radius:8px;">
      <div style="font-size:10px;color:{INK_2};letter-spacing:0.08em;
                  text-transform:uppercase;font-weight:500;">{label}</div>
      <div style="font-size:22px;font-weight:600;letter-spacing:-0.02em;
                  margin-top:4px;font-variant-numeric:tabular-nums;
                  color:{INK_0};">{value}</div>
      <div style="display:flex;justify-content:space-between;
                  align-items:center;margin-top:6px;">
        {delta_html}<div>{spark_html}</div>
      </div>
    </div>
    """


def kpi_grid(cells: list) -> str:
    """Wrap 4 kpi_cell strings in a 2×2 CSS grid."""
    return (
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
        f'{"".join(cells)}</div>'
    )


# ── Percentile bar (WOD) ─────────────────────────────────────────────────

def percentile_bar_html(rank: int, field: int, height: int = 6) -> str:
    """
    Horizontal bar with a pin at rank/field. Tick at 50% so you can read
    'better' or 'worse' than median at a glance. Lower rank = better.
    """
    pct = max(0.0, min(100.0, 100 * rank / max(field, 1)))
    top = round(100 - pct)
    good = top >= 50
    color = ACCENT_PR if good else ACCENT_WARN
    return f"""
    <div style="margin-top:6px;">
      <div style="position:relative;height:{height}px;background:{BG_2};
                  border-radius:99px;">
        <div style="position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;
                    background:{LINE};"></div>
        <div style="position:absolute;left:{pct}%;top:-{(height // 2) + 1}px;
                    width:{height + 6}px;height:{height + 6}px;
                    border-radius:99px;background:{color};
                    border:2px solid {BG_0};transform:translateX(-50%);"></div>
      </div>
      <div style="margin-top:6px;font-size:11px;color:{color};font-weight:500;
                  font-variant-numeric:tabular-nums;">
        Top {top}% <span style="color:{INK_3};font-weight:400;">· field {field:,}</span>
      </div>
    </div>
    """


# ── Sparkline ────────────────────────────────────────────────────────────

def sparkline_html(values: list, width: int = 64, height: int = 18,
                   color: str = None) -> str:
    """
    Simple SVG sparkline. Values are oldest→newest. < 2 values renders empty.
    """
    color = color or ACCENT_SEL
    if not values or len(values) < 2:
        return f'<svg width="{width}" height="{height}"></svg>'
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1
    step = width / (len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - (v - vmin) / span * height:.1f}"
        for i, v in enumerate(values)
    )
    return (
        f'<svg width="{width}" height="{height}" '
        f'style="display:inline-block;vertical-align:middle;">'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


# ── Altair chart theme ────────────────────────────────────────────────────

def altair_theme(chart):
    """Apply the dark palette to any Altair chart.

    Call as the last step before st.altair_chart():
        st.altair_chart(ui.altair_theme(chart), use_container_width=True)
    """
    return (
        chart
        .configure_axis(
            gridColor=LINE,
            domainColor=LINE,
            tickColor=LINE,
            labelColor=INK_2,
            titleColor=INK_2,
            labelFontSize=11,
        )
        .configure_view(strokeWidth=0)
        .configure_legend(
            labelColor=INK_1,
            titleColor=INK_2,
            orient="top",
            labelFontSize=11,
        )
    )
