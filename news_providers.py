"""News ingestion for the internal Audit Intelligence dashboard.

NewsAPI remains the primary API for this repository. Google News RSS is only
used as a no-key fallback/supplement when NewsAPI returns no usable stories.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

DEFAULT_TIMEOUT = 15

PROVIDERS = {
    "newsapi": {
        "label": "NewsAPI",
        "endpoint": "https://newsapi.org/v2/everything",
    },
    "google_rss": {
        "label": "Google News RSS",
        "endpoint": "https://news.google.com/rss/search",
    },
}

QUERIES = {
    "Transformation": [
        'banking AND ("digital transformation" OR "digital banking" OR "core banking")',
        'banking AND ("artificial intelligence" OR automation OR cloud)',
    ],
    "Regulation": [
        'banking AND (regulation OR regulatory OR compliance OR supervision)',
        'banking AND (AML OR KYC OR sanctions OR enforcement OR penalty)',
    ],
    "People": [
        'bank AND (CEO OR CFO OR "chief risk officer" OR "chief audit")',
        'bank AND (appointed OR appointment OR resignation OR leadership)',
    ],
    "Cyber & Tech": [
        'banking AND (cybersecurity OR "cyber attack" OR ransomware OR "data breach")',
        'banking AND (technology OR "artificial intelligence" OR fraud)',
    ],
    "Global Banks": [
        '(HSBC OR JPMorgan OR Barclays OR "Deutsche Bank" OR Citigroup OR Citi)',
        '("Bank of America" OR "Wells Fargo" OR UBS OR Santander OR "Goldman Sachs")',
    ],
}

CATEGORY_NAMES = list(QUERIES.keys())

QUOTA_MARKERS = (
    "quota", "rate limit", "ratelimited", "too many requests",
    "exhausted", "limit reached", "limit exceeded", "apikeyexhausted", "429",
)


class QuotaExhausted(RuntimeError):
    pass


def blank(value):
    return str(value).strip() if value is not None else ""


def is_quota_error(message, status=None):
    if status == 429:
        return True
    text = str(message or "").lower()
    return any(marker in text for marker in QUOTA_MARKERS)


def fetch_newsapi(query, api_key, lookback_days):
    """Fetch one NewsAPI Everything query.

    NewsAPI supports q/from/to/language/sortBy/pageSize on /v2/everything.
    The key is sent in X-Api-Key rather than in the URL.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    from_date = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    response = requests.get(
        PROVIDERS["newsapi"]["endpoint"],
        params={
            "q": query[:500],
            "from": from_date,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 100,
            "page": 1,
        },
        headers={"X-Api-Key": api_key},
        timeout=DEFAULT_TIMEOUT,
    )

    try:
        payload = response.json()
    except Exception:
        payload = {}

    if payload.get("status") != "ok":
        message = payload.get("message") or f"HTTP {response.status_code}"
        code = payload.get("code", "")
        if is_quota_error(f"{code} {message}", response.status_code):
            raise QuotaExhausted(message)
        raise RuntimeError(message)

    rows = []
    for item in payload.get("articles", []) or []:
        source = item.get("source") or {}
        rows.append({
            "title": blank(item.get("title")),
            "description": blank(item.get("description")),
            "content": blank(item.get("content")),
            "url": blank(item.get("url")),
            "image_url": blank(item.get("urlToImage")),
            "source": blank(source.get("name")) or "NewsAPI",
            "published_at": blank(item.get("publishedAt")),
            "author": blank(item.get("author")),
        })

    return rows


def fetch_google_rss(query, lookback_days):
    """No-key fallback/supplement used only to keep the internal feed alive."""
    q = f"{query} when:{max(1, min(30, int(lookback_days)))}d"
    response = requests.get(
        PROVIDERS["google_rss"]["endpoint"],
        params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
        headers={"User-Agent": "Mozilla/5.0 Audit-Intelligence/1.0"},
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    rows = []

    for item in root.findall("./channel/item"):
        title = blank(item.findtext("title"))
        link = blank(item.findtext("link"))
        if not title or not link:
            continue

        description = re.sub(
            r"<[^>]+>", " ", blank(item.findtext("description"))
        )
        source_node = item.find("source")
        source = (
            blank(source_node.text)
            if source_node is not None
            else ""
        ) or "Google News"

        rows.append({
            "title": title,
            "description": re.sub(r"\\s+", " ", description).strip(),
            "content": "",
            "url": link,
            "image_url": "",
            "source": source,
            "published_at": blank(item.findtext("pubDate")),
            "author": "",
        })

    return rows


TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "icid")
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "after", "over", "amid", "says", "say", "new", "news", "report",
}


def canonical_url(url):
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        keep = [
            (k, v) for k, v in parse_qsl(p.query or "")
            if not any(k.lower().startswith(x) for x in TRACKING)
        ]
        return urlunparse(
            ("", host, p.path.rstrip("/"), "", urlencode(sorted(keep)), "")
        ).lower()
    except Exception:
        return url.strip().lower()


def normalize_title(title):
    text = re.sub(r"[^a-z0-9 ]+", " ", blank(title).lower())
    return re.sub(r"\s+", " ", text).strip()


def tokens(title):
    return frozenset(
        x for x in normalize_title(title).split()
        if x not in STOPWORDS and len(x) > 2
    )


def similarity(a, b):
    ta, tb = a["_tokens"], b["_tokens"]
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return max(
        inter / len(ta | tb),
        0.92 * inter / min(len(ta), len(tb)),
        SequenceMatcher(None, a["_norm"], b["_norm"]).ratio(),
    )


def merge(keep, other):
    keep["providers"] = set(keep.get("providers", set())) | set(
        other.get("providers", set())
    )
    for key in ("image_url", "author", "content"):
        if not keep.get(key) and other.get(key):
            keep[key] = other[key]
    if len(other.get("description", "")) > len(keep.get("description", "")):
        keep["description"] = other["description"]


def deduplicate(records, threshold=0.78):
    stats = {"by_url": 0, "by_title": 0, "by_fuzzy": 0}
    by_url, by_title, survivors = {}, {}, []

    for row in records:
        title = blank(row.get("title"))
        if not title or title.lower().startswith("[removed]"):
            continue

        row["providers"] = set(row.get("providers", set()))
        cu = canonical_url(row.get("url", ""))
        nt = normalize_title(title)

        if cu and cu in by_url:
            merge(by_url[cu], row)
            stats["by_url"] += 1
            continue
        if nt and nt in by_title:
            merge(by_title[nt], row)
            stats["by_title"] += 1
            continue

        row["_norm"] = nt
        row["_tokens"] = tokens(title)
        survivors.append(row)
        if cu:
            by_url[cu] = row
        if nt:
            by_title[nt] = row

    final = []
    for row in survivors:
        match = None
        best = 0.0
        for existing in final:
            score = similarity(row, existing)
            if score >= threshold and score > best:
                best, match = score, existing
        if match:
            merge(match, row)
            stats["by_fuzzy"] += 1
        else:
            final.append(row)

    for row in final:
        row.pop("_norm", None)
        row.pop("_tokens", None)
        row["providers"] = sorted(row.get("providers", set()))

    return final, stats


def fetch_all(
    api_keys,
    lookback_days=7,
    categories=None,
    fuzzy_threshold=0.72,
    max_workers=5,
):
    """NewsAPI-first ingestion with RSS fallback.

    NewsAPI is always attempted first. RSS is used when NewsAPI is unavailable
    or when it returns no usable articles. This preserves NewsAPI as the
    internal repository's news API while preventing an empty dashboard.
    """
    per_provider = {
        "newsapi": {"requests": 0, "articles": 0, "errors": 0, "quota_hits": 0},
        "google_rss": {"requests": 0, "articles": 0, "errors": 0, "quota_hits": 0},
    }
    errors = []
    raw = []

    jobs = [
        (category, query)
        for category, queries in QUERIES.items()
        if not categories or category in categories
        for query in queries
    ]

    key = blank(api_keys.get("newsapi"))

    if key:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(fetch_newsapi, query, key, lookback_days): (category, query)
                for category, query in jobs
            }
            for future in as_completed(futures):
                category, query = futures[future]
                per_provider["newsapi"]["requests"] += 1
                try:
                    rows = future.result()
                    per_provider["newsapi"]["articles"] += len(rows)
                    for row in rows:
                        row["category_hint"] = category
                        row["providers"] = {"newsapi"}
                        raw.append(row)
                except QuotaExhausted as exc:
                    per_provider["newsapi"]["quota_hits"] += 1
                    if not any("NewsAPI" in e for e in errors):
                        errors.append(f"NewsAPI quota: {exc}")
                except Exception as exc:
                    per_provider["newsapi"]["errors"] += 1
                    errors.append(f"NewsAPI · {category}: {exc}")
    else:
        errors.append("NewsAPI key is not configured in the server environment or Streamlit Secrets.")

    # Supplement only when NewsAPI produced nothing. This avoids doubling
    # traffic during normal operation and gives the dashboard a public fallback.
    if not raw:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(fetch_google_rss, query, lookback_days): (category, query)
                for category, query in jobs
            }
            for future in as_completed(futures):
                category, query = futures[future]
                per_provider["google_rss"]["requests"] += 1
                try:
                    rows = future.result()
                    per_provider["google_rss"]["articles"] += len(rows)
                    for row in rows:
                        row["category_hint"] = category
                        row["providers"] = {"google_rss"}
                        raw.append(row)
                except Exception as exc:
                    per_provider["google_rss"]["errors"] += 1
                    errors.append(f"Google News RSS · {category}: {exc}")

    unique, dedup = deduplicate(raw, threshold=fuzzy_threshold)

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    filtered = []
    for row in unique:
        value = row.get("published_at")
        if not value:
            filtered.append(row)
            continue
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                filtered.append(row)
        except Exception:
            filtered.append(row)

    return filtered, errors, {
        "per_provider": per_provider,
        "raw": len(raw),
        "unique": len(unique),
        "retained": len(filtered),
        "dedup": dedup,
        "active": ["newsapi"] + (["google_rss"] if not key or not raw else []),
    }
