"""
Concept2 Personal Dashboard — main Streamlit application.
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import is_placeholder_token
import api
from data import (
    load_results_df,
    compute_summary,
    compute_prs,
    weekly_meters,
    pace_trend,
    format_pace,
    format_duration,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Concept2 Dashboard",
    page_icon="🚣",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "selected_id" not in st.session_state:
    st.session_state.selected_id = None
if "selector_ver" not in st.session_state:
    st.session_state.selector_ver = 0

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Loading workouts…", ttl=300)
def get_data() -> tuple:
    return tuple(api.fetch_results())


@st.cache_data(show_spinner=False, ttl=3600)
def get_challenges() -> list:
    return api.fetch_challenges()


try:
    raw_results = get_data()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

df = load_results_df(raw_results)

# ---------------------------------------------------------------------------
# Header & sample-data warning
# ---------------------------------------------------------------------------

col_title, col_badge = st.columns([4, 1])
with col_title:
    st.title("🚣 Concept2 Dashboard")
with col_badge:
    if is_placeholder_token():
        st.warning("Sample data", icon="⚠️")

if is_placeholder_token():
    st.info(
        "**Running with sample data.** To see your real workouts, add your Concept2 API token to "
        "`config.py` (replace `YOUR_TOKEN_HERE`). See README.md for instructions.",
        icon="ℹ️",
    )

st.divider()

# ---------------------------------------------------------------------------
# 1. Summary bar
# ---------------------------------------------------------------------------

if not df.empty:
    summary = compute_summary(df)

    c1, c2, c3, c4, c5, c6 = st.columns(6)

    def fmt_km(meters: int) -> str:
        return f"{meters / 1000:,.1f} km"

    def fmt_time(total_s: float) -> str:
        h = int(total_s // 3600)
        m = int((total_s % 3600) // 60)
        return f"{h:,}h {m}m"

    c1.metric("Total Distance",  fmt_km(summary["total_meters"]))
    c2.metric("Total Workouts",  f"{summary['total_workouts']:,}")
    c3.metric("Total Time",      fmt_time(summary["total_time_s"]))
    c4.metric("This Month",      fmt_km(summary["this_month_m"]))
    c5.metric("This Year",       fmt_km(summary["this_year_m"]))
    c6.metric("Current Streak",  f"{summary['streak_days']} days")

    st.divider()

# ---------------------------------------------------------------------------
# 2. Recent workouts + inline detail view
# ---------------------------------------------------------------------------

st.subheader("Recent Workouts")

if df.empty:
    st.info("No workout data available.")
else:
    recent = df.head(20).copy()

    display_cols = {
        "date":       "Date",
        "label":      "Workout",
        "distance_m": "Distance (m)",
        "duration":   "Duration",
        "pace":       "Pace /500m",
        "spm":        "SPM",
        "watts":      "Watts",
        "calories":   "Cal",
        "hr_avg":     "Avg HR",
    }
    display = recent[list(display_cols.keys())].rename(columns=display_cols)
    display["Date"] = display["Date"].dt.strftime("%Y-%m-%d")
    display["Distance (m)"] = display["Distance (m)"].apply(lambda x: f"{int(x):,}")

    st.dataframe(display, use_container_width=True, hide_index=True)

    # Selector
    workout_options = {
        f"{row['date'].strftime('%Y-%m-%d')} — {row['label']} ({row['duration']})": row["id"]
        for _, row in recent.iterrows()
    }
    col_sel, col_clr = st.columns([5, 1])
    with col_sel:
        selected_label = st.selectbox(
            "Select a workout to view splits:",
            options=["— none —"] + list(workout_options.keys()),
            key=f"workout_selector_{st.session_state.selector_ver}",
        )
    with col_clr:
        st.write("")  # vertical alignment spacer
        if st.button("Clear", use_container_width=True):
            st.session_state.selected_id = None
            st.session_state.selector_ver += 1  # new key forces selectbox to remount at index 0
            st.rerun()

    if selected_label != "— none —":
        st.session_state.selected_id = workout_options[selected_label]

    # ── Inline detail ──────────────────────────────────────────────────────
    selected_id = st.session_state.get("selected_id")
    if selected_id is not None:
        row_mask = df["id"] == selected_id
        if row_mask.any():
            row = df[row_mask].iloc[0]
            splits = row["splits"]

            st.markdown("---")

            h1, h2, h3, h4, h5 = st.columns(5)
            h1.metric("Date",     row["date"].strftime("%Y-%m-%d"))
            h2.metric("Workout",  row["label"])
            h3.metric("Distance", f"{int(row['distance_m']):,} m")
            h4.metric("Time",     row["duration"])
            h5.metric("Avg Pace", row["pace"])

            r1, r2, r3 = st.columns(3)
            r1.metric("Avg SPM",   row["spm"])
            r2.metric("Avg Watts", f"{row['watts']:.0f} W")
            r3.metric("Calories",  f"{int(row['calories'])} kcal")

            if splits:
                splits_df = pd.DataFrame(splits)
                display_splits = splits_df[
                    ["split_number", "distance", "pace_formatted", "spm", "watts", "heart_rate"]
                ].copy()
                display_splits.columns = ["Split", "Distance (m)", "Pace /500m", "SPM", "Watts", "Heart Rate"]
                st.dataframe(display_splits, hide_index=True, use_container_width=True)

                pace_vals = [s["pace"] for s in splits]
                y_min = max(0, min(pace_vals) - 3)
                y_max = max(pace_vals) + 3
                fig = go.Figure(go.Bar(
                    x=[f"Split {s['split_number']}" for s in splits],
                    y=[s["pace"] for s in splits],
                    marker_color="#00b4d8",
                    text=[s["pace_formatted"] for s in splits],
                    textposition="outside",
                ))
                fig.update_yaxes(
                    range=[y_max, y_min],
                    tickvals=list(range(int(y_min), int(y_max) + 1, 2)),
                    ticktext=[format_pace(v) for v in range(int(y_min), int(y_max) + 1, 2)],
                    title="Pace /500m",
                )
                fig.update_layout(
                    title="Pace per Split (lower = faster)",
                    xaxis_title="Split",
                    height=320,
                    margin=dict(t=40),
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("No split data available for this workout.")

st.divider()

# ---------------------------------------------------------------------------
# 3. Progress charts
# ---------------------------------------------------------------------------

st.subheader("Progress Charts")

if not df.empty:
    tab_weekly, tab_pace, tab_spm, tab_hr = st.tabs(
        ["📊 Meters / Week", "📈 Pace Trend", "🔄 SPM Trend", "❤️ Heart Rate Trend"]
    )

    with tab_weekly:
        wm = weekly_meters(df)
        fig = px.bar(
            wm, x="week", y="meters",
            labels={"week": "Week", "meters": "Meters Rowed"},
            color_discrete_sequence=["#00b4d8"],
        )
        fig.update_layout(margin=dict(t=20), height=380)
        st.plotly_chart(fig, use_container_width=True)

    with tab_pace:
        dist_options = {
            "All distances":          (0, 99999),
            "Short (≤ 2000m)":        (0, 2000),
            "Medium (2001 – 6000m)":  (2001, 6000),
            "Long (> 6000m)":         (6001, 99999),
        }
        chosen = st.selectbox("Filter by distance:", list(dist_options.keys()), key="pace_filter")
        lo, hi = dist_options[chosen]
        pt = pace_trend(df, lo, hi)

        if pt.empty:
            st.info("No workouts match this filter.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=pt["date"], y=pt["pace_s"],
                mode="markers+lines",
                marker=dict(size=6, color="#00b4d8"),
                line=dict(width=1.5, color="#00b4d8"),
                text=pt.apply(lambda r: f"{r['label']}<br>{r['pace']}", axis=1),
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<extra></extra>",
            ))
            fig.update_yaxes(
                autorange="reversed",
                tickvals=list(range(90, 160, 5)),
                ticktext=[format_pace(v) for v in range(90, 160, 5)],
                title="Pace /500m",
            )
            fig.update_xaxes(title="Date")
            fig.update_layout(margin=dict(t=20), height=380)
            st.plotly_chart(fig, use_container_width=True)

    with tab_spm:
        spm_df = df.sort_values("date")[["date", "spm", "label"]].dropna()
        spm_roll = spm_df["spm"].rolling(7, min_periods=1).mean()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=spm_df["date"], y=spm_df["spm"],
            mode="markers", name="SPM",
            marker=dict(size=6, color="#48cae4"),
            text=spm_df["label"],
            hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>%{y} SPM<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=spm_df["date"], y=spm_roll,
            mode="lines", name="7-workout avg",
            line=dict(width=2, color="#0077b6"),
        ))
        fig.update_layout(yaxis_title="Strokes per Minute", xaxis_title="Date",
                          margin=dict(t=20), height=380, legend=dict(orientation="h"))
        st.plotly_chart(fig, use_container_width=True)

    with tab_hr:
        hr_df = df[df["hr_avg"] > 0].sort_values("date")[["date", "hr_avg", "label"]]
        if hr_df.empty:
            st.info("No heart rate data available.")
        else:
            hr_roll = hr_df["hr_avg"].rolling(7, min_periods=1).mean()
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=hr_df["date"], y=hr_df["hr_avg"],
                mode="markers", name="Avg HR",
                marker=dict(size=6, color="#ef476f"),
                text=hr_df["label"],
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>%{y} bpm<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=hr_df["date"], y=hr_roll,
                mode="lines", name="7-workout avg",
                line=dict(width=2, color="#c1121f"),
            ))
            fig.update_layout(yaxis_title="Avg Heart Rate (bpm)", xaxis_title="Date",
                              margin=dict(t=20), height=380, legend=dict(orientation="h"))
            st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# 4. Personal Records
# ---------------------------------------------------------------------------

st.subheader("Personal Records & Rankings")

if not df.empty:
    dist_prs, timed_prs = compute_prs(df)

    tab_pr, tab_rank = st.tabs(["🏅 Personal Records", "🏆 Rankings"])

    with tab_pr:
        pr_col1, pr_col2 = st.columns(2)
        with pr_col1:
            st.markdown("**Distance Events**")
            st.dataframe(dist_prs, hide_index=True, use_container_width=True)
        with pr_col2:
            st.markdown("**Timed Events**")
            st.dataframe(timed_prs, hide_index=True, use_container_width=True)

    with tab_rank:
        _ranking_distances = {
            "500m":   500,
            "1000m":  1000,
            "2000m":  2000,
            "5000m":  5000,
            "6000m":  6000,
            "10000m": 10000,
            "Half Marathon": 21097,
            "Marathon":      42195,
        }
        _year = pd.Timestamp.now().year
        _gender = "M"  # TODO: pull from fetch_profile() if needed

        # Only look up distances where the user actually has a PR
        _distances_with_pr = {
            name: dist
            for name, dist in _ranking_distances.items()
            if not dist_prs.loc[dist_prs["Event"] == name, "Best Time"].eq("—").all()
        }

        @st.cache_data(show_spinner=False, ttl=1800)
        def _fetch_rankings(distances: tuple, year: int, gender: str) -> dict:
            results = {}
            for name, dist in distances:
                results[name] = api.fetch_ranking(dist, year, gender)
            return results

        if not _distances_with_pr:
            st.caption(
                f"No ranked results yet for {_year}. "
                "Log a fixed-distance piece (2000m, 5000m, etc.) to appear on the rankings board."
            )
        else:
            with st.spinner(f"Looking up your {_year} rankings…"):
                ranking_data = _fetch_rankings(
                    tuple(_distances_with_pr.items()), _year, _gender
                )

            rows = []
            for name, result in ranking_data.items():
                if result:
                    rows.append({
                        "Distance":  name,
                        "Rank":      f"#{result['rank']:,}",
                        "Time":      result["time"],
                        "Year":      _year,
                        "Rankings page": result["rankings_url"],
                    })
                else:
                    rows.append({
                        "Distance":  name,
                        "Rank":      "Not in top 1,000",
                        "Time":      "—",
                        "Year":      _year,
                        "Rankings page": "",
                    })

            rank_df = pd.DataFrame(rows)
            # Separate link column so we don't clutter the table
            link_col = rank_df.pop("Rankings page")
            st.dataframe(rank_df, hide_index=True, use_container_width=True)

            for name, url in zip(rank_df["Distance"], link_col):
                if url:
                    st.caption(f"[{name} rankings page →]({url})")

        # ── WOD honorboard ────────────────────────────────────────────────
        st.markdown("---")
        st.markdown("**WOD Honorboard**")

        # Collect unique dates from the user's results (most recent first)
        if not df.empty:
            wod_dates = (
                df["date"].dt.strftime("%Y-%m-%d")
                .drop_duplicates()
                .head(14)
                .tolist()
            )

            @st.cache_data(show_spinner=False, ttl=86400)
            def _fetch_wod_rankings(dates: tuple) -> list:
                rows = []
                for d in dates:
                    result = api.fetch_wod_ranking(d, machine="rowerg")
                    if result:
                        rows.append({"Date": d, **result})
                return rows

            with st.spinner("Looking up WOD rankings…"):
                wod_rows = _fetch_wod_rankings(tuple(wod_dates))

            if not wod_rows:
                st.caption("No WOD honorboard appearances found in your recent workouts.")
            else:
                wod_display = pd.DataFrame([
                    {
                        "Date":    r["Date"],
                        "Rank":    f"#{r['rank']:,}",
                        "Result":  r["result"],
                        "Pace":    r["pace"],
                        "~ Field": f"~{r['total']:,}",
                    }
                    for r in wod_rows
                ])
                st.dataframe(wod_display, hide_index=True, use_container_width=True)
                for r in wod_rows:
                    st.caption(f"[{r['Date']} WOD honorboard →]({r['url']})")

st.divider()

# ---------------------------------------------------------------------------
# 5. Challenges
# ---------------------------------------------------------------------------

st.subheader("Challenges")

challenges = get_challenges()

if not challenges:
    st.caption("No active challenges found.")
else:
    for ch in challenges:
        ch_start = pd.Timestamp(ch.get("start", "2000-01-01"), tz="UTC")
        ch_end   = pd.Timestamp(ch.get("end",   "2000-01-01"), tz="UTC")

        # Calculate meters the user logged during this challenge window
        user_meters = 0
        if not df.empty:
            dates = df["date"].dt.tz_localize("UTC") if df["date"].dt.tz is None else df["date"].dt.tz_convert("UTC")
            mask = (dates >= ch_start) & (dates <= ch_end + pd.Timedelta(days=1))
            user_meters = int(df.loc[mask, "distance_m"].sum())

        with st.container():
            col_info, col_stats = st.columns([3, 2])
            with col_info:
                st.markdown(f"**{ch['name']}**")
                st.caption(ch.get("description", ""))
                st.caption(
                    f"{ch_start.strftime('%b %-d')} – {ch_end.strftime('%b %-d, %Y')}  ·  "
                    f"{ch.get('activity', '')}  ·  {ch.get('category', '')}"
                )
                link = ch.get("link", "")
                if link:
                    st.markdown(f"[View on Concept2 →]({link})")
            with col_stats:
                if user_meters:
                    st.metric("Your meters this challenge", f"{user_meters:,} m")
                else:
                    st.caption("No logged meters in this challenge window yet.")

        st.markdown("")

st.divider()
st.caption("Data refreshes every 5 minutes · Challenges refresh hourly · Built with Streamlit + Plotly")
