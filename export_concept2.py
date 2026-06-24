#!/usr/bin/env python3
"""
Concept2 detailed workout exporter (CLI).

Downloads the most detailed workout data the Concept2 Logbook API exposes
(per-result detail, including every interval/split with its pace, stroke rate,
watts and heart rate) and writes it to two CSV files:

    workouts.csv  — one row per workout (summary fields)
    splits.csv    — one row per interval/split, keyed by workout_id

Detail granularity requires one API call per workout (GET .../results/{id}),
so to stay friendly to Concept2 the exporter reuses the project's SQLite store
(data/workouts.db): each run only fetches workouts it doesn't already hold in
full detail. A small delay is kept between detail calls.

Usage
-----
    # incremental (default): fetch only new / not-yet-detailed workouts
    python export_concept2.py

    # write somewhere else
    python export_concept2.py --out-dir /path/to/dir

    # ignore the cache and re-fetch full detail for every workout
    python export_concept2.py --full

Token / user id are read from .streamlit/secrets.toml (same as the dashboard),
so run this from the project root.
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

import config
import db
from api import fetch_results, fetch_results_incremental, fetch_result_detail

DEFAULT_OUT_DIR = (
    "/Users/killiandent/Library/Mobile Documents/iCloud~md~obsidian"
    "/Documents/rostrum/resources"
)

# Key under which we remember which workout ids we've fetched in full detail,
# so genuinely interval-less workouts aren't re-fetched on every run.
def _detailed_key(uid: str) -> str:
    return f"export:detailed_ids:{uid}"


def _load_detailed_ids(uid: str) -> set[int]:
    stored = db.cache_peek(_detailed_key(uid))
    return set(stored) if isinstance(stored, list) else set()


def _save_detailed_ids(uid: str, ids: set[int]) -> None:
    db.cache_set(_detailed_key(uid), sorted(ids))


def _detail(result_id: int, uid: str, delay: float) -> dict:
    """Fetch one full-detail result, pausing first to be gentle on the API."""
    time.sleep(delay)
    return fetch_result_detail(result_id, user_id=int(uid))


def sync(uid: str, full: bool, delay: float) -> None:
    """Bring the local store up to full-detail for this user."""
    detailed = _load_detailed_ids(uid)

    if full:
        print("Full refresh: fetching the complete result list…")
        summaries = fetch_results(uid)
        targets = [r["id"] for r in summaries]
        detailed = set()  # force re-fetch of everything
    else:
        newest = db.get_newest_id(uid)
        if newest is None:
            print("Empty cache: fetching the complete result list…")
            summaries = fetch_results(uid)
        else:
            print(f"Incremental: looking for workouts newer than id {newest}…")
            summaries = fetch_results_incremental(uid, since_id=newest)
        new_ids = [r["id"] for r in summaries]
        if new_ids:
            db.upsert(uid, summaries)  # store summaries now; detail fills in below
        # Anything in the store without confirmed full detail (incl. rows the
        # dashboard may have stored summary-only) gets a detail fetch.
        cached_ids = [w["id"] for w in db.get_all(uid)]
        targets = [i for i in cached_ids if i not in detailed] + [
            i for i in new_ids if i not in detailed
        ]
        targets = list(dict.fromkeys(targets))  # de-dupe, keep order

    if not targets:
        print("Already up to date — nothing to fetch.")
        return

    print(f"Fetching full detail for {len(targets)} workout(s)…")
    fetched = []
    for n, rid in enumerate(targets, 1):
        detail = _detail(rid, uid, delay)
        if detail:
            fetched.append(detail)
            detailed.add(rid)
        if n % 25 == 0 or n == len(targets):
            print(f"  …{n}/{len(targets)}")
    if fetched:
        db.upsert(uid, fetched)
    _save_detailed_ids(uid, detailed)
    db.set_synced(uid)


def build_frames(uid: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Turn the cached workouts into a workout-level and a split-level frame."""
    workouts = db.get_all(uid)  # newest first
    work_rows, split_rows = [], []
    for r in workouts:
        w = r.get("workout", {})
        wid = r["id"]
        date_display = r.get("date_display") or (r.get("date") or "")[:10]
        splits = w.get("splits", []) or []
        work_rows.append({
            "id":              wid,
            "date":            r.get("date", ""),
            "date_display":    date_display,
            "machine":         r.get("workout_type", ""),
            "type":            w.get("type", ""),
            "label":           w.get("label", ""),
            "distance_m":      w.get("distance", 0),
            "time_s":          w.get("time_seconds", 0),
            "pace_s_per_500":  round(w.get("pace", 0), 2),
            "pace":            w.get("pace_formatted", ""),
            "spm":             w.get("spm", 0),
            "hr_avg":          w.get("heart_rate_average", 0),
            "watts_avg":       w.get("watts_average", 0),
            "calories":        w.get("calories", 0),
            "drag_factor":     w.get("drag_factor", 0),
            "rest_distance_m": w.get("rest_distance", 0),
            "rest_time_s":     w.get("rest_time_seconds", 0),
            "n_splits":        len(splits),
        })
        for s in splits:
            split_rows.append({
                "workout_id":     wid,
                "date_display":   date_display,
                "split_number":   s.get("split_number", 0),
                "distance_m":     s.get("distance", 0),
                "time_s":         s.get("time", 0),
                "pace_s_per_500": round(s.get("pace", 0), 2),
                "pace":           s.get("pace_formatted", ""),
                "spm":            s.get("spm", 0),
                "watts":          s.get("watts", 0),
                "heart_rate":     s.get("heart_rate", 0),
            })

    workouts_df = pd.DataFrame(work_rows)
    splits_df = pd.DataFrame(split_rows)
    return workouts_df, splits_df


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help="Directory to write workouts.csv and splits.csv into.")
    parser.add_argument("--full", action="store_true",
                        help="Ignore the cache and re-fetch full detail for every workout.")
    parser.add_argument("--delay", type=float, default=0.2,
                        help="Seconds to pause between detail API calls (default 0.2).")
    args = parser.parse_args()

    if config.is_placeholder_token():
        print("ERROR: no API token configured. Set API_TOKEN in "
              ".streamlit/secrets.toml (run from the project root).", file=sys.stderr)
        return 1

    uid = str(config.USER_ID) if config.USER_ID else "me"
    out_dir = Path(args.out_dir).expanduser()
    if not out_dir.is_dir():
        print(f"ERROR: output directory does not exist: {out_dir}", file=sys.stderr)
        return 1

    sync(uid, full=args.full, delay=args.delay)

    workouts_df, splits_df = build_frames(uid)
    if workouts_df.empty:
        print("No workouts found — nothing written.")
        return 0

    workouts_path = out_dir / "workouts.csv"
    splits_path = out_dir / "splits.csv"
    workouts_df.to_csv(workouts_path, index=False)
    splits_df.to_csv(splits_path, index=False)

    print(f"\nWrote {len(workouts_df)} workouts → {workouts_path}")
    print(f"Wrote {len(splits_df)} splits   → {splits_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
