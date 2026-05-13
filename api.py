"""
Concept2 Logbook API client.

Real API docs: https://log.concept2.com/developers/documentation/

Key facts about the live API:
  - Base path: https://log.concept2.com/api  (NO /v1/ in the URL)
  - API version goes in the Accept header: application/vnd.c2logbook.v1+json
  - Pagination param is "number" (not "per_page"), max 250
  - Result "time" field is in tenths of seconds
  - Workout intervals are under result["workout"]["intervals"]

To activate live data:
  1. Complete OAuth2 flow to obtain a Bearer token (see README.md)
  2. Replace API_TOKEN in config.py with your token
  3. Set USER_ID in config.py to your numeric Concept2 user ID
"""

import re
import threading
import time as _time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import config
from config import API_TOKEN, API_BASE_URL, API_VERSION, RESULTS_PER_PAGE, is_placeholder_token
from data import generate_sample_results, pace_from_time_distance, watts_from_pace, format_pace


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        # Version is declared in the Accept header, NOT in the URL path
        "Accept": f"application/vnd.c2logbook.{API_VERSION}+json",
    }


def _get(endpoint: str, params: Optional[dict] = None) -> dict:
    """Single authenticated GET. Raises on HTTP errors."""
    # Correct URL: /api/{endpoint}  — no version prefix in path
    url = f"{API_BASE_URL}/{endpoint}"
    resp = requests.get(url, headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _paginate(endpoint: str, extra_params: Optional[dict] = None) -> list[dict]:
    """Fetch all pages and return the combined data list."""
    # Pagination param is "number" per the API docs (not "per_page")
    params = {"number": RESULTS_PER_PAGE, **(extra_params or {})}
    results = []
    page = 1
    while True:
        params["page"] = page
        body = _get(endpoint, params)
        data = body.get("data", [])
        results.extend(data)
        pagination = body.get("meta", {}).get("pagination", {})
        if page >= pagination.get("total_pages", 1):
            break
        page += 1
        _time.sleep(0.1)
    return results


# ---------------------------------------------------------------------------
# Result normalisation
# ---------------------------------------------------------------------------

def _label(r: dict) -> str:
    """Human-readable label for a result based on workout_type and distance/time."""
    wt = r.get("workout_type", "")
    dist = r.get("distance", 0)
    time_s = r.get("time", 0) / 10.0

    if wt in ("FixedDistRow", "FixedDistSki", "FixedDistBike", "FixedDistDynRow"):
        return f"{dist:,}m"
    if wt in ("FixedTimeRow", "FixedTimeSki", "FixedTimeBike", "FixedTimeDynRow"):
        mins = int(time_s // 60)
        return f"{mins} min"
    if wt == "JustRow":
        return f"{dist:,}m free row"
    if wt == "VariableInterval":
        return "Intervals"
    if wt == "FixedCalRow":
        return f"{r.get('calories_total', 0)} cal"
    return f"{dist:,}m" if dist else wt


def _normalize(r: dict) -> dict:
    """
    Convert a raw Concept2 API result dict into our internal shape,
    which matches what generate_sample_results() produces.
    """
    time_tenths = r.get("time", 0)
    time_s = time_tenths / 10.0
    dist = r.get("distance", 0)
    pace_s = pace_from_time_distance(time_s, dist) if dist else 0.0

    wt = r.get("workout_type", "")
    is_timed = "FixedTime" in wt

    # Intervals → splits
    intervals = (r.get("workout") or {}).get("intervals", [])
    splits = []
    for i, iv in enumerate(intervals):
        iv_time_s = iv.get("time", 0) / 10.0
        iv_dist = iv.get("distance", 0)
        iv_pace = pace_from_time_distance(iv_time_s, iv_dist) if iv_dist else 0.0
        iv_hr = iv.get("heart_rate") or {}
        splits.append({
            "split_number": i + 1,
            "distance":      iv_dist,
            "time":          round(iv_time_s, 1),
            "pace":          iv_pace,
            "pace_formatted": format_pace(iv_pace),
            "spm":           iv.get("stroke_rate", 0),
            "watts":         round(watts_from_pace(iv_pace), 1) if iv_pace else 0,
            "heart_rate":    iv_hr.get("max", iv_hr.get("ending", 0)),
        })

    hr = r.get("heart_rate") or {}

    rest_dist  = r.get("rest_distance", 0) or 0
    rest_time_s = (r.get("rest_time", 0) or 0) / 10.0

    return {
        "id":           r["id"],
        "date":         r.get("date_utc") or r.get("date", ""),
        "date_display": (r.get("date") or "")[:10],
        "workout_type": r.get("type", "rower"),
        "workout": {
            "type":               "time" if is_timed else "distance",
            "label":              _label(r),
            "distance":           dist,
            "time":               time_tenths,
            "time_seconds":       time_s,
            "rest_distance":      rest_dist,
            "rest_time_seconds":  rest_time_s,
            "spm":                r.get("stroke_rate", 0),
            "heart_rate_average": hr.get("average", 0),
            "watts_average":      round(watts_from_pace(pace_s), 1) if pace_s else 0,
            "calories":           r.get("calories_total", 0),
            "drag_factor":        r.get("drag_factor", 0) or 0,
            "pace":               pace_s,
            "pace_formatted":     format_pace(pace_s),
            "splits":             splits,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_results(user_id: Optional[str] = None) -> list[dict]:
    """
    Return all workout results for the given user.

    Live path: GET /api/users/{user_id}/results  (paginated)
    user_id may be a numeric string or "me" (Concept2 API shorthand for the
    authenticated account).  Falls back to sample data when token is a placeholder.
    """
    if is_placeholder_token():
        return generate_sample_results()

    # ── REAL API CALL ──────────────────────────────────────────────────────
    if user_id is None:
        configured = getattr(config, "USER_ID", None)
        user_id = str(configured) if configured else "me"

    raw = _paginate(f"users/{user_id}/results")
    return [_normalize(r) for r in raw]


def fetch_result_detail(result_id: int, user_id: Optional[int] = None) -> dict:
    """
    Return full detail (including splits) for a single result.

    Live path: GET /api/users/{user_id}/results/{result_id}
    Falls back to the matching sample record when token is a placeholder.
    """
    if is_placeholder_token():
        for r in generate_sample_results():
            if r["id"] == result_id:
                return r
        return {}

    # ── REAL API CALL ──────────────────────────────────────────────────────
    if user_id is None:
        user_id = getattr(config, "USER_ID", None)
    body = _get(f"users/{user_id}/results/{result_id}")
    raw = body.get("data", {})
    return _normalize(raw) if raw else {}


def fetch_ranking(
    distance: int,
    year: int,
    gender: str = "M",
    machine: str = "rower",
    max_pages: int = 20,
) -> Optional[dict]:
    """
    Scrape the Concept2 rankings page to find this user's position.

    Searches up to max_pages (50 results each = top 1000 by default).
    Returns {"rank": int, "time": str} or None if not found.

    Identifies the user by their numeric USER_ID appearing in the row link
    as /individual/{USER_ID}, so it's robust against name changes.
    """
    user_id = getattr(config, "USER_ID", None)
    if not user_id:
        return None

    search_token = f"/individual/{user_id}"
    base_url = f"https://log.concept2.com/rankings/{year}/{machine}/{distance}"

    for page in range(1, max_pages + 1):
        try:
            resp = requests.get(
                base_url,
                params={"gender": gender, "page": page},
                timeout=10,
            )
            if resp.status_code != 200:
                break
            html = resp.text

            if search_token in html:
                idx = html.find(search_token)
                preceding = html[:idx]
                tr_ids = re.findall(r'<tr id="(\d+)">', preceding)
                if not tr_ids:
                    return None
                rank = int(tr_ids[-1])
                # Extract time from the same row
                row_start = preceding.rfind(f'<tr id="{tr_ids[-1]}">')
                row_end   = html.find('</tr>', row_start) + 5
                row_html  = html[row_start:row_end]
                time_match = re.search(r'<td[^>]*>\s*(\d+:\d+\.\d)\s*</td>', row_html)
                return {
                    "rank":     rank,
                    "time":     time_match.group(1) if time_match else "",
                    "page":     page,
                    "rankings_url": f"{base_url}?gender={gender}&page={page}",
                }

            if f"page={page + 1}" not in html:
                break

            _time.sleep(0.15)
        except Exception:
            break

    return None


def _extract_wod_row(html: str, search_token: str, base_url: str) -> Optional[dict]:
    """Parse rank and result cells from a WOD honorboard page that contains the user."""
    idx = html.find(search_token)
    if idx < 0:
        return None
    preceding = html[:idx]
    tr_ids = re.findall(r'<tr id="(\d+)">', preceding)
    if not tr_ids:
        return None
    rank = int(tr_ids[-1])

    row_start = preceding.rfind(f'<tr id="{tr_ids[-1]}">')
    row_end   = html.find('</tr>', row_start) + 5
    row_html  = html[row_start:row_end]
    cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
    cells_clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]

    page_nums = [int(p) for p in re.findall(r'page=(\d+)', html)]
    total_pages = max(page_nums) if page_nums else 1

    return {
        "rank":   rank,
        "total":  total_pages * 50,
        "result": cells_clean[-2] if len(cells_clean) >= 2 else "",
        "pace":   cells_clean[-1] if cells_clean else "",
        "url":    base_url,
    }


def fetch_wod_ranking(date: str, machine: str = "rowerg") -> Optional[dict]:
    """
    Scrape the Concept2 WOD honorboard for a given date and find the user's position.

    date:    YYYY-MM-DD
    machine: rowerg | skierg | bikeerg

    WOD boards use /profile/{user_id} links (unlike standard rankings which use
    /individual/{user_id}), so this is a separate scraper.

    Fetches page 1 sequentially to discover the total page count, then scans
    all remaining pages in parallel (8 workers) and returns as soon as the user
    is found — typically 3-5x faster than sequential for large boards.

    Returns {"rank": int, "result": str, "pace": str, "total": int, "url": str}
    or None if not found.
    """
    user_id = getattr(config, "USER_ID", None)
    if not user_id:
        return None

    search_token = f"/profile/{user_id}"
    base_url = f"https://log.concept2.com/wod/{date}/{machine}"

    # Page 1: check for user and discover total page count
    try:
        resp = requests.get(base_url, params={"page": 1}, timeout=10)
        if resp.status_code != 200:
            return None
    except Exception:
        return None

    html = resp.text
    if search_token in html:
        return _extract_wod_row(html, search_token, base_url)

    page_nums = [int(p) for p in re.findall(r'page=(\d+)', html)]
    total_pages = max(page_nums) if page_nums else 1
    if total_pages <= 1:
        return None

    # Scan remaining pages in parallel. as_completed lets us return the moment
    # the user's page finishes — we don't wait for the other in-flight requests.
    stop = threading.Event()

    def check_page(page: int) -> Optional[str]:
        if stop.is_set():
            return None
        try:
            r = requests.get(base_url, params={"page": page}, timeout=10)
            if r.status_code == 200 and search_token in r.text:
                return r.text
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(check_page, p): p for p in range(2, total_pages + 1)}
        for future in as_completed(futures):
            html = future.result()
            if html:
                stop.set()
                return _extract_wod_row(html, search_token, base_url)

    return None


def fetch_challenges() -> list[dict]:
    """
    Return currently active Concept2 challenges.

    Live path: GET /api/challenges/current  (no auth required, but token is fine)
    Returns empty list on error so the UI degrades gracefully.
    """
    try:
        body = _get("challenges/current")
        return body.get("data", [])
    except Exception:
        return []


def fetch_profile() -> dict:
    """
    Return the authenticated user's profile.

    Live path: GET /api/users/{USER_ID}
    """
    if is_placeholder_token():
        return {"id": 0, "username": "Sample Athlete", "first_name": "Sample",
                "last_name": "Athlete", "email": ""}

    # ── REAL API CALL ──────────────────────────────────────────────────────
    user_id = getattr(config, "USER_ID", None)
    if not user_id:
        return {}
    body = _get(f"users/{user_id}")
    return body.get("data", {})
