"""Collect one daily snapshot of the WordPress.org plugin market.

Writes:
  data/snapshots/YYYY-MM-DD.json     all plugins seen today (new + popular + updated lists)
  data/downloads/<slug>.json         daily download history for tracked plugins (merged over time)
  data/reviews/<slug>.json           latest reviews for tracked plugins (merged, de-duplicated)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import wporg

ROOT = Path(os.environ.get("RADAR_DATA", Path(__file__).resolve().parent.parent / "data"))
SNAP_DIR = ROOT / "snapshots"
DL_DIR = ROOT / "downloads"
REV_DIR = ROOT / "reviews"
WATCHLIST = ROOT / "watchlist.json"

# How much of each browse list to pull per run (250 per page).
LISTS = {"new": 4, "popular": 4, "updated": 4}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def load_json(path: Path, default):
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    tmp.replace(path)


def collect_snapshot(today: str | None = None, lists: dict | None = None) -> dict:
    today = today or date.today().isoformat()
    lists = lists or LISTS
    plugins: dict[str, dict] = {}
    sources: dict[str, list[str]] = {}
    for browse, pages in lists.items():
        log(f"fetching browse={browse} ({pages} pages)")
        slugs = []
        try:
            for p in wporg.iter_plugins(browse, max_pages=pages):
                if not p["slug"]:
                    continue
                plugins[p["slug"]] = p
                slugs.append(p["slug"])
        except (wporg.ApiError, ValueError) as e:  # keep what we got; don't lose the whole day
            log(f"  browse={browse} failed after {len(slugs)} plugins: {e}")
        sources[browse] = slugs
        log(f"  {len(slugs)} plugins")
    snap = {"date": today, "collected_at": datetime.now(timezone.utc).isoformat(),
            "sources": sources, "plugins": plugins}
    save_json(SNAP_DIR / f"{today}.json", snap)
    log(f"snapshot saved: {len(plugins)} unique plugins")
    return snap


def tracked_slugs(snap: dict, extra: list[str] | None = None, limit: int = 60) -> list[str]:
    """Which plugins deserve the expensive per-plugin calls (downloads + reviews)?
    = manual watchlist + new plugins that already have real traction + hottest 'updated'."""
    watch = load_json(WATCHLIST, {"slugs": []}).get("slugs", [])
    chosen = list(dict.fromkeys(watch + (extra or [])))
    new_with_traction = [
        s for s in snap["sources"].get("new", [])
        if snap["plugins"][s]["active_installs"] >= 100 or snap["plugins"][s]["num_ratings"] >= 3
    ]
    chosen += new_with_traction
    return list(dict.fromkeys(chosen))[:limit]


def collect_details(slugs: list[str], today: str | None = None,
                    reviews: bool = True, downloads: bool = True) -> None:
    today = today or date.today().isoformat()
    for i, slug in enumerate(slugs, 1):
        log(f"details {i}/{len(slugs)}: {slug}")
        if downloads:
            try:
                path = DL_DIR / f"{slug}.json"
                hist = load_json(path, {})
                hist.update(wporg.download_stats(slug, limit=30))
                save_json(path, hist)
            except (wporg.ApiError, ValueError) as e:  # ValueError: bad JSON / non-int counts
                log(f"  downloads failed: {e}")
        if reviews:
            try:
                path = REV_DIR / f"{slug}.json"
                existing = load_json(path, {"slug": slug, "reviews": []})
                seen = {r["guid"] for r in existing["reviews"]}
                fresh = [r for r in wporg.reviews(slug) if r["guid"] not in seen]
                for r in fresh:
                    r["first_seen"] = today
                existing["reviews"] = (fresh + existing["reviews"])[:300]
                save_json(path, existing)
                if fresh:
                    log(f"  {len(fresh)} new reviews")
            except (wporg.ApiError, Exception) as e:  # feed may 404 for brand-new plugins
                log(f"  reviews failed: {e}")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Collect a daily WP.org plugin snapshot")
    ap.add_argument("--date", default=None, help="override snapshot date (YYYY-MM-DD)")
    ap.add_argument("--pages", type=int, default=None, help="pages per browse list (250/page)")
    ap.add_argument("--no-details", action="store_true", help="skip downloads/reviews calls")
    ap.add_argument("--details-limit", type=int, default=60)
    a = ap.parse_args(argv)
    lists = {k: a.pages for k in LISTS} if a.pages else LISTS
    snap = collect_snapshot(a.date, lists)
    if not a.no_details:
        slugs = tracked_slugs(snap, limit=a.details_limit)
        collect_details(slugs, a.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
