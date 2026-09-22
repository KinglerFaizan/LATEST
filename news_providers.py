from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

DEFAULT_TIMEOUT = 12

PROVIDERS = {
    "google_rss": {"label": "Google News RSS", "endpoint": "https://news.google.com/rss/search"},
    "newsdata": {"label": "NewsData.io", "endpoint": "https://newsdata.io/api/1/latest"},
}

QUERIES = {
    "Transformation": [
        "banking digital transformation",
        "bank artificial intelligence",
        "bank digital banking",
    ],
    "Regulation": [
        "banking regulation",
        "banking compliance",
        "bank regulator",
    ],
    "People": [
        "bank CEO appointment",
        "bank leadership",
        "bank executive appointment",
    ],
    "Cyber & Tech": [
        "bank cybersecurity",
        "bank cyber attack",
        "bank technology fraud",
    ],
    "Global Banks": [
        "HSBC JPMorgan Barclays Deutsche Bank",
        "Bank of America Wells Fargo Citi UBS",
    ],
}

QUOTA_MARKERS = (
    "quota", "rate limit", "ratelimited", "too many requests",
    "exhausted", "limit reached", "limit exceeded", "apikeyexhausted", "429",
)


class QuotaExhausted(RuntimeError):
    pass


def blank(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "null", "nan", "n/a", "na"}:
        return ""
    return text


def is_quota_error(message, status=None):
    if status in (402, 429):
        return True
    text = str(message or "").lower()
    return any(marker in text for marker in QUOTA_MARKERS)


def fetch_google_rss(query, lookback_days=2):
    """Fetch recent banking stories without requiring an API key."""
    # Google News supports the when:N d search modifier.
    q = f"{query} when:{max(1, min(7, int(lookback_days)))}d"
    response = requests.get(
        PROVIDERS["google_rss"]["endpoint"],
        params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 Audit-Intelligence-News/1.0"},
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    rows = []

    for item in root.findall("./channel/item"):
        title = blank(item.findtext("title"))
        link = blank(item.findtext("link"))
        description = re.sub(
            r"<[^>]+>", " ", blank(item.findtext("description"))
        )
        pub = blank(item.findtext("pubDate"))

        source_node = item.find("source")
        source = (
            blank(source_node.text)
            if source_node is not None
            else ""
        ) or "Google News"

        if not title or not link:
            continue

        rows.append({
            "title": title,
            "description": re.sub(r"\\s+", " ", description).strip(),
            "content": "",
            "url": link,
            "image_url": "",
            "source": source,
            "published_at": pub,
            "author": "",
        })

    return rows


def fetch_newsdata(query, api_key):
    """Optional NewsData source. Deliberately avoids timeframe because the
    supplied plan rejects that parameter. Local filtering handles lookback."""
    response = requests.get(
        PROVIDERS["newsdata"]["endpoint"],
        params={
            "apikey": api_key,
            "q": query[:100],
            "language": "en",
            "size": 10,
            "image": 1,
        },
        timeout=DEFAULT_TIMEOUT,
    )

    try:
        payload = response.json()
    except Exception:
        payload = {}

    if payload.get("status") != "success":
        result = payload.get("results")
        message = (
            result.get("message")
            if isinstance(result, dict)
            else payload.get("message")
        ) or f"HTTP {response.status_code}"
        code = result.get("code", "") if isinstance(result, dict) else ""
        if is_quota_error(f"{code} {message}", response.status_code):
            raise QuotaExhausted(message)
        raise RuntimeError(message)

    rows = []
    for item in payload.get("results", []) or []:
        creator = item.get("creator")
        author = ", ".join(creator) if isinstance(creator, list) else blank(creator)
        rows.append({
            "title": blank(item.get("title")),
            "description": blank(item.get("description")),
            "content": blank(item.get("content")),
            "url": blank(item.get("link")),
            "image_url": blank(item.get("image_url")),
            "source": blank(item.get("source_id")) or "NewsData",
            "published_at": blank(item.get("pubDate")),
            "author": author,
        })
    return rows


def extract_page_image(url):
    """Best-effort article image recovery when NewsData has no image_url."""
    if not url:
        return ""
    try:
        response = requests.get(
            url,
            timeout=7,
            headers={"User-Agent": "Mozilla/5.0 (Audit-Intelligence/1.0)"},
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return ""
        html = response.text[:120000]

        patterns = [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                image = match.group(1).strip().replace("&amp;", "&")
                if image.startswith("//"):
                    image = "https:" + image
                elif image.startswith("/"):
                    parsed = urlparse(response.url)
                    image = f"{parsed.scheme}://{parsed.netloc}{image}"
                if image.startswith(("http://", "https://")):
                    return image
    except Exception:
        pass
    return ""


def enrich_missing_images(rows, max_workers=8):
    """Recover article-specific OG/Twitter images for NewsData rows."""
    # Prefer the image embedded by the publisher on the actual article page.
    # This prevents NewsData/source-level thumbnails from being reused across
    # unrelated stories. If extraction fails, retain NewsData's image_url.
    candidates = [r for r in rows if blank(r.get("url")) and not blank(r.get("image_url"))]
    if not candidates:
        return rows

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(extract_page_image, row.get("url", "")): row
            for row in candidates
        }
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                image = future.result()
                if image:
                    row["image_url"] = image
            except Exception:
                pass
    return rows


# ----------------------------- dedup -----------------------------

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
    if not keep.get("published_at") and other.get("published_at"):
        keep["published_at"] = other["published_at"]


def deduplicate(records, threshold=0.80):
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


def _fetch_rss_job(category, query, lookback_days):
    return category, fetch_google_rss(query, lookback_days)


def fetch_all(
    api_keys,
    lookback_days=2,
    categories=None,
    fuzzy_threshold=0.80,
    max_workers=6,
):
    """NewsData.io-only ingestion with category queries and image preservation."""
    per_provider = {
        "newsdata": {"requests": 0, "articles": 0, "errors": 0, "quota_hits": 0},
    }
    errors = []
    raw = []
    key = blank(api_keys.get("newsdata"))

    if not key:
        return [], ["NewsData.io API key is missing."], {
            "per_provider": per_provider,
            "raw": 0, "unique": 0, "retained": 0,
            "dedup": {"by_url": 0, "by_title": 0, "by_fuzzy": 0},
            "active": [],
        }

    jobs = [
        (category, query)
        for category, queries in QUERIES.items()
        if not categories or category in categories
        for query in queries
    ]

    def job(category, query):
        return category, fetch_newsdata(query, key)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(job, category, query) for category, query in jobs]
        for future in as_completed(futures):
            category = "Unknown"
            try:
                category, rows = future.result()
                per_provider["newsdata"]["requests"] += 1
                per_provider["newsdata"]["articles"] += len(rows)
                for row in rows:
                    row["provider_category"] = category
                    row["providers"] = {"newsdata"}
                    raw.append(row)
            except QuotaExhausted as exc:
                per_provider["newsdata"]["requests"] += 1
                per_provider["newsdata"]["quota_hits"] += 1
                errors.append(f"NewsData.io quota reached for {category}: {exc}")
            except Exception as exc:
                per_provider["newsdata"]["requests"] += 1
                per_provider["newsdata"]["errors"] += 1
                errors.append(f"NewsData.io · {category} · {exc}")

    unique, dedup = deduplicate(raw, threshold=fuzzy_threshold)
    unique = enrich_missing_images(unique)

    cutoff = datetime.now(timezone.utc) - timedelta(days=int(lookback_days))
    filtered = []
    for row in unique:
        value = row.get("published_at")
        if not value:
            filtered.append(row)
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
        "active": ["newsdata"],
    }
