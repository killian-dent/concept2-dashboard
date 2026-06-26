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
import db
from config import API_TOKEN, API_BASE_URL, API_VERSION, RESULTS_PER_PAGE, is_placeholder_token
from data import generate_sample_results, pace_from_time_distance, watts_from_pace, format_pace


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# One shared connection pool (keep-alive) for every outbound request — removes
# the TLS handshake from each call. The semaphore is the single app-wide cap on
# concurrent requests against Concept2: views may parallelize lookups freely
# above it, and total in-flight requests never exceed _MAX_CONCURRENCY. Tune
# here if Concept2 ever shows strain. (~6 is under what one browser opens per
# host, and 24h caching keeps total volume tiny.)
_session = requests.Session()
_MAX_CONCURRENCY = 6
_request_slot = threading.Semaphore(_MAX_CONCURRENCY)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {API_TOKEN}",
        # Version is declared in the Accept header, NOT in the URL path
        "Accept": f"application/vnd.c2logbook.{API_VERSION}+json",
    }


def _get(endpoint: str, params: Optional[dict] = None) -> dict:
    """Single authenticated GET. Retries once on a transient (5xx/timeout/
    connection) error, then raises."""
    # Correct URL: /api/{endpoint}  — no version prefix in path
    url = f"{API_BASE_URL}/{endpoint}"
    for attempt in range(2):
        try:
            with _request_slot:
                resp = _session.get(url, headers=_headers(),
                                    params=params or {}, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            transient = status is None or status >= 500
            if attempt == 0 and transient:
                _time.sleep(0.5)
                continue
            raise


def _rate_limited_get(url: str, **kwargs) -> requests.Response:
    """Every site-scrape request goes through here so the shared semaphore caps
    app-wide concurrency regardless of how many threads call in."""
    with _request_slot:
        return _session.get(url, **kwargs)


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


def _paginate_incremental(endpoint: str, since_id: int, extra_params: Optional[dict] = None) -> list[dict]:
    """Fetch pages newest-first, stopping as soon as a result id <= since_id is seen."""
    params = {"number": RESULTS_PER_PAGE, **(extra_params or {})}
    results = []
    page = 1
    while True:
        params["page"] = page
        body = _get(endpoint, params)
        data = body.get("data", [])
        done = False
        for r in data:
            if r.get("id", 0) <= since_id:
                done = True
                break
            results.append(r)
        if done:
            break
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


def _category(workout_type: str) -> str:
    """Coarse training category from the Concept2 workout_type.

    Used by the plan-adherence views to tell interval days from steady pieces.
    'SteadyState' covers single time/distance pieces (the plan's easy/steady
    days); easy-vs-steady is refined later by HR/duration, not by type.
    """
    wt = workout_type or ""
    if wt == "VariableInterval" or "Interval" in wt:
        return "Interval"
    if wt == "JustRow":
        return "JustRow"
    return "SteadyState"


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
            "category":           _category(wt),
            "raw_type":           wt,
            "distance":           dist,
            "time":               time_tenths,
            "time_seconds":       time_s,
            "rest_distance":      rest_dist,
            "rest_time_seconds":  rest_time_s,
            "spm":                r.get("stroke_rate", 0),
            "stroke_count":       r.get("stroke_count", 0) or 0,
            "heart_rate_average": hr.get("average", 0),
            "heart_rate_min":     hr.get("min", 0) or 0,
            "heart_rate_max":     hr.get("max", 0) or 0,
            "heart_rate_ending":  hr.get("ending", 0) or 0,
            "heart_rate_recovery": hr.get("recovery", 0) or 0,
            "watts_average":      round(watts_from_pace(pace_s), 1) if pace_s else 0,
            "wattminutes":        r.get("wattminutes_total", 0) or 0,
            "calories":           r.get("calories_total", 0),
            "drag_factor":        r.get("drag_factor", 0) or 0,
            "comments":           r.get("comments") or "",
            "verified":           bool(r.get("verified", False)),
            "ranked":             bool(r.get("ranked", False)),
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


def fetch_results_incremental(
    user_id: Optional[str] = None,
    since_id: Optional[int] = None,
    updated_after: Optional[str] = None,
) -> list[dict]:
    """
    Return workouts to upsert since the last sync.

    Three modes, in priority order:
      • updated_after="YYYY-MM-DD": ask the API for every result created OR
        edited since that date and fetch all of them. This is the robust
        default — it catches edits to older workouts, which the id-based mode
        silently misses. The date filter keeps the page count tiny.
      • since_id: paginate newest-first and stop at the first id we already
        have (legacy fast path).
      • neither: full fetch.

    Falls back to sample data when the token is a placeholder.
    """
    if is_placeholder_token():
        return generate_sample_results()

    if user_id is None:
        configured = getattr(config, "USER_ID", None)
        user_id = str(configured) if configured else "me"

    endpoint = f"users/{user_id}/results"
    if updated_after is not None:
        raw = _paginate(endpoint, {"updated_after": updated_after})
    elif since_id is None:
        raw = _paginate(endpoint)
    else:
        raw = _paginate_incremental(endpoint, since_id)
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


# ---------------------------------------------------------------------------
# Per-stroke data (time-in-zone / decoupling enabler)
# ---------------------------------------------------------------------------

def _normalize_stroke(s: dict) -> dict:
    """Normalise one raw stroke sample to {t, distance, pace, spm, hr}.

    The strokes endpoint returns compact keys (t/d/p/spm/hr). Time and distance
    come in tenths (consistent with the rest of the API); we expose seconds and
    metres. Accepts a couple of key spellings defensively since the exact shape
    can only be confirmed against a live account — only t and hr are essential
    (they drive time-in-zone).
    """
    def g(*keys, default=0):
        for k in keys:
            v = s.get(k)
            if v is not None:
                return v
        return default

    t = g("t", "time") / 10.0
    d = g("d", "distance") / 10.0
    p = g("p", "pace") / 10.0
    return {
        "t":        round(t, 1),
        "distance": round(d, 1),
        "pace":     round(p, 1),
        "spm":      g("spm", "stroke_rate"),
        "hr":       g("hr", "heart_rate"),
    }


def _sample_strokes(result_id: int) -> list[dict]:
    """Synthesise a plausible stroke series for sample mode from the matching
    sample workout, so the time-in-zone / HR-trace UI works without a token."""
    import random
    for r in generate_sample_results():
        if r["id"] != result_id:
            continue
        w = r["workout"]
        total = w.get("time_seconds", 0) or 0
        if total <= 0:
            return []
        hr_avg = w.get("heart_rate_average", 0) or 0
        splits = w.get("splits", [])
        rnd = random.Random(result_id)
        out, t, step = [], 0.0, 5.0
        while t < total:
            frac = t / total
            sp = splits[min(int(frac * len(splits)), len(splits) - 1)] if splits else None
            pace = (sp or {}).get("pace", w.get("pace", 0))
            warm = min(1.0, t / 120.0)          # HR ramps up over first 2 min
            hr = hr_avg * (0.85 + 0.15 * warm) + rnd.uniform(-3, 3)
            out.append({
                "t":        round(t, 1),
                "distance": round(w.get("distance", 0) * frac, 1),
                "pace":     round(pace, 1),
                "spm":      max(0, w.get("spm", 20) + rnd.randint(-1, 1)),
                "hr":       int(hr),
            })
            t += step
        return out
    return []


def fetch_strokes(result_id: int, user_id: Optional[int] = None) -> list[dict]:
    """Return the per-stroke series for a result.

    Live path: GET /api/users/{user_id}/results/{result_id}/strokes
    Returns a list of {t, distance, pace, spm, hr}. Empty list when no stroke
    data exists for the result. Falls back to synthesised data in sample mode.
    """
    if is_placeholder_token():
        return _sample_strokes(result_id)

    if user_id is None:
        user_id = getattr(config, "USER_ID", None)
    try:
        body = _get(f"users/{user_id}/results/{result_id}/strokes")
    except Exception:
        return []
    raw = body.get("data", [])
    return [_normalize_stroke(s) for s in raw]


def cached_strokes(user_id, result_id: int) -> list[dict]:
    """Get-or-fetch the stroke series, persisting it in SQLite.

    Stroke data never changes once a workout is logged, so this is cached
    permanently (no TTL). An empty result is cached too, so we don't re-hit the
    API for workouts that have no stroke data.
    """
    uid = str(user_id)
    cached = db.strokes_get(uid, result_id)
    if cached is not None:
        return cached
    data = fetch_strokes(result_id, user_id)
    db.strokes_set(uid, result_id, data)
    return data


def fetch_ranking(
    distance: int,
    year: int,
    gender: str = "M",
    machine: str = "rower",
    max_pages: int = 20,
    hint_page: int = None,
) -> Optional[dict]:
    """
    Scrape the Concept2 rankings page to find this user's position.

    Searches up to max_pages (50 results each = top 1000 by default).
    Returns {"rank": int, "time": str, "page": int, "rankings_url": str} or None.

    Identifies the user by their numeric USER_ID appearing in the row link
    as /individual/{USER_ID}, so it's robust against name changes.

    hint_page: if provided, the search starts near that page (and its
    neighbours) before falling back to a full sequential scan. When None
    the behaviour is identical to the original sequential scan.
    """
    user_id = getattr(config, "USER_ID", None)
    if not user_id:
        return None

    search_token = f"/individual/{user_id}"
    base_url = f"https://log.concept2.com/rankings/{year}/{machine}/{distance}"

    if hint_page:
        sequential = False
        pages_to_try = []
        for offset in (0, -1, 1, -2, 2):
            p = hint_page + offset
            if 1 <= p <= max_pages and p not in pages_to_try:
                pages_to_try.append(p)
        pages_to_try += [p for p in range(1, max_pages + 1) if p not in pages_to_try]
    else:
        sequential = True
        pages_to_try = list(range(1, max_pages + 1))

    for page in pages_to_try:
        try:
            resp = _rate_limited_get(
                base_url,
                params={"gender": gender, "page": page},
                timeout=10,
            )
            if resp.status_code != 200:
                if sequential:
                    break
                else:
                    continue
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

            if sequential and f"page={page + 1}" not in html:
                break

            _time.sleep(0.15)
        except Exception:
            if sequential:
                break
            else:
                continue

    return None


def _extract_wod_row(html: str, search_token: str, base_url: str, page: int) -> Optional[dict]:
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
        "page":   page,
    }


def fetch_wod_ranking(date: str, machine: str = "rowerg", hint_page: int = None) -> Optional[dict]:
    """
    Scrape the Concept2 WOD honorboard for a given date and find the user's position.

    date:      YYYY-MM-DD
    machine:   rowerg | skierg | bikeerg
    hint_page: if provided, try this page and its immediate neighbours first
               (sequential, cheap) before falling back to the full scan.
               Speeds up repeat lookups for dates whose honorboard has barely
               changed since the last fetch.

    WOD boards use /profile/{user_id} links (unlike standard rankings which use
    /individual/{user_id}), so this is a separate scraper.

    Fetches page 1 sequentially to discover the total page count, then scans
    all remaining pages in parallel (4 workers) and returns as soon as the user
    is found — typically 3-5x faster than sequential for large boards.

    Returns {"rank": int, "result": str, "pace": str, "total": int, "url": str,
             "page": int}
    or None if not found.
    """
    user_id = getattr(config, "USER_ID", None)
    if not user_id:
        return None

    search_token = f"/profile/{user_id}"
    base_url = f"https://log.concept2.com/wod/{date}/{machine}"

    def _get_page(page: int) -> Optional[str]:
        """Fetch one honorboard page; return its html, or None on any failure."""
        try:
            r = _rate_limited_get(base_url, params={"page": page}, timeout=10)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
        return None

    def _total_pages(html: str) -> int:
        nums = [int(p) for p in re.findall(r'page=(\d+)', html)]
        return max(nums) if nums else 1

    checked: set = set()
    total_pages: Optional[int] = None

    # 1. Try the hint page and its immediate neighbours first (sequential, cheap).
    if hint_page and hint_page >= 1:
        for p in (hint_page, hint_page - 1, hint_page + 1):
            if p < 1 or p in checked:
                continue
            checked.add(p)
            html = _get_page(p)
            if html is None:
                continue
            if total_pages is None:
                total_pages = _total_pages(html)
            if search_token in html:
                return _extract_wod_row(html, search_token, base_url, p)
            _time.sleep(0.15)

    # 2. Page 1: check it (if not already) and make sure we know the page count.
    if 1 not in checked:
        checked.add(1)
        html = _get_page(1)
        if html is None:
            return None
        total_pages = _total_pages(html)
        if search_token in html:
            return _extract_wod_row(html, search_token, base_url, 1)

    if total_pages is None or total_pages <= 1:
        return None

    # 3. Parallel-scan the remaining pages (4 workers), skipping already-checked.
    stop = threading.Event()

    def check_page(page: int) -> Optional[tuple]:
        if stop.is_set():
            return None
        try:
            r = _rate_limited_get(base_url, params={"page": page}, timeout=10)
            if r.status_code == 200 and search_token in r.text:
                return (page, r.text)
        except Exception:
            pass
        return None

    remaining = [p for p in range(2, total_pages + 1) if p not in checked]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(check_page, p): p for p in remaining}
        for future in as_completed(futures):
            res = future.result()
            if res:
                stop.set()
                page, html = res
                return _extract_wod_row(html, search_token, base_url, page)

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
                "last_name": "Athlete", "email": "", "gender": "M"}

    # ── REAL API CALL ──────────────────────────────────────────────────────
    user_id = getattr(config, "USER_ID", None)
    if not user_id:
        return {}
    body = _get(f"users/{user_id}")
    return body.get("data", {})
