"""Turn snapshots into market intelligence.

Reads  data/snapshots/*.json, data/downloads/*.json, data/reviews/*.json
Writes docs/data/latest.json  (feeds the dashboard)
       docs/data/history.json (one row per plugin per snapshot – for trend lines)
       reports/YYYY-MM-DD.md  (human-readable weekly/monthly digest)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .collect import ROOT, SNAP_DIR, DL_DIR, REV_DIR, load_json, save_json

DOCS = Path(os.environ.get("RADAR_DOCS", Path(__file__).resolve().parent.parent / "docs"))
REPORTS = Path(os.environ.get("RADAR_REPORTS", Path(__file__).resolve().parent.parent / "reports"))

TOP = 25
COMPLAINT_WORDS = re.compile(
    r"\b(bug|broken|crash|doesn.?t work|not work|slow|conflict|error|missing|wish|would be (nice|great)|"
    r"please add|need(s)? to|should|can.?t|unable|paid|premium|upsell|annoying|spam|ads?)\b", re.I)


# --------------------------------------------------------------- loading

def list_snapshots() -> list[str]:
    return sorted(p.stem for p in SNAP_DIR.glob("????-??-??.json"))


def load_snapshot(day: str) -> dict:
    return load_json(SNAP_DIR / f"{day}.json", None)


def baseline_for(days_back: int, latest: str, available: list[str]) -> str | None:
    """Snapshot closest to `days_back` days before `latest` (tolerance: half the period)."""
    latest_d = date.fromisoformat(latest)
    target = latest_d - timedelta(days=days_back)
    tol = max(2, days_back // 2)
    cands = []
    for d in available:
        dd = date.fromisoformat(d)
        if dd < latest_d and abs((dd - target).days) <= tol:
            cands.append((abs((dd - target).days), d))
    return min(cands)[1] if cands else None


def days_between(a: str, b: str) -> int:
    return max(1, abs((date.fromisoformat(b) - date.fromisoformat(a)).days))


# --------------------------------------------------------------- metrics

def pct(new: float, old: float) -> float | None:
    if old <= 0:
        return None
    return round((new - old) / old * 100, 1)


def plugin_row(p: dict, latest_day: str) -> dict:
    added = p.get("added") or ""
    days_old = None
    if added:
        try:
            days_old = max(1, (date.fromisoformat(latest_day) - date.fromisoformat(added)).days)
        except ValueError:
            pass
    return {
        "slug": p["slug"], "name": p["name"], "author": p["author"],
        "active_installs": p["active_installs"], "downloaded": p["downloaded"],
        "rating": p["rating"], "num_ratings": p["num_ratings"],
        "support_threads": p["support_threads"],
        "support_threads_resolved": p["support_threads_resolved"],
        "added": added, "last_updated": p.get("last_updated"), "days_old": days_old,
        "downloads_per_day_lifetime": round(p["downloaded"] / days_old, 1) if days_old else None,
        "tags": p.get("tags", []), "short_description": p.get("short_description", ""),
        "url": f"https://wordpress.org/plugins/{p['slug']}/",
    }


def compare(latest: dict, base: dict | None, days: int) -> list[dict]:
    """Delta rows for plugins present in both snapshots."""
    if not base:
        return []
    days = days_between(base["date"], latest["date"])  # real gap, not nominal
    rows = []
    for slug, p in latest["plugins"].items():
        b = base["plugins"].get(slug)
        if not b:
            continue
        d_dl = p["downloaded"] - b["downloaded"]
        d_ai = p["active_installs"] - b["active_installs"]
        d_rt = p["num_ratings"] - b["num_ratings"]
        if d_dl <= 0 and d_ai <= 0 and d_rt <= 0:
            continue
        row = plugin_row(p, latest["date"])
        row.update({
            "period_days": days,
            "delta_downloaded": d_dl,
            "delta_downloaded_pct": pct(p["downloaded"], b["downloaded"]),
            "delta_active_installs": d_ai,
            "delta_active_installs_pct": pct(p["active_installs"], b["active_installs"]),
            "delta_num_ratings": d_rt,
            "downloads_per_day": round(d_dl / max(1, days), 1),
        })
        rows.append(row)
    return rows


def rising_velocity(slugs: list[str]) -> list[dict]:
    """From daily download history: last-7-day average vs the 7 days before."""
    out = []
    for slug in slugs:
        hist = load_json(DL_DIR / f"{slug}.json", {})
        if len(hist) < 8:
            continue
        days = sorted(hist)[-14:]
        recent = [hist[d] for d in days[-7:]]
        prev = [hist[d] for d in days[:-7]] or [0]
        r, p = sum(recent) / len(recent), sum(prev) / len(prev)
        out.append({"slug": slug, "avg_last7": round(r, 1), "avg_prev7": round(p, 1),
                    "accel_pct": pct(r, p), "series": [[d, hist[d]] for d in days]})
    out.sort(key=lambda x: (x["accel_pct"] is None, -(x["accel_pct"] or 0)))
    return out


def review_intel(slugs: list[str], since_days: int = 30, today: str | None = None) -> list[dict]:
    """What people are saying – with 1-2★ complaints surfaced as feature gaps."""
    today_d = date.fromisoformat(today) if today else date.today()
    out = []
    for slug in slugs:
        data = load_json(REV_DIR / f"{slug}.json", None)
        if not data or not data.get("reviews"):
            continue
        revs = data["reviews"]
        recent = []
        for r in revs:
            fs = r.get("first_seen")
            try:
                if fs and (today_d - date.fromisoformat(fs)).days <= since_days:
                    recent.append(r)
            except ValueError:
                pass
        stars = Counter(r["stars"] for r in revs if r.get("stars"))
        complaints = [r for r in revs if (r.get("stars") or 5) <= 2 or COMPLAINT_WORDS.search(r.get("text", ""))]
        reviewers = Counter(r["author"] for r in revs if r.get("author"))
        out.append({
            "slug": slug,
            "reviews_total_seen": len(revs),
            "reviews_last_period": len(recent),
            "star_breakdown": {str(k): stars[k] for k in sorted(stars)},
            "top_reviewers": reviewers.most_common(5),
            "complaints": [{"title": r["title"], "stars": r.get("stars"), "author": r.get("author"),
                            "date": r.get("date"), "link": r.get("link"),
                            "snippet": r.get("text", "")[:300]} for r in complaints[:8]],
            "latest": [{"title": r["title"], "stars": r.get("stars"), "author": r.get("author"),
                        "date": r.get("date"), "link": r.get("link"),
                        "snippet": r.get("text", "")[:200]} for r in revs[:5]],
        })
    out.sort(key=lambda x: -x["reviews_last_period"])
    return out


def category_heat(new_entrants: list[dict], risers: list[dict]) -> list[dict]:
    """Which tags/categories are getting the most new plugins and the most growth."""
    stats = defaultdict(lambda: {"new_plugins": 0, "rising_plugins": 0,
                                 "delta_downloads": 0, "installs_of_new": 0, "examples": []})
    for r in new_entrants:
        for t in r["tags"][:6]:
            s = stats[t]
            s["new_plugins"] += 1
            s["installs_of_new"] += r["active_installs"]
            if len(s["examples"]) < 3:
                s["examples"].append(r["slug"])
    for r in risers:
        for t in r["tags"][:6]:
            s = stats[t]
            s["rising_plugins"] += 1
            s["delta_downloads"] += r["delta_downloaded"]
            if len(s["examples"]) < 3 and r["slug"] not in s["examples"]:
                s["examples"].append(r["slug"])
    rows = [{"tag": t, **v, "heat": v["new_plugins"] * 3 + v["rising_plugins"] * 2
             + v["delta_downloads"] / 1000} for t, v in stats.items()]
    rows.sort(key=lambda x: -x["heat"])
    return rows[:40]


def opportunities(new_entrants: list[dict], risers: list[dict], reviews: list[dict]) -> list[str]:
    """Plain-language hints for 'what should I build'. Heuristic, not magic."""
    hints = []
    risers = sorted(risers, key=lambda r: -r["delta_downloaded"])  # compare() output is unordered
    hot = sorted([r for r in risers if (r["delta_downloaded_pct"] or 0) >= 50 and r["delta_downloaded"] >= 500],
                 key=lambda r: -r["delta_downloaded_pct"])
    for r in hot[:5]:
        hints.append(f"'{r['name']}' grew {r['delta_downloaded_pct']}% in downloads "
                     f"({r['delta_downloaded']:+,}) – demand is proven in: {', '.join(r['tags'][:4])}.")
    weak = [r for r in risers if r["num_ratings"] >= 5 and 0 < r["rating"] <= 70]
    for r in weak[:4]:
        hints.append(f"'{r['name']}' is growing but only rated {r['rating']}/100 with "
                     f"{r['num_ratings']} ratings – users want it, but they are not happy. Build the better version.")
    unsupported = [r for r in risers if r["support_threads"] >= 10
                   and r["support_threads_resolved"] / max(1, r["support_threads"]) < 0.4]
    for r in unsupported[:3]:
        hints.append(f"'{r['name']}' has {r['support_threads']} open support threads, "
                     f"only {r['support_threads_resolved']} resolved – poor support is an opening.")
    for rv in reviews:
        if len(rv["complaints"]) >= 3:
            titles = "; ".join(c["title"] for c in rv["complaints"][:3])
            hints.append(f"Complaints on '{rv['slug']}': {titles}")
    return hints[:15]


# --------------------------------------------------------------- main build

def build(latest_day: str | None = None) -> dict:
    days = list_snapshots()
    if not days:
        raise SystemExit("no snapshots in data/snapshots – run collect first")
    latest_day = latest_day or days[-1]
    latest = load_snapshot(latest_day)
    b7, b30 = baseline_for(7, latest_day, days), baseline_for(30, latest_day, days)
    base7, base30 = (load_snapshot(b7) if b7 else None), (load_snapshot(b30) if b30 else None)

    rows = {s: plugin_row(p, latest_day) for s, p in latest["plugins"].items()}
    cutoff30 = (date.fromisoformat(latest_day) - timedelta(days=30)).isoformat()
    new_entrants = sorted([r for r in rows.values() if r["added"] and r["added"] >= cutoff30],
                          key=lambda r: (-r["active_installs"], -r["downloaded"]))

    cmp7, cmp30 = compare(latest, base7, 7), compare(latest, base30, 30)

    def top_abs(c):  # booming: biggest absolute download gain
        return sorted(c, key=lambda r: -r["delta_downloaded"])[:TOP]

    def top_pct(c, min_dl=100, min_delta=200):  # fast risers: biggest % gain with some base
        c = [r for r in c if r["downloaded"] - r["delta_downloaded"] >= min_dl and r["delta_downloaded"] >= min_delta]
        return sorted(c, key=lambda r: -(r["delta_downloaded_pct"] or 0))[:TOP]

    def top_reviews(c):
        return sorted([r for r in c if r["delta_num_ratings"] > 0], key=lambda r: -r["delta_num_ratings"])[:TOP]

    tracked = [p.stem for p in DL_DIR.glob("*.json")] if DL_DIR.exists() else []
    reviewed = [p.stem for p in REV_DIR.glob("*.json")] if REV_DIR.exists() else []
    risers_for_heat = top_pct(cmp30 or cmp7, min_dl=50, min_delta=100)
    reviews = review_intel(reviewed, 30, latest_day)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_snapshot": latest_day,
        "baseline_7d": b7, "baseline_30d": b30,
        "snapshots_available": len(days),
        "plugins_tracked": len(rows),
        "new_entrants": new_entrants[:60],
        "weekly": {"booming": top_abs(cmp7), "fast_risers": top_pct(cmp7),
                   "most_new_reviews": top_reviews(cmp7)},
        "monthly": {"booming": top_abs(cmp30), "fast_risers": top_pct(cmp30),
                    "most_new_reviews": top_reviews(cmp30)},
        "most_installed": sorted(rows.values(), key=lambda r: -r["active_installs"])[:TOP],
        "most_reviewed_all_time": sorted(rows.values(), key=lambda r: -r["num_ratings"])[:TOP],
        "download_velocity": rising_velocity(tracked)[:TOP],
        "reviews": reviews[:40],
        "category_heat": category_heat(new_entrants, risers_for_heat),
        "opportunities": opportunities(new_entrants, cmp30 or cmp7, reviews),
    }
    save_json(DOCS / "data" / "latest.json", result)

    # compact per-plugin history for trend lines (append-only)
    hist_path = DOCS / "data" / "history.json"
    hist = load_json(hist_path, {})
    for s, p in latest["plugins"].items():
        hist.setdefault(s, {})[latest_day] = [p["active_installs"], p["downloaded"], p["num_ratings"], p["rating"]]
    save_json(hist_path, hist)

    write_report(result)
    return result


# --------------------------------------------------------------- markdown

def _table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    if not rows:
        return "_no data yet (need an older snapshot to compare against)_\n"
    head = "| " + " | ".join(h for h, _ in cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = ""
    for r in rows:
        cells = []
        for _, k in cols:
            v = r.get(k)
            if k == "name":
                v = f"[{v}]({r['url']})"
            elif isinstance(v, (int, float)) and v is not None:
                v = f"{v:+,}" if k.startswith("delta") and not k.endswith("pct") else (f"{v:,}" if isinstance(v, int) else v)
            elif isinstance(v, list):
                v = ", ".join(v[:4])
            cells.append(str(v if v is not None else ""))
        body += "| " + " | ".join(cells) + " |\n"
    return head + body


def write_report(res: dict) -> Path:
    d = res["latest_snapshot"]
    md = [f"# WP Plugin Radar – {d}\n",
          f"Snapshots: {res['snapshots_available']} · plugins tracked today: {res['plugins_tracked']:,} · "
          f"7-day baseline: {res['baseline_7d'] or '—'} · 30-day baseline: {res['baseline_30d'] or '—'}\n"]

    if res["opportunities"]:
        md.append("## 💡 Opportunities spotted\n")
        md += [f"- {h}" for h in res["opportunities"]] + [""]

    md.append("## 🆕 New plugins (added in last 30 days) with the most traction\n")
    md.append(_table(res["new_entrants"][:20], [("Plugin", "name"), ("Age (d)", "days_old"),
              ("Active installs", "active_installs"), ("Downloads", "downloaded"),
              ("DL/day", "downloads_per_day_lifetime"), ("Ratings", "num_ratings"), ("Tags", "tags")]))

    for label, key in (("This week", "weekly"), ("This month", "monthly")):
        blk = res[key]
        md.append(f"## 🚀 {label}: fast risers (highest % download growth)\n")
        md.append(_table(blk["fast_risers"][:15], [("Plugin", "name"), ("Δ downloads", "delta_downloaded"),
                  ("Δ %", "delta_downloaded_pct"), ("Δ installs", "delta_active_installs"),
                  ("Rating", "rating"), ("Tags", "tags")]))
        md.append(f"## 🔥 {label}: booming (biggest absolute download gain)\n")
        md.append(_table(blk["booming"][:15], [("Plugin", "name"), ("Δ downloads", "delta_downloaded"),
                  ("DL/day", "downloads_per_day"), ("Active installs", "active_installs"), ("Rating", "rating")]))
        md.append(f"## ⭐ {label}: most new reviews\n")
        md.append(_table(blk["most_new_reviews"][:15], [("Plugin", "name"), ("Δ ratings", "delta_num_ratings"),
                  ("Total ratings", "num_ratings"), ("Rating", "rating")]))

    md.append("## 🏷️ Category heat (where new & rising plugins cluster)\n")
    md.append(_table(res["category_heat"][:20], [("Tag", "tag"), ("New plugins", "new_plugins"),
              ("Rising", "rising_plugins"), ("Δ downloads", "delta_downloads"), ("Examples", "examples")]))

    if res["reviews"]:
        md.append("## 🗣️ What users are saying (tracked plugins)\n")
        for rv in res["reviews"][:12]:
            md.append(f"### {rv['slug']} – {rv['reviews_last_period']} new reviews this month, "
                      f"stars seen: {rv['star_breakdown']}")
            for c in rv["complaints"][:4]:
                star = f"{c['stars']}★ " if c.get("stars") else ""
                md.append(f"- {star}**{c['title']}** ({c.get('author','')}): {c['snippet'][:160]}")
            md.append("")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f"{d}.md"
    out.write_text("\n".join(md), encoding="utf-8")
    (REPORTS / "latest.md").write_text("\n".join(md), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Analyse snapshots → dashboard data + markdown report")
    ap.add_argument("--date", default=None, help="analyse as of this snapshot date")
    a = ap.parse_args(argv)
    res = build(a.date)
    print(f"report: reports/{res['latest_snapshot']}.md · dashboard data: docs/data/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
