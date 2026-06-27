"""
Trends tab — multi-period analytics charts, rewritten with Altair.

Altair (Vega-Lite) handles date axes natively: ticks auto-scale to the
data range and align exactly to data points. No manual tick position
arrays needed. Altair ships with Streamlit so there's no extra dependency.

Sub-tabs: Meters / Week · Pace · SPM · Heart rate
"""
import altair as alt
import pandas as pd
import streamlit as st

import ui
from data import weekly_meters, pace_trend


def _to_day(series: pd.Series) -> pd.Series:
    """Strip timezone and normalize to midnight so data points align with
    day-boundary ticks. Without this a workout at 7:30am sits between the
    midnight ticks for its day and the next, making labels appear offset."""
    if series.dt.tz is not None:
        series = series.dt.tz_convert("UTC").dt.tz_localize(None)
    return series.dt.normalize()



def render(df: pd.DataFrame):
    if df.empty:
        st.info("No data for trends yet.")
        return

    _range_days = {"Last 3 months": 90, "Last 12 months": 365, "All time": None}
    chosen = st.selectbox(
        "Time range", list(_range_days.keys()),
        index=1,
        key="trends_time_range",
        label_visibility="collapsed",
    )
    days = _range_days[chosen]
    if days is not None:
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        _dates = (df["date"].dt.tz_convert("UTC")
                  if df["date"].dt.tz is not None
                  else df["date"].dt.tz_localize("UTC"))
        df = df[_dates >= cutoff].copy()

    if df.empty:
        st.info("No workouts in this time range.")
        return

    t_w, t_p, t_s, t_h = st.tabs(["Meters / Week", "Pace", "SPM", "Heart rate"])

    with t_w:
        _chart_weekly(df)
    with t_p:
        _chart_pace(df)
    with t_s:
        _chart_spm(df)
    with t_h:
        _chart_hr(df)


def _chart_weekly(df: pd.DataFrame):
    wm = weekly_meters(df).copy()
    wm["week"] = _to_day(wm["week"])
    wm["is_latest"] = wm["week"] == wm["week"].max()

    chart = (
        alt.Chart(wm)
        .mark_bar(color=ui.ACCENT_SEL, cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("week:T", axis=alt.Axis(format="%b %d", title=None, labelAngle=-30, tickCount="day")),
            y=alt.Y("meters:Q", axis=alt.Axis(title=None)),
            # Latest week at full strength; prior weeks faded, so the current
            # week reads as the focal point (matches the Trends mockup).
            opacity=alt.condition("datum.is_latest", alt.value(1.0), alt.value(0.4)),
            tooltip=[
                alt.Tooltip("week:T", format="%b %d, %Y", title="Week of"),
                alt.Tooltip("meters:Q", format=",", title="Meters"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(ui.altair_theme(chart), use_container_width=True)


def _chart_pace(df: pd.DataFrame):
    dist_options = {
        "All distances":          (0, 99999),
        "Short (≤ 2000m)":        (0, 2000),
        "Medium (2001 – 6000m)":  (2001, 6000),
        "Long (> 6000m)":         (6001, 99999),
    }
    chosen = st.selectbox(
        "Filter by distance", list(dist_options.keys()),
        key="trends_pace_filter", label_visibility="collapsed",
    )
    lo, hi = dist_options[chosen]
    pt = pace_trend(df, lo, hi)

    if pt.empty:
        st.info("No workouts match this filter.")
        return

    pt = pt.copy()
    pt["date"] = _to_day(pt["date"])

    # Vega expression to format seconds as M:SS on the y-axis labels
    pace_label_expr = (
        "floor(datum.value / 60) + ':' + "
        "(floor(datum.value % 60) < 10 ? '0' + floor(datum.value % 60) "
        ": '' + floor(datum.value % 60))"
    )

    base = alt.Chart(pt).encode(
        x=alt.X("date:T", axis=alt.Axis(format="%b %d", title=None, labelAngle=-30, tickCount="day")),
        y=alt.Y(
            "pace_s:Q",
            scale=alt.Scale(reverse=True),
            axis=alt.Axis(labelExpr=pace_label_expr, title=None),
        ),
        tooltip=[
            alt.Tooltip("date:T", format="%Y-%m-%d", title="Date"),
            alt.Tooltip("label:N", title="Workout"),
            alt.Tooltip("pace:N", title="Pace /500m"),
        ],
    )

    chart = (
        base.mark_line(color=ui.ACCENT_SEL, strokeWidth=1.5)
        + base.mark_point(color=ui.ACCENT_SEL, filled=True, size=50)
    ).properties(height=320)

    st.altair_chart(ui.altair_theme(chart), use_container_width=True)


def _chart_spm(df: pd.DataFrame):
    spm_df = df.sort_values("date")[["date", "spm", "label"]].dropna().copy()
    spm_df["date"] = _to_day(spm_df["date"])
    spm_df["avg7"] = spm_df["spm"].rolling(7, min_periods=1).mean()

    base = alt.Chart(spm_df).encode(
        x=alt.X("date:T", axis=alt.Axis(format="%b %d", title=None, labelAngle=-30, tickCount="day")),
    )
    points = base.mark_point(color=ui.INK_2, filled=True, size=35, opacity=0.8).encode(
        y=alt.Y("spm:Q", axis=alt.Axis(title=None)),
        tooltip=[
            alt.Tooltip("date:T", format="%Y-%m-%d", title="Date"),
            alt.Tooltip("label:N", title="Workout"),
            alt.Tooltip("spm:Q", title="SPM"),
        ],
    )
    line = base.mark_line(color=ui.ACCENT_SEL, strokeWidth=2).encode(
        y=alt.Y("avg7:Q"),
    )
    chart = (
        alt.layer(points, line)
        .resolve_scale(y="shared")
        .properties(height=320)
    )
    st.altair_chart(ui.altair_theme(chart), use_container_width=True)


def _chart_hr(df: pd.DataFrame):
    hr_df = (
        df[df["hr_avg"] > 0]
        .sort_values("date")[["date", "hr_avg", "label"]]
        .copy()
    )
    if hr_df.empty:
        st.info("No heart rate data available.")
        return

    hr_df["date"] = _to_day(hr_df["date"])
    hr_df["avg7"] = hr_df["hr_avg"].rolling(7, min_periods=1).mean()

    base = alt.Chart(hr_df).encode(
        x=alt.X("date:T", axis=alt.Axis(format="%b %d", title=None, labelAngle=-30, tickCount="day")),
    )
    points = base.mark_point(color=ui.INK_2, filled=True, size=35, opacity=0.8).encode(
        y=alt.Y("hr_avg:Q", axis=alt.Axis(title=None)),
        tooltip=[
            alt.Tooltip("date:T", format="%Y-%m-%d", title="Date"),
            alt.Tooltip("label:N", title="Workout"),
            alt.Tooltip("hr_avg:Q", title="Avg HR"),
        ],
    )
    line = base.mark_line(color=ui.ACCENT_WARN, strokeWidth=2).encode(
        y=alt.Y("avg7:Q"),
    )
    chart = (
        alt.layer(points, line)
        .resolve_scale(y="shared")
        .properties(height=320)
    )
    st.altair_chart(ui.altair_theme(chart), use_container_width=True)
