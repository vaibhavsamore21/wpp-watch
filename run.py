#!/usr/bin/env python3
"""WP Plugin Radar CLI.

  python run.py collect              # fetch today's snapshot + details for tracked plugins
  python run.py collect --no-details # quick: lists only, no per-plugin calls
  python run.py analyze              # build docs/data/latest.json + reports/<date>.md
  python run.py daily                # collect then analyze (what the cron runs)
  python run.py watch <slug> ...     # add plugins to data/watchlist.json
  python run.py demo                 # fake snapshots in demo/ (never in data/) to preview the dashboard
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE / "demo"
sys.path.insert(0, str(HERE))
if len(sys.argv) > 1 and sys.argv[1] == "demo":
    # Fake data must never land in data/ docs/data/ reports/ – the cron commits those and
    # analyze would compare real snapshots against fake baselines. Must be set before import.
    os.environ["RADAR_DATA"] = str(DEMO / "data")
    os.environ["RADAR_DOCS"] = str(DEMO / "docs")
    os.environ["RADAR_REPORTS"] = str(DEMO / "reports")
from radar import collect, analyze  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "collect":
        return collect.main(rest)
    if cmd == "analyze":
        return analyze.main(rest)
    if cmd == "daily":
        collect.main(rest)
        return analyze.main([])
    if cmd == "watch":
        wl = collect.load_json(collect.WATCHLIST, {"slugs": []})
        wl["slugs"] = sorted(set(wl["slugs"]) | set(rest))
        collect.save_json(collect.WATCHLIST, wl)
        print("watching:", ", ".join(wl["slugs"]))
        return 0
    if cmd == "demo":
        from tests import make_fixtures
        shutil.rmtree(DEMO, ignore_errors=True)
        make_fixtures.main()
        rc = analyze.main([])
        shutil.copy(HERE / "docs" / "index.html", DEMO / "docs" / "index.html")
        print(f"demo dashboard: python -m http.server -d {DEMO / 'docs'} 8080")
        return rc
    print(f"unknown command {cmd!r}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
