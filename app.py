"""
Concept2 Personal Dashboard — refactored entry point (Direction B).

What changed vs the original app.py:

  • Sidebar is gone. User ID input now lives in a header popover (less
    permanent chrome, more screen real estate on mobile).
  • Six numbered sections → seven top-level tabs. st.tabs gives you a
    horizontally-scrolling segmented control on phones for free.
  • This file is the ROUTER. Each tab's body lives in views/<name>.py so
    you can edit one screen without scrolling past unrelated code.
  • compute_period_kpis / daily_meters / wod_percentile etc. live in
    data_extras.py (additive — does not modify your existing data.py).
"""
import streamlit as st

import api
import config
from config import is_placeholder_token
from data import load_results_df

import ui
from views import overview, workouts, trends, records, compare, wod, challenges


# ── Page config ───────────────────────────────────────────────────────────
# layout="centered" gives us a max-width column instead of edge-to-edge —
# reads much better on iPad and matches mobile naturally.
st.set_page_config(
    page_title="Concept2",
    page_icon="🚣",
    layout="centered",
    initial_sidebar_state="collapsed",
)
ui.inject_styles()


# ── Session state ─────────────────────────────────────────────────────────
if "user_id" not in st.session_state:
    _cfg = getattr(config, "USER_ID", None)
    st.session_state.user_id = str(_cfg) if _cfg else "me"

if "selected_workout_id" not in st.session_state:
    st.session_state.selected_workout_id = None


# ── Data load ─────────────────────────────────────────────────────────────
# Cache is keyed by user_id; switching IDs in the header popover invalidates.
@st.cache_data(show_spinner="Loading workouts…", ttl=21600)
def get_data(user_id: str):
    return tuple(api.fetch_results(user_id=user_id))


def _set_user(new_id: str):
    """Called by the header popover when the user changes ID."""
    st.session_state.user_id = new_id
    st.cache_data.clear()
    st.rerun()


try:
    raw = get_data(st.session_state.user_id)
except RuntimeError as e:
    st.error(str(e))
    st.stop()

df = load_results_df(raw)


# ── Header ────────────────────────────────────────────────────────────────
ui.render_header(
    user_id=st.session_state.user_id,
    on_change=_set_user,
    is_placeholder=is_placeholder_token(),
)


# ── Tab router ────────────────────────────────────────────────────────────
# IMPORTANT — st.tabs renders ALL bodies on every run. That's fine here:
# all heavy work is behind @st.cache_data, and per-tab work is light. If
# perf becomes an issue (e.g. you add an expensive view), swap st.tabs for
# `tab = st.radio(...)` + if/elif blocks and only render the active one.
tab_overview, tab_workouts, tab_trends, tab_records, tab_compare, tab_wod, tab_challenges = st.tabs(
    ["Overview", "Workouts", "Trends", "Records", "Compare", "WOD", "Challenges"]
)

with tab_overview:    overview.render(df)
with tab_workouts:    workouts.render(df)
with tab_trends:      trends.render(df)
with tab_records:     records.render(df)
with tab_compare:     compare.render(df)
with tab_wod:         wod.render(df)
with tab_challenges:  challenges.render()


st.caption(
    "Data refreshes every 6 hours · WOD rankings cached 24 hours · "
    "Built with Streamlit + Plotly"
)
