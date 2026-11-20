"""Generate realistic fake snapshots (3 dates: -31d, -8d, today) so the pipeline
can be exercised without network access. Shapes match the real WP.org API."""
from __future__ import annotations

import random
import random as _random
from datetime import date, timedelta
from pathlib import Path

from radar import collect, wporg

random.seed(7)
TAGS = ["seo", "woocommerce", "ai", "forms", "security", "performance", "cache", "chatgpt",
        "block", "gutenberg", "analytics", "booking", "membership", "popup", "email", "elementor"]


def fake_api_plugin(i: int, added: date, today: date, growth: float) -> dict:
    """A record the way api.wordpress.org returns it (then run through normalise)."""
    age = max(1, (today - added).days)
    random = _random.Random(i)  # per-plugin constants must be stable across snapshot dates
    base = int(random.lognormvariate(5, 1.4))  # downloads/day-ish
    downloaded = int(base * age * growth)
    ai_raw = int(downloaded * random.uniform(0.05, 0.4))
    # WP.org buckets active installs: 10, 20 ... 100, 200 ... 1000 ...
    bucket = 10
    while bucket * 10 <= ai_raw:
        bucket *= 10
    active = max(10, (ai_raw // bucket) * bucket) if ai_raw >= 10 else 0
    n_rat = int(downloaded / random.uniform(800, 4000))
    tags = random.sample(TAGS, k=random.randint(2, 5))
    return {
        "name": f"Plugin {i} &amp; Co" if i % 9 == 0 else f"Plugin {i}",
        "slug": f"plugin-{i}",
        "version": "1.0.%d" % (i % 7),
        "author": f'<a href="https://example.com">Dev {i % 40}</a>',
        "rating": random.choice([60, 72, 80, 88, 92, 96, 100]) if n_rat else 0,
        "ratings": {"1": n_rat // 10, "2": n_rat // 20, "3": n_rat // 10, "4": n_rat // 4, "5": n_rat // 2},
        "num_ratings": n_rat,
        "support_threads": random.randint(0, 40),
        "support_threads_resolved": random.randint(0, 20),
        "active_installs": active,
        "downloaded": downloaded,
        "last_updated": f"{today - timedelta(days=random.randint(0, 200)):%Y-%m-%d} 10:00am GMT",
        "added": added.isoformat(),
        "homepage": "https://example.com",
        "short_description": "Does something useful for WordPress sites.",
        "tags": {t: t.title() for t in tags},
        "requires": "6.0", "tested": "6.8", "requires_php": "7.4",
    }


def main() -> None:
    today = date.today()
    days = [today - timedelta(days=31), today - timedelta(days=8), today]
    # 120 plugins: 30 "new" (added in last 40 days), rest older
    added_dates = {}
    for i in range(120):
        if i < 30:
            added_dates[i] = today - timedelta(days=random.randint(1, 40))
        else:
            added_dates[i] = today - timedelta(days=random.randint(60, 2000))
    growth_rate = {i: random.choice([1.0, 1.02, 1.1, 1.5, 2.5]) for i in range(120)}

    for d in days:
        plugins, sources = {}, {"new": [], "popular": [], "updated": []}
        for i in range(120):
            if added_dates[i] > d:
                continue  # not launched yet on this date
            steps = (d - days[0]).days / 31
            g = growth_rate[i] ** steps
            p = wporg.normalise(fake_api_plugin(i, added_dates[i], d, g))
            plugins[p["slug"]] = p
            if i < 30:
                sources["new"].append(p["slug"])
            elif i < 80:
                sources["popular"].append(p["slug"])
            else:
                sources["updated"].append(p["slug"])
        snap = {"date": d.isoformat(), "collected_at": d.isoformat() + "T02:00:00+00:00",
                "sources": sources, "plugins": plugins}
        collect.save_json(collect.SNAP_DIR / f"{d:%Y-%m-%d}.json", snap)

    # download history + reviews for a few tracked plugins
    for i in (3, 5, 31, 40):
        slug = f"plugin-{i}"
        hist = {}
        for k in range(30, -1, -1):
            dd = today - timedelta(days=k)
            hist[dd.isoformat()] = int(random.uniform(50, 120) * (1.08 ** (30 - k) if i == 3 else 1))
        collect.save_json(collect.DL_DIR / f"{slug}.json", hist)
        xml = SAMPLE_FEED.format(slug=slug)
        revs = wporg.parse_review_feed(xml.encode())
        for n, r in enumerate(revs):
            r["stars"] = [5, 1, 2, 4, 5][n % 5]
            r["first_seen"] = (today - timedelta(days=n * 6)).isoformat()
        collect.save_json(collect.REV_DIR / f"{slug}.json", {"slug": slug, "reviews": revs})
    print(f"fixtures written to {collect.ROOT}")


SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>Reviews for {slug}</title>
<item><title>Great plugin, does the job</title><link>https://wordpress.org/support/topic/great-{slug}/</link>
<dc:creator>happyuser</dc:creator><pubDate>Mon, 10 Nov 2025 10:00:00 +0000</pubDate>
<guid>https://wordpress.org/support/topic/great-{slug}/</guid>
<content:encoded><![CDATA[<p>Works out of the box. Would be nice to have a dark mode.</p>]]></content:encoded></item>
<item><title>Broken after update</title><link>https://wordpress.org/support/topic/broken-{slug}/</link>
<dc:creator>angrydev</dc:creator><pubDate>Sun, 09 Nov 2025 10:00:00 +0000</pubDate>
<guid>https://wordpress.org/support/topic/broken-{slug}/</guid>
<content:encoded><![CDATA[<p>Conflict with WooCommerce checkout, site crash. Please add a fix.</p>]]></content:encoded></item>
<item><title>Too many upsells</title><link>https://wordpress.org/support/topic/upsell-{slug}/</link>
<dc:creator>freeuser</dc:creator><pubDate>Sat, 08 Nov 2025 10:00:00 +0000</pubDate>
<guid>https://wordpress.org/support/topic/upsell-{slug}/</guid>
<content:encoded><![CDATA[<p>Every screen has premium ads. Missing the one feature I need: CSV export.</p>]]></content:encoded></item>
<item><title>Solid</title><link>https://wordpress.org/support/topic/solid-{slug}/</link>
<dc:creator>agency_guy</dc:creator><pubDate>Fri, 07 Nov 2025 10:00:00 +0000</pubDate>
<guid>https://wordpress.org/support/topic/solid-{slug}/</guid>
<content:encoded><![CDATA[<p>Using on 20 client sites, fast support.</p>]]></content:encoded></item>
<item><title>Love it</title><link>https://wordpress.org/support/topic/love-{slug}/</link>
<dc:creator>happyuser</dc:creator><pubDate>Thu, 06 Nov 2025 10:00:00 +0000</pubDate>
<guid>https://wordpress.org/support/topic/love-{slug}/</guid>
<content:encoded><![CDATA[<p>Simple and clean.</p>]]></content:encoded></item>
</channel></rss>"""


if __name__ == "__main__":
    main()
