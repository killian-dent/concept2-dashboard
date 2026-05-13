"""
Trends tab — multi-period analytics charts.

Direct port of section 3 ("Progress Charts") with the chart styling brought
in line with the new dark palette. Logic unchanged.

Sub-tabs: Meters / Week · Pace · SPM · Heart rate
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import ui
from data import format_pace, weekly_meters, pace_trend


def _strip_tz(series: pd.Series) -> pd.Series:
    """Plotly's auto-ticking misbehaves with tz-aware datetimes — strip it."""
    if series.dt.tz is not None:
        return series.dt.tz_convert("UTC").dt.tz_localize(None)
    return series


def _date_xaxis(df: pd.DataFrame) -> dict:
    """Explicit tick positions scaled to the data span.

    tickmode="array" with pre-formatted ticktext is used throughout so
    Plotly never falls back to its own datetime formatter (which appends
    the time component and makes labels messy).

    Tick density:
      ≤ 14 days  → daily ticks,       "May 13"
      ≤ 90 days  → weekly ticks,      "May 13"
      > 90 days  → month-start ticks, "May '26"
    """
    dmin, dmax = df["date"].min(), df["date"].max()
    if dmin.tz is not None:
        dmin = dmin.tz_convert("UTC").tz_localize(None)
        dmax = dmax.tz_convert("UTC").tz_localize(None)

    span = (dmax - dmin).days

    if span <= 14:
        ticks = pd.date_range(start=dmin.normalize(), end=dmax, freq="D")
        fmt = "%b %d"
    elif span <= 90:
        ticks = pd.date_range(start=dmin.normalize(), end=dmax, freq="W-MON")
        # Always include the first data point so the axis isn't blank
        if len(ticks) == 0 or ticks[0] > dmin:
            ticks = pd.DatetimeIndex([dmin.normalize()]).append(ticks)
        fmt = "%b %d"
    else:
        ticks = pd.date_range(
            start=dmin.replace(day=1),
            end=dmax + pd.DateOffset(months=1),
            freq="MS",
        )
        fmt = "%b '%y"

    return dict(
        type="date", tickmode="array",
        tickvals=ticks.tolist(),
        ticktext=[d.strftime(fmt) for d in ticks],
        gridcolor=ui.LINE, color=ui.INK_2,
    )


def _layout(**extra):
    """Shared dark-theme layout used by every trend chart."""
    base = dict(
        height=320, margin=dict(t=10, l=6, r=6, b=6),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=ui.INK_1, size=11),
        legend=dict(orientation="h", y=1.12, x=0,
                    font=dict(size=11, color=ui.INK_1)),
    )
    base.update(extra)
    return base


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No data for trends yet.")
        return

    t_w, t_p, t_s, t_h = st.tabs(["Meters / Week", "Pace", "SPM", "Heart rate"])
    xaxis = _date_xaxis(df)

    with t_w:
        wm = weekly_meters(df)
        wm["week"] = _strip_tz(wm["week"])
        fig = px.bar(
            wm, x="week", y="meters",
            labels={"week": "", "meters": "Meters"},
            color_discrete_sequence=[ui.ACCENT_SEL],
        )
        fig.update_xaxes(**xaxis)
        fig.update_yaxes(gridcolor=ui.LINE, color=ui.INK_2)
        fig.update_layout(**_layout())
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})

    with t_p:
        dist_options = {
            "All distances":          (0, 99999),
            "Short (≤ 2000m)":        (0, 2000),
            "Medium (2001 – 6000m)":  (2001, 6000),
            "Long (> 6000m)":         (6001, 99999),
        }
        chosen = st.selectbox("Filter by distance", list(dist_options.keys()),
                              key="trends_pace_filter",
                              label_visibility="collapsed")
        lo, hi = dist_options[chosen]
        pt = pace_trend(df, lo, hi)
        if pt.empty:
            st.info("No workouts match this filter.")
        else:
            x = _strip_tz(pt["date"])
            fig = go.Figure(go.Scatter(
                x=x, y=pt["pace_s"],
                mode="markers+lines",
                marker=dict(size=6, color=ui.ACCENT_SEL),
                line=dict(width=1.5, color=ui.ACCENT_SEL),
                text=pt.apply(lambda r: f"{r['label']}<br>{r['pace']}", axis=1),
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra></extra>",
            ))
            fig.update_yaxes(
                autorange="reversed",
                tickvals=list(range(90, 160, 5)),
                ticktext=[format_pace(v) for v in range(90, 160, 5)],
                gridcolor=ui.LINE, color=ui.INK_2, title="",
            )
            fig.update_xaxes(**xaxis)
            fig.update_layout(**_layout())
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False})

    with t_s:
        spm_df = df.sort_values("date")[["date", "spm", "label"]].dropna()
        x = _strip_tz(spm_df["date"])
        roll = spm_df["spm"].rolling(7, min_periods=1).mean()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=x, y=spm_df["spm"], mode="markers", name="SPM",
            marker=dict(size=5, color=ui.INK_2),
            text=spm_df["label"],
            hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>%{y} SPM<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=x, y=roll, mode="lines", name="7-workout avg",
            line=dict(width=2, color=ui.ACCENT_SEL),
        ))
        fig.update_xaxes(**xaxis)
        fig.update_yaxes(gridcolor=ui.LINE, color=ui.INK_2, title="")
        fig.update_layout(**_layout())
        st.plotly_chart(fig, use_container_width=True,
                        config={"displayModeBar": False})

    with t_h:
        hr_df = df[df["hr_avg"] > 0].sort_values("date")[["date", "hr_avg", "label"]]
        if hr_df.empty:
            st.info("No heart rate data available.")
        else:
            x = _strip_tz(hr_df["date"])
            roll = hr_df["hr_avg"].rolling(7, min_periods=1).mean()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=x, y=hr_df["hr_avg"], mode="markers", name="Avg HR",
                marker=dict(size=5, color=ui.INK_2),
                text=hr_df["label"],
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>%{y} bpm<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=x, y=roll, mode="lines", name="7-workout avg",
                line=dict(width=2, color=ui.ACCENT_WARN),
            ))
            fig.update_xaxes(**xaxis)
            fig.update_yaxes(gridcolor=ui.LINE, color=ui.INK_2, title="")
            fig.update_layout(**_layout())
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False})
