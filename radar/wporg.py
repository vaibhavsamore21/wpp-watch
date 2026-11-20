"""Thin client for the public WordPress.org APIs. Standard library only.

Endpoints used:
  * Plugin info API   https://api.wordpress.org/plugins/info/1.2/
  * Download stats    https://api.wordpress.org/stats/plugin/1.0/downloads.php
  * Review feed (RSS) https://wordpress.org/support/plugin/<slug>/reviews/feed/
  * Review page HTML  https://wordpress.org/support/plugin/<slug>/reviews/   (star ratings per review)
"""
from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape

USER_AGENT = "wp-plugin-radar/0.1 (market research bot; github.com/YOUR-USER/wp-plugin-radar)"
PLUGIN_INFO = "https://api.wordpress.org/plugins/info/1.2/"
DOWNLOAD_STATS = "https://api.wordpress.org/stats/plugin/1.0/downloads.php"
REVIEWS_BASE = "https://wordpress.org/support/plugin/{slug}/reviews/"

# Fields we ask the API to include on every plugin record.
FIELDS = [
    "active_installs", "downloaded", "ratings", "rating", "num_ratings",
    "support_threads", "support_threads_resolved", "tags", "added",
    "last_updated", "short_description", "requires", "tested", "requires_php",
    "homepage",
]
NO_FIELDS = ["description", "sections", "banners", "icons", "screenshots",
             "compatibility", "donate_link", "contributors", "reviews"]

PAUSE = 0.6  # seconds between requests – be polite to wordpress.org


class ApiError(RuntimeError):
    pass


def _get(url: str, timeout: int = 30, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (OSError, http.client.HTTPException) as e:  # URLError/HTTPError/timeouts/resets
            if isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429:
                last = e
                break  # 404 etc. will not fix itself
            last = e
            time.sleep(2 * (attempt + 1))
    raise ApiError(f"GET failed after {retries} tries: {url} ({last})")


def _query_url(params: dict) -> str:
    # The API wants PHP-style nested params: request[browse]=new&request[fields][tags]=1
    flat = {"action": "query_plugins"}
    for k, v in params.items():
        flat[f"request[{k}]"] = v
    for f in FIELDS:
        flat[f"request[fields][{f}]"] = 1
    for f in NO_FIELDS:
        flat[f"request[fields][{f}]"] = 0
    return PLUGIN_INFO + "?" + urllib.parse.urlencode(flat)


def query_plugins(browse: str = "new", page: int = 1, per_page: int = 250,
                  search: str | None = None, tag: str | None = None) -> dict:
    """One page of plugins. browse ∈ {new, popular, updated, featured, beta}."""
    params = {"browse": browse, "page": page, "per_page": per_page}
    if search:
        params["search"] = search
        params.pop("browse")
    if tag:
        params["tag"] = tag
    data = json.loads(_get(_query_url(params)))
    time.sleep(PAUSE)
    return data


def iter_plugins(browse: str, max_pages: int = 4, per_page: int = 250):
    """Yield normalised plugin dicts across pages."""
    for page in range(1, max_pages + 1):
        data = query_plugins(browse=browse, page=page, per_page=per_page)
        for p in data.get("plugins", []):
            yield normalise(p)
        info = data.get("info", {})
        if page >= int(info.get("pages", 1) or 1):
            break


def plugin_info(slug: str) -> dict:
    flat = {"action": "plugin_information", "request[slug]": slug}
    for f in FIELDS:
        flat[f"request[fields][{f}]"] = 1
    for f in NO_FIELDS:
        flat[f"request[fields][{f}]"] = 0
    data = json.loads(_get(PLUGIN_INFO + "?" + urllib.parse.urlencode(flat)))
    time.sleep(PAUSE)
    return normalise(data)


def normalise(p: dict) -> dict:
    """Keep only the fields the radar uses, with stable types."""
    tags = p.get("tags") or {}
    if isinstance(tags, list):
        tags = {t: t for t in tags}
    ratings = p.get("ratings") or {}
    return {
        "slug": p.get("slug"),
        "name": unescape(p.get("name") or ""),
        "author": re.sub(r"<[^>]+>", "", unescape(p.get("author") or "")).strip(),
        "version": p.get("version"),
        "short_description": unescape(p.get("short_description") or ""),
        "active_installs": int(p.get("active_installs") or 0),
        "downloaded": int(p.get("downloaded") or 0),
        "rating": int(p.get("rating") or 0),            # 0-100
        "num_ratings": int(p.get("num_ratings") or 0),
        "ratings": {str(k): int(v) for k, v in ratings.items()},  # {"1":n,...,"5":n}
        "support_threads": int(p.get("support_threads") or 0),
        "support_threads_resolved": int(p.get("support_threads_resolved") or 0),
        "added": (p.get("added") or "")[:10],            # YYYY-MM-DD
        "last_updated": (p.get("last_updated") or "")[:10],
        "requires": p.get("requires"),
        "tested": p.get("tested"),
        "requires_php": p.get("requires_php"),
        "tags": sorted(str(k) for k in tags.keys()),
        "homepage": p.get("homepage") or "",
    }


def download_stats(slug: str, limit: int = 30) -> dict[str, int]:
    """Daily download counts: {"YYYY-MM-DD": n}."""
    url = DOWNLOAD_STATS + "?" + urllib.parse.urlencode({"slug": slug, "limit": limit})
    data = json.loads(_get(url))
    time.sleep(PAUSE)
    if not isinstance(data, dict):  # unknown slug -> [] / error payload
        return {}
    return {k: int(v) for k, v in data.items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(k)) and v is not None}


# ---------------------------------------------------------------- reviews

_TOPIC_SPLIT = re.compile(r'<(?:ul|li|article|div)[^>]*id="(?:bbp-topic|post)-\d+"', re.I)
_STARS_RE = re.compile(r'data-rating="(\d)"|wporg-ratings[^>]*title="(\d) out of 5 stars', re.I)
_LINK_RE = re.compile(r'href="(https://wordpress\.org/support/topic/[^"#?]+)', re.I)


def _norm_url(u: str) -> str:
    return u.strip().rstrip("/").lower()


def _review_ratings_from_html(html: str) -> dict[str, int]:
    """Best effort: map review topic URL -> star rating from the reviews page.
    The page is a bbPress topic list; each review block holds a `wporg-ratings` element
    with data-rating and the topic permalink. We split into blocks and read both,
    whatever order they appear in, so modest markup changes do not break it."""
    out = {}
    for block in _TOPIC_SPLIT.split(html)[1:]:
        s = _STARS_RE.search(block)
        l = _LINK_RE.search(block)
        if s and l:
            out[_norm_url(l.group(1))] = int(s.group(1) or s.group(2))
    return out


def reviews(slug: str, with_stars: bool = True) -> list[dict]:
    """Latest ~15 reviews for a plugin from its RSS feed (+ star rating from HTML)."""
    feed_url = REVIEWS_BASE.format(slug=slug) + "feed/"
    xml_bytes = _get(feed_url)
    time.sleep(PAUSE)
    items = parse_review_feed(xml_bytes)
    if with_stars and items:
        try:
            html = _get(REVIEWS_BASE.format(slug=slug)).decode("utf-8", "replace")
            time.sleep(PAUSE)
            stars = _review_ratings_from_html(html)
            for it in items:
                for key in (it.get("link"), it.get("guid")):
                    if key and _norm_url(key) in stars:
                        it["stars"] = stars[_norm_url(key)]
                        break
        except ApiError:
            pass
    return items


def parse_review_feed(xml_bytes: bytes) -> list[dict]:
    ns = {"dc": "http://purl.org/dc/elements/1.1/",
          "content": "http://purl.org/rss/1.0/modules/content/"}
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        body = item.findtext("content:encoded", namespaces=ns) or item.findtext("description") or ""
        body = re.sub(r"<[^>]+>", " ", unescape(body))
        body = re.sub(r"\s+", " ", body).strip()
        out.append({
            "title": unescape(title),
            "author": (item.findtext("dc:creator", namespaces=ns) or "").strip(),
            "date": (item.findtext("pubDate") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "guid": (item.findtext("guid") or item.findtext("link") or "").strip(),
            "text": body[:1500],
            "stars": None,
        })
    return out
