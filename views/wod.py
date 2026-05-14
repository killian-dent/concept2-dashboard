"""
WOD tab — Workout of the Day with percentile rendering.

The WOD honorboard block in the original app was buried inside section 4
behind a "Load WOD Rankings" button. This refactor gives it dedicated real
estate AND changes the central metric:

  • Raw rank ("#1,026") is meaningless without the field size.
  • The new metric is PERCENTILE: "top 57%". Same data, instantly readable.
  • Every row renders a percentile pin on a track, with a 50% tick.

Data flow is unchanged — api.fetch_wod_ranking, 24h cache, gated load.
"""
import time as _time

import pandas as pd
import streamlit as st

import api
import db
import ui
from data_extras import wod_summary, wod_percentile


def render(df: pd.DataFrame):
    if df.empty:
        st.info("No workouts logged yet — once you do a WOD, it'll show up here.")
        return

    st.caption(
        "Concept2's daily challenge, ranked against the global field. "
        "Percentile is the metric — raw rank means nothing without field size."
    )

    # ── Load gate ────────────────────────────────────────────────────────
    # Same UX as the original: scanning the honorboard takes 15–20s, so it's
    # behind a button. Cache lives 24h.
    if "wod_loaded" not in st.session_state:
        st.session_state.wod_loaded = False

    if not st.session_state.wod_loaded:
        st.info(
            "Loading WOD rankings scans up to 40 pages of Concept2's site "
            "(~15–20s). Results cached for 24 hours."
        )
        if st.button("Load WOD rankings", type="primary"):
            st.session_state.wod_loaded = True
            st.rerun()
        return

    # Take up to 14 most-recent unique workout dates for the search
    wod_dates = (
        df["date"].dt.strftime("%Y-%m-%d")
                  .drop_duplicates()
                  .head(14)
                  .tolist()
    )

    def _fetch(dates: list) -> list:
        out = []
        made_request = False
        for d in dates:
            cache_key = f"wod:{d}"
            result = db.cache_get(cache_key, ttl_seconds=86400)
            if result is None:
                if made_request:
                    _time.sleep(0.25)
                result = api.fetch_wod_ranking(d, machine="rowerg")
                made_request = True
                if result:
                    db.cache_set(cache_key, result)
            if result:
                out.append({"date": d, **result})
        return out

    with st.spinner("Searching WOD honorboards…"):
        rows = _fetch(wod_dates)

    if not rows:
        st.caption("No WOD honorboard appearances found in your recent workouts.")
        return

    # ── Stats strip (3 KPIs across the top) ──────────────────────────────
    summary = wod_summary(rows)
    s1, s2, s3 = st.columns(3)
    s1.metric("Logged", summary["count"])
    s2.metric("Avg percentile", f"Top {summary['avg_top']}%")
    s3.metric("Best percentile", f"Top {summary['best_top']}%")

    # ── History list with percentile pin per row ─────────────────────────
    st.html(
        f"<div style='font-size:10px;color:{ui.INK_2};letter-spacing:0.12em;"
        f"text-transform:uppercase;font-weight:600;margin:18px 0 6px;'>"
        f"History</div>"
    )
    for r in rows:
        _render_row(r)


def _render_row(r: dict):
    rank = r["rank"]
    field = r["total"]
    top = 100 - wod_percentile(rank, field)
    color = ui.ACCENT_PR if top >= 50 else ui.ACCENT_WARN

    st.html(f"""
    <div style="display:grid;grid-template-columns:62px 1fr 62px;gap:10px;
                align-items:center;padding:12px 4px;
                border-bottom:1px solid {ui.LINE};">
      <div style="font-size:11px;color:{ui.INK_2};letter-spacing:0.06em;
                  font-variant-numeric:tabular-nums;">{r['date']}</div>
      <div>
        <div style="font-size:12.5px;font-variant-numeric:tabular-nums;">
          {r['result']}
          <span style="color:{ui.INK_2};">· {r['pace']}/500</span>
        </div>
        {ui.percentile_bar_html(rank, field, height=5)}
      </div>
      <div style="text-align:right;font-variant-numeric:tabular-nums;">
        <div style="font-size:12px;font-weight:600;color:{color};">Top {top}%</div>
        <div style="font-size:10px;color:{ui.INK_3};">#{rank:,}</div>
      </div>
    </div>
    <div style="text-align:right;margin:-4px 4px 6px;">
      <a href="{r['url']}" target="_blank"
         style="font-size:10.5px;color:{ui.ACCENT_SEL};text-decoration:none;">
         honorboard →
      </a>
    </div>
    """)
