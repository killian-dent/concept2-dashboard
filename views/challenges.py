"""
Challenges tab — Concept2 challenge progress.

Light cleanup of section 5: each challenge becomes a card with the same
information, in our card style. Logic / data flow unchanged.
"""
import pandas as pd
import streamlit as st

import api
import ui


@st.cache_data(show_spinner=False, ttl=21600)
def _challenges() -> list:
    return api.fetch_challenges()


def render():
    items = _challenges()
    if not items:
        st.caption("No active challenges found.")
        return

    for ch in items:
        start = pd.Timestamp(ch.get("start", "2000-01-01"), tz="UTC")
        end   = pd.Timestamp(ch.get("end",   "2000-01-01"), tz="UTC")
        details = f"{start.strftime('%b %-d')} – {end.strftime('%b %-d, %Y')}"
        if ch.get("activity"):
            details += f"  ·  {ch['activity']}"
        if ch.get("category"):
            details += f"  ·  {ch['category']}"

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
          <div style="font-size:10.5px;color:{ui.INK_3};margin-top:8px;
                      font-variant-numeric:tabular-nums;">{details}</div>
          {link_html}
        </div>
        """)
