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

Data strategy:
  • Workouts are persisted in a local SQLite DB (data/workouts.db).
  • On each new session we do an incremental sync: only pages newer than
    the most-recently-stored workout are fetched from Concept2.
  • A full re-fetch only happens the very first time for a user_id.
  • The 6-hour throttle prevents re-checking within the same session
    window; "Refresh data" in the header bypasses it explicitly.
"""
from datetime import datetime, timezone

import streamlit as st

import api
import config
import db
from config import is_placeholder_token
from data import load_results_df

import ui
from views import overview, workouts, trends, records, compare, wod, challenges


# ── Page config ───────────────────────────────────────────────────────────
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


# ── Sync helpers ──────────────────────────────────────────────────────────
_SYNC_INTERVAL_S = 6 * 3600


def _needs_sync(uid: str) -> bool:
    if db.count(uid) == 0:
        return True
    last = db.last_synced(uid)
    if last is None:
        return True
    return (datetime.now(tz=timezone.utc) - last).total_seconds() > _SYNC_INTERVAL_S


def _run_sync(uid: str) -> int:
    """Fetch new workouts incrementally and persist to DB. Returns count of new rows."""
    since_id = db.get_newest_id(uid)
    fetch_fn = getattr(api, "fetch_results_incremental", None) or api.fetch_results
    new = fetch_fn(uid, since_id=since_id) if since_id is not None else fetch_fn(uid)
    if new:
        db.upsert(uid, new)
    db.set_synced(uid)
    return len(new)


@st.cache_data(ttl=300)
def _load_df(uid: str):
    """Load and build the DataFrame, cached by uid string (fast hash)."""
    if is_placeholder_token():
        raw = api.fetch_results()
    else:
        raw = db.get_all(uid)
        if not raw:
            raw = api.fetch_results(uid)
    return load_results_df(tuple(raw))


def _set_user(new_id: str):
    """Called by the header popover when the user changes ID."""
    st.session_state.user_id = new_id
    st.session_state.pop(f"synced_{new_id}", None)
    st.cache_data.clear()
    st.rerun()


def _on_refresh():
    """Called by the header Refresh button — force an incremental sync."""
    uid = st.session_state.user_id
    st.session_state["_force_sync"] = True
    st.session_state.pop(f"synced_{uid}", None)
    st.cache_data.clear()
    st.rerun()


# ── Incremental sync (once per session, or on explicit refresh) ───────────
user_id = st.session_state.user_id
_synced_key = f"synced_{user_id}"
_force = st.session_state.pop("_force_sync", False)

if not is_placeholder_token():
    if _force or not st.session_state.get(_synced_key):
        try:
            if _force or _needs_sync(user_id):
                _msg = "Loading workouts…" if db.count(user_id) == 0 else "Checking for new workouts…"
                with st.spinner(_msg):
                    _run_sync(user_id)
                st.cache_data.clear()
        except Exception as _e:
            st.warning(f"Sync failed ({_e}); showing cached data.")
    st.session_state[_synced_key] = True

try:
    df = _load_df(user_id)
except RuntimeError as e:
    st.error(str(e))
    st.stop()


# ── Header ────────────────────────────────────────────────────────────────
_known_users = db.get_all_user_ids() if not is_placeholder_token() else []
ui.render_header(
    user_id=user_id,
    on_change=_set_user,
    on_refresh=_on_refresh,
    is_placeholder=is_placeholder_token(),
    known_users=_known_users,
)


# ── Tab router ────────────────────────────────────────────────────────────
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
    "Data synced incrementally · Rankings and WOD cached 24 hours · "
    "Built with Streamlit + Altair"
)
