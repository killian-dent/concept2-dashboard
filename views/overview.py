"""
Overview tab — the new home screen.

Replaces the original section 1 (6-metric strip) and reframes it as:
  1. A 2×2 KPI quadrant — last 30 days, with delta vs previous 30 + sparkline.
  2. A calendar heatmap of activity — last 12 weeks, dow × week.
  3. A compact list of the most recent workouts.

Lifetime totals (which used to occupy half of section 1) move into the Records
tab, where they belong; the home screen is for "how am I doing right now?"
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

import ui
from data import format_pace, format_duration
from data_extras import compute_period_kpis, daily_meters


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workout data available.")
        return

    # ── KPI quadrant ─────────────────────────────────────────────────────
    k = compute_period_kpis(df, days=30)
    spark = daily_meters(df, days=30)["meters"].tolist()

    def fmt_h(seconds):
        # H:MM, no seconds — KPI cell has no room for them
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}:{m:02d}"

    cells = [
        ui.kpi_cell(
            label="Total meters · 30d",
            value=f"{k['meters'] / 1000:,.1f}k",
            delta=(("+" if k["meters_delta"] >= 0 else "−") +
                   f"{abs(k['meters_delta']) / 1000:,.1f}k"),
            up=k["meters_delta"] >= 0,
            spark=spark,
            color=ui.ACCENT_SEL,
        ),
        ui.kpi_cell(
            label="Avg /500m",
            value=format_pace(k["avg_pace_s"]) if k["avg_pace_s"] else "—",
            delta=(("+" if k["avg_pace_delta"] >= 0 else "−") +
                   f"{abs(k['avg_pace_delta']):.1f}s"),
            up=k["avg_pace_delta"] >= 0,  # sign already flipped in compute_period_kpis
        ),
        ui.kpi_cell(
            label="Sessions · 30d",
            value=str(k["sessions"]),
            delta=(("+" if k["sessions_delta"] >= 0 else "") +
                   str(k["sessions_delta"])),
            up=k["sessions_delta"] >= 0,
        ),
        ui.kpi_cell(
            label="Time on erg · 30d",
            value=fmt_h(k["time_s"]),
            delta=(("+" if k["time_s_delta"] >= 0 else "−") +
                   fmt_h(abs(k["time_s_delta"]))),
            up=k["time_s_delta"] >= 0,
        ),
    ]
    st.html(ui.kpi_grid(cells))

    # ── Heatmap ──────────────────────────────────────────────────────────
    _section_label("Activity · last 12 weeks")
    _render_heatmap(df)

    # ── Recent ───────────────────────────────────────────────────────────
    _section_label("Recent", trailing_link="View all in Workouts →", tab_name="Workouts")
    _render_recent(df.head(6))


# ── Helpers ──────────────────────────────────────────────────────────────

def _section_label(text: str, trailing_link: str = None, tab_name: str = None):
    """Small uppercase section heading with optional right-aligned link.

    When tab_name is provided the link uses window.parent JS to click the
    matching Streamlit tab button — the only way to switch tabs programmatically.
    Uses components.html (which allows JS) instead of st.html (which doesn't).
    """
    if trailing_link and tab_name:
        js = (
            f"var tabs=window.parent.document.querySelectorAll('[data-baseweb=\"tab\"]');"
            f"for(var i=0;i<tabs.length;i++){{"
            f"if(tabs[i].innerText.trim()==='{tab_name}'){{tabs[i].click();break;}}}}"
        )
        components.html(
            f"""
            <style>
              body {{ margin: 0; padding: 0; background: transparent; }}
            </style>
            <div style="display:flex;justify-content:space-between;align-items:baseline;
                        padding:20px 4px 8px;
                        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
              <div style="font-size:10px;color:{ui.INK_2};letter-spacing:0.12em;
                          text-transform:uppercase;font-weight:600;">{text}</div>
              <span onclick="{js}"
                    style="font-size:11px;color:{ui.ACCENT_SEL};cursor:pointer;">
                {trailing_link}
              </span>
            </div>
            """,
            height=48,
        )
    else:
        st.html(
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:baseline;margin:20px 4px 8px;'>"
            f"<div style='font-size:10px;color:{ui.INK_2};letter-spacing:0.12em;"
            f"text-transform:uppercase;font-weight:600;'>{text}</div>"
            f"</div>"
        )


def _render_heatmap(df: pd.DataFrame):
    daily = daily_meters(df, days=84)
    pivot = (daily.pivot_table(index="dow", columns="week",
                               values="meters", aggfunc="sum")
                  .reindex(range(7)).fillna(0))

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[f"W{i}" for i in pivot.columns],
        y=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        colorscale=[[0, ui.BG_2], [1, ui.ACCENT_SEL]],
        showscale=False,
        hovertemplate="%{y} · %{z:,.0f}m<extra></extra>",
        xgap=2, ygap=2,
    ))
    fig.update_layout(
        height=200,
        margin=dict(t=4, b=4, l=4, r=4),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=ui.INK_2, size=10),
        xaxis=dict(showgrid=False, zeroline=False, tickfont=dict(size=9)),
        yaxis=dict(showgrid=False, zeroline=False, autorange="reversed"),
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False})


def _render_recent(rows: pd.DataFrame):
    """Compact list as styled HTML — no st.dataframe (its column widths
    fight you on phones)."""
    if rows.empty:
        st.caption("No recent workouts.")
        return
    items = []
    for _, r in rows.iterrows():
        items.append(
            f"""
            <div style="display:grid;
                        grid-template-columns:54px 1fr 1fr auto;
                        gap:10px;align-items:center;padding:10px 4px;
                        border-bottom:1px solid {ui.LINE};font-size:12.5px;">
              <div style="color:{ui.INK_2};font-variant-numeric:tabular-nums;">
                {r['date'].strftime('%b %d')}
              </div>
              <div style="font-weight:500;color:{ui.INK_0};">{r['label']}</div>
              <div style="font-variant-numeric:tabular-nums;">
                {int(r['distance_m']):,}m · {r['duration']}
              </div>
              <div style="font-variant-numeric:tabular-nums;color:{ui.INK_1};">
                {r['pace']}/500
              </div>
            </div>
            """
        )
    st.html("<div>" + "".join(items) + "</div>")
