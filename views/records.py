"""
Records tab — PRs and rankings merged into a single dense list.

Replaces section 4's nested "Personal Records" + "Rankings" tabs (and the
two side-by-side tables inside the PR tab). Now: one row per event, every
piece of information inline.

What lives on each row:
  • Event name (left)
  • Best time + pace + date + pace-trend sparkline (middle)
  • Current-year world rank (right, when available)

Empty PRs become invitations ("No record yet — row this distance to set
one") instead of "—".

The WOD honorboard which lived in section 4 has moved to its own tab
(views/wod.py).
"""
import pandas as pd
import streamlit as st

import api
import config
import ui
from data import compute_prs, format_pace
from data_extras import pr_sparkline_series


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workout data available.")
        return

    dist_prs, timed_prs = compute_prs(df)

    # Only look up rankings for events the user has actually completed
    distances_with_pr = {
        name: dist
        for name, dist in config.STANDARD_DISTANCES.items()
        if dist_prs.loc[dist_prs["Event"] == name, "Best Time"].ne("—").any()
    }

    year = pd.Timestamp.now().year
    gender = "M"  # TODO: pull from api.fetch_profile() once that's wired up

    @st.cache_data(show_spinner="Looking up rankings…", ttl=86400)
    def _fetch(distances: tuple, year: int, gender: str) -> dict:
        return {n: api.fetch_ranking(d, year, gender) for n, d in distances}

    rankings = (_fetch(tuple(distances_with_pr.items()), year, gender)
                if distances_with_pr else {})

    st.caption(
        f"Lifetime records · {year} rankings shown where you've placed "
        "in the top 1,000."
    )

    # ── Distance events ──────────────────────────────────────────────────
    for _, pr in dist_prs.iterrows():
        ev = pr["Event"]
        dist = config.STANDARD_DISTANCES[ev]
        spark = pr_sparkline_series(df, dist, count=10)
        _render_dist_row(
            event=ev,
            time=pr["Best Time"],
            pace=pr["Best Pace"],
            date=pr["Date"],
            rank_info=rankings.get(ev),
            spark=spark,
            year=year,
        )

    # ── Timed events (no rankings — different Concept2 category) ─────────
    if not timed_prs.empty:
        st.html(
            f"<div style='font-size:10px;color:{ui.INK_2};letter-spacing:0.12em;"
            f"text-transform:uppercase;font-weight:600;margin:20px 0 6px;'>"
            f"Timed events</div>"
        )
        for _, tpr in timed_prs.iterrows():
            _render_timed_row(tpr)


# ── Row renderers ────────────────────────────────────────────────────────

def _render_dist_row(event, time, pace, date, rank_info, spark, year):
    is_blank = time == "—"

    # Right column: world rank or "unranked"
    if rank_info:
        rank_url = rank_info.get("rankings_url", "")
        rank_html = (
            f"<a href='{rank_url}' target='_blank' style='text-decoration:none;'>"
            f"<div style='text-align:right;font-variant-numeric:tabular-nums;'>"
            f"<div style='font-size:13px;color:{ui.INK_0};font-weight:600;'>"
            f"#{rank_info['rank']:,}</div>"
            f"<div style='font-size:9px;color:{ui.INK_3};'>{year} world</div>"
            f"</div></a>"
        )
    elif not is_blank:
        rank_html = (
            f"<div style='text-align:right;color:{ui.INK_3};font-size:11px;'>"
            f"unranked</div>"
        )
    else:
        rank_html = ""

    spark_html = (
        ui.sparkline_html(spark, width=58, height=16, color=ui.INK_2)
        if spark else ""
    )

    if is_blank:
        body = (
            f"<div style='font-size:11.5px;color:{ui.INK_3};font-style:italic;'>"
            f"No record yet · row this distance to set one</div>"
        )
    else:
        body = (
            f"<div style='font-size:15px;font-weight:600;letter-spacing:-0.01em;"
            f"font-variant-numeric:tabular-nums;color:{ui.INK_0};'>{time}</div>"
            f"<div style='display:flex;align-items:center;gap:8px;margin-top:3px;'>"
            f"<span style='font-size:11px;color:{ui.INK_2};"
            f"font-variant-numeric:tabular-nums;'>{pace}/500 · {date}</span>"
            f"{spark_html}</div>"
        )

    st.html(f"""
    <div style="display:grid;grid-template-columns:80px 1fr 80px;gap:10px;
                align-items:center;padding:12px 4px;
                border-bottom:1px solid {ui.LINE};">
      <div style="font-size:13.5px;font-weight:500;">{event}</div>
      <div>{body}</div>
      <div>{rank_html}</div>
    </div>
    """)


def _render_timed_row(tpr):
    ev = tpr["Event"]
    dist = tpr["Best Distance"]
    pace = tpr["Best Pace"]
    date = tpr["Date"]
    blank = dist == "—"

    body = (
        f"<div style='font-size:11.5px;color:{ui.INK_3};font-style:italic;'>"
        f"No record yet · row this duration to set one</div>"
        if blank else
        f"<div style='font-size:15px;font-weight:600;letter-spacing:-0.01em;"
        f"font-variant-numeric:tabular-nums;color:{ui.INK_0};'>{dist}</div>"
        f"<div style='font-size:11px;color:{ui.INK_2};margin-top:3px;"
        f"font-variant-numeric:tabular-nums;'>{pace}/500 · {date}</div>"
    )

    st.html(f"""
    <div style="display:grid;grid-template-columns:80px 1fr;gap:10px;
                align-items:center;padding:12px 4px;
                border-bottom:1px solid {ui.LINE};">
      <div style="font-size:13.5px;font-weight:500;">{ev}</div>
      <div>{body}</div>
    </div>
    """)
