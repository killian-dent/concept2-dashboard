import os


def _secret(key: str, default: str = "") -> str:
    """Read from st.secrets (Streamlit Cloud), then env vars, then default."""
    try:
        import streamlit as st
        return str(st.secrets.get(key, os.environ.get(key, default)))
    except Exception:
        return os.environ.get(key, default)


API_TOKEN = _secret("API_TOKEN", "YOUR_TOKEN_HERE")

_uid = _secret("USER_ID", "")
USER_ID = int(_uid) if _uid.isdigit() else None

API_BASE_URL = "https://log.concept2.com/api"
API_VERSION = "v1"

RESULTS_PER_PAGE = 100

STANDARD_DISTANCES = {
    "100m":          100,
    "500m":          500,
    "1000m":         1000,
    "2000m":         2000,
    "5000m":         5000,
    "6000m":         6000,
    "10000m":        10000,
    "Half Marathon": 21097,
    "Marathon":      42195,
}

STANDARD_TIMED = {
    "30 min": 1800,
    "60 min": 3600,
}


def is_placeholder_token() -> bool:
    return API_TOKEN in ("YOUR_TOKEN_HERE", "", None)
