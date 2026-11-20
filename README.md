# WP Plugin Radar

Market-research tracker for the WordPress.org plugin directory. Every day it records a
snapshot of ~3,000 plugins (newest, most popular, recently updated), then compares
snapshots to answer:

| Question | Where it shows |
|---|---|
| What launched recently and already has traction? | **New** tab / "New plugins" in report |
| What grew fastest this week / month (in %)? | **Fast risers** |
| What is booming in absolute downloads? | **Booming** |
| What is getting the most new reviews, and who is reviewing? | **Most new reviews**, **Reviews** tab |
| Which categories/tags are heating up? | **Category heat** |
| What are users complaining about / wishing for? | **Reviews** tab → complaints & wishes |
| Where is the gap I could fill? | **Opportunities** (heuristic hints) |

Zero dependencies (Python 3.10+ standard library). Runs for free on GitHub Actions and
publishes a static dashboard on GitHub Pages. Nothing touches your WordPress site.

## Setup (10 minutes)

1. Create a new **private or public GitHub repo** and push this folder to it.
2. GitHub → repo **Settings → Pages** → Source: *Deploy from a branch* → Branch `main`, folder `/docs`.
3. GitHub → **Actions** tab → "Daily plugin radar" → **Run workflow** (first run takes ~10–15 min).
4. Open `https://<your-user>.github.io/<repo>/` – the dashboard.

The workflow runs every day at 08:00 IST and commits `data/`, `reports/` and `docs/data/`.
Weekly comparisons start after 7 days of snapshots, monthly after 30. Until then the
dashboard shows the "New" and "Top" tabs only.

## Run locally

```bash
python run.py demo                 # fake data in demo/ (git-ignored) → see the dashboard immediately
python -m http.server -d demo/docs 8080 # open http://localhost:8080
python run.py collect --no-details # real snapshot, lists only (~3 min)
python run.py daily                # full daily run (snapshot + downloads + reviews)
python run.py watch wpforms-lite seo-by-rank-math   # track specific competitors closely
```

`watch`ed plugins (plus any brand-new plugin that already has ≥100 installs or ≥3 ratings)
get the expensive per-plugin calls: 30-day daily download history and the latest reviews
with star ratings and reviewer names.

## How "trending" is measured

WordPress.org only exposes bucketed active-install counts (10, 20 … 100, 200 … 1,000 …),
so the primary growth signal is **`downloaded`** (exact, cumulative). We compute:

* `delta_downloaded` – downloads gained since the 7/30-day baseline snapshot
* `delta_downloaded_pct` – growth relative to the baseline (fast risers need ≥100 base downloads and ≥200 gained, to filter noise)
* `downloads_per_day` – velocity over the comparison period
* `delta_active_installs` – bucket jumps (a 1,000 → 2,000 jump is a strong signal)
* `delta_num_ratings` – review momentum
* `download_velocity` – for tracked plugins, last-7-day average vs previous 7 (acceleration)

## Layout

```
run.py                 CLI
radar/wporg.py         API client (plugin info, download stats, review RSS + star scrape)
radar/collect.py       daily snapshot → data/snapshots/YYYY-MM-DD.json
radar/analyze.py       snapshots → docs/data/latest.json + reports/YYYY-MM-DD.md
docs/index.html        dashboard (reads docs/data/latest.json, no build step)
data/watchlist.json    slugs you want tracked closely
tests/make_fixtures.py fake data generator used by `run.py demo`
.github/workflows/     daily cron
```

## Notes & limits

* Be polite: the client sleeps 0.6 s between calls. A daily run is ~15 list calls + ~3 calls per tracked plugin.
* Star ratings per review come from scraping the reviews page HTML (`data-rating`); if wordpress.org
  changes its markup, reviews still arrive from RSS but `stars` becomes `null`.
* `active_installs` buckets mean small plugins show `0` until they cross 10 installs.
* Snapshot files are ~2 MB/day; after a year prune `data/snapshots/` to weekly files if the repo gets heavy
  (`docs/data/history.json` already keeps a compact per-plugin series).
