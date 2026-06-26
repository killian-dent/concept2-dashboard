"""
Challenges tab — Concept2 challenge progress.

Each active challenge becomes a card. The challenges API returns no per-user
progress, so we compute it locally: meters rowed within the challenge window,
and — when the goal is stated in the name/description — a progress bar against
that goal. In sample mode we fall back to illustrative sample challenges.
"""
import pandas as pd
import streamlit as st

import api
import ui
from config import is_placeholder_token
from data import SAMPLE_CHALLENGES
from data_extras import meters_in_window, parse_goal_meters


@st.cache_data(show_spinner=False, ttl=21600)
def _challenges() -> list:
    return api.fetch_challenges()


def render(df: pd.DataFrame):
    items = _challenges()

    if not items and is_placeholder_token():
        _render_sample()
        return
    if not items:
        st.caption("No active challenges found.")
        return

    st.caption("Progress is computed from your logged meters within each "
               "challenge window.")

    for ch in items:
        _render_real(ch, df)


# ── Real challenges (progress computed from the user's results) ──────────

def _render_real(ch: dict, df: pd.DataFrame):
    start = pd.Timestamp(ch.get("start", "2000-01-01"))
    end = pd.Timestamp(ch.get("end", "2000-01-01"))
    details = f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
    if ch.get("activity"):
        details += f"  ·  {ch['activity']}"
    if ch.get("category"):
        details += f"  ·  {ch['category']}"

    meters = meters_in_window(df, start, end) if df is not None and not df.empty else 0
    goal = parse_goal_meters(ch.get("name"), ch.get("description"),
                             ch.get("short_description"))
    # Only treat large numbers as meter goals — small ones (e.g. "5000m
    # pieces") are workout descriptions, not challenge targets.
    if goal < 10000:
        goal = 0

    if goal:
        pct = meters / goal * 100
        progress = (
            ui.progress_bar_html(pct, color=ui.ACCENT_PR)
            + f"<div style='font-size:11px;color:{ui.INK_1};"
              f"font-variant-numeric:tabular-nums;'>{meters:,} / {goal:,} m"
              f" <span style='color:{ui.INK_3};'>· {round(pct)}%</span></div>"
        )
    elif meters:
        progress = (
            f"<div style='font-size:11px;color:{ui.INK_1};margin-top:8px;"
            f"font-variant-numeric:tabular-nums;'>{meters:,} m logged so far</div>"
        )
    else:
        progress = ""

    link_html = (
        f'<a href="{ch["link"]}" target="_blank" '
        f'style="display:inline-block;margin-top:8px;font-size:11px;'
        f'color:{ui.ACCENT_SEL};text-decoration:none;">View on Concept2 →</a>'
        if ch.get("link") else ""
    )

    st.html(f"""
    <div style="padding:14px;background:{ui.BG_1};border:1px solid {ui.LINE};
                border-radius:10px;margin-bottom:10px;">
      <div style="font-size:14px;font-weight:600;letter-spacing:-0.01em;
                  color:{ui.INK_0};">{ch['name']}</div>
      <div style="font-size:12px;color:{ui.INK_1};margin-top:4px;
                  line-height:1.4;">{ch.get('description', '')}</div>
      {progress}
      <div style="font-size:10.5px;color:{ui.INK_3};margin-top:8px;
                  font-variant-numeric:tabular-nums;">{details}</div>
      {link_html}
    </div>
    """)


# ── Sample mode ──────────────────────────────────────────────────────────

def _render_sample():
    st.caption("Sample challenges (demo data).")
    for ch in SAMPLE_CHALLENGES:
        pct = ch["progress"] / ch["goal"] * 100 if ch.get("goal") else 0
        unit = ch.get("unit", "")
        st.html(f"""
        <div style="padding:14px;background:{ui.BG_1};border:1px solid {ui.LINE};
                    border-radius:10px;margin-bottom:10px;">
          <div style="font-size:14px;font-weight:600;color:{ui.INK_0};">{ch['name']}</div>
          <div style="font-size:12px;color:{ui.INK_1};margin-top:4px;
                      line-height:1.4;">{ch.get('description', '')}</div>
          {ui.progress_bar_html(pct, color=ui.ACCENT_PR)}
          <div style="font-size:11px;color:{ui.INK_1};
                      font-variant-numeric:tabular-nums;">
            {ch['progress']:,} / {ch['goal']:,} {unit}
            <span style="color:{ui.INK_3};">· {round(pct)}%</span></div>
          <div style="font-size:10.5px;color:{ui.INK_3};margin-top:8px;">
            Ends {ch.get('ends', '')}</div>
        </div>
        """)
