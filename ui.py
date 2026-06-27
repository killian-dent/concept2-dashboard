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
  threshold_bar_html(…)     — zoned track + marker (e.g. phase-readiness gate)
  sparkline_html(values)    — SVG sparkline as a base64 <img> data URI
"""
import base64

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

# HR-zone palette (1→5). Zone 2 is green — the aerobic-base zone the plan
# wants most volume in; 4/5 warm up toward red to read as "hard".
ZONE_COLORS = {
    1: "#6b8cae",   # recovery — muted blue
    2: "#7ec97a",   # aerobic base — green (the good zone)
    3: "#6aa3e6",   # aerobic/tempo — blue
    4: "#e6b86a",   # threshold — amber
    5: "#e07a5f",   # VO2max/max — terracotta red
}


def zone_color(zone: int) -> str:
    """Color for an HR zone number; falls back to a neutral hairline tone."""
    return ZONE_COLORS.get(zone, INK_3)


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

def render_header(user_id: str, on_change, on_refresh, is_placeholder: bool,
                  known_users: list = None):
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
        with st.popover(f"👤  {user_id}", width="stretch"):
            st.caption("Track a Concept2 user")
            new_id = None
            if known_users:
                _NEW = "＋ New user…"
                options = known_users + [_NEW]
                default = options.index(user_id) if user_id in options else 0
                selected = st.selectbox(
                    "Saved users", options, index=default,
                    label_visibility="collapsed",
                )
                if selected == _NEW:
                    typed = st.text_input(
                        "User ID", placeholder='Concept2 ID or "me"',
                        label_visibility="collapsed",
                    )
                    if typed:
                        new_id = typed
                elif selected != user_id:
                    new_id = selected
            else:
                typed = st.text_input(
                    "User ID", value=user_id, label_visibility="collapsed",
                    placeholder='Concept2 ID or "me"',
                )
                if typed and typed != user_id:
                    new_id = typed
            if new_id:
                on_change(new_id)
            st.caption('Numeric ID, or `"me"` for your own account.')
            st.divider()
            if st.button("🔄  Refresh data", width="stretch",
                         help="Check Concept2 for new workouts now"):
                on_refresh()


# ── Section label ─────────────────────────────────────────────────────────

def section_label(text: str, margin: str = "20px 4px 8px"):
    """Small uppercase section heading (the recurring label style)."""
    st.html(
        f"<div style='font-size:10px;color:{INK_2};letter-spacing:0.12em;"
        f"text-transform:uppercase;font-weight:600;margin:{margin};'>{text}</div>"
    )


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
    <div style="margin-top:6px;" role="img"
         aria-label="ranked top {top} percent of a field of {field}">
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
                   color: str = None, label: str = "trend",
                   fill: bool = False) -> str:
    """
    SVG sparkline, returned as a base64 ``<img>`` data URI.

    Streamlit's st.html/st.markdown sanitizer strips inline ``<svg>`` (it shows
    up fine in some older versions but is removed on current ones), so the SVG
    is encoded and embedded as an ``<img>``, which survives sanitization. Values
    are oldest→newest; < 2 values renders a blank spacer of the same size.

    When ``fill`` is True, a faint area is drawn under the line (the
    filled-gradient look from the hi-fi mockups). The stroke is inset by
    ~1px top/bottom so it isn't clipped at the edges.
    """
    color = color or ACCENT_SEL
    if not values or len(values) < 2:
        # keep the layout stable even with no data to plot
        return (f'<span style="display:inline-block;width:{width}px;'
                f'height:{height}px;"></span>')
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1
    step = width / (len(values) - 1)
    pad = 1.0  # keep the 1.4px stroke off the top/bottom edges
    plot_h = height - 2 * pad
    pts = " ".join(
        f"{i * step:.1f},{pad + (1 - (v - vmin) / span) * plot_h:.1f}"
        for i, v in enumerate(values)
    )
    area = ""
    if fill:
        area = (
            f'<polygon points="0,{height} {pts} {width:.1f},{height}" '
            f'fill="{color}" fill-opacity="0.14" stroke="none"/>'
        )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
        f'{area}'
        f'<polyline points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return (
        f'<img src="data:image/svg+xml;base64,{b64}" '
        f'width="{width}" height="{height}" alt="{label} sparkline" '
        f'style="display:inline-block;vertical-align:middle;"/>'
    )


def progress_bar_html(pct: float, color: str = None, height: int = 8) -> str:
    """Horizontal progress bar filled to pct (0–100), clamped."""
    pct = max(0.0, min(100.0, pct))
    color = color or ACCENT_SEL
    return (
        f'<div role="progressbar" aria-valuenow="{round(pct)}" aria-valuemin="0" '
        f'aria-valuemax="100" style="height:{height}px;background:{BG_2};'
        f'border-radius:99px;overflow:hidden;margin:8px 0 4px;">'
        f'<div style="width:{pct:.1f}%;height:100%;background:{color};'
        f'border-radius:99px;"></div></div>'
    )


def threshold_bar_html(value: float, vmax: float, bands: list,
                       vmin: float = 0.0, height: int = 10,
                       marker_color: str = None) -> str:
    """
    Horizontal track split into colored zones with a marker at ``value``.

    ``bands`` is an ascending list of ``(upper, color)``: each zone runs from
    the previous upper (or ``vmin``) to ``upper``. Use it to show where a value
    sits across labelled thresholds (e.g. the phase-readiness gate). Pure
    HTML/CSS — no SVG — so it renders through st.html's sanitizer unchanged.
    """
    span = (vmax - vmin) or 1
    marker_color = marker_color or INK_0
    segs, prev = [], vmin
    for upper, color in bands:
        upper = min(upper, vmax)
        w = max(0.0, (upper - prev) / span * 100)
        if w > 0:
            segs.append(f'<div style="width:{w:.2f}%;background:{color};"></div>')
        prev = upper
    pos = max(0.0, min(1.0, (value - vmin) / span)) * 100
    return (
        f'<div style="position:relative;margin:9px 0;">'
        f'<div style="display:flex;height:{height}px;border-radius:99px;'
        f'overflow:hidden;">{"".join(segs)}</div>'
        f'<div style="position:absolute;left:{pos:.2f}%;top:-3px;'
        f'height:{height + 6}px;width:2px;background:{marker_color};'
        f'transform:translateX(-1px);border-radius:2px;"></div></div>'
    )


# ── Altair chart theme ────────────────────────────────────────────────────

def altair_theme(chart):
    """Apply the dark palette to any Altair chart.

    Call as the last step before st.altair_chart():
        st.altair_chart(ui.altair_theme(chart), width="stretch")
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
