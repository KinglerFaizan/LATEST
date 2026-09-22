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
                image = normalize_image_url(match.group(1), response.url)
                if image:
                    return image
    except Exception:
        pass
    return ""


def normalize_image_url(image, article_url=""):
    image = blank(image).replace("&amp;", "&")
    if not image:
        return ""
    if image.startswith("//"):
        return "https:" + image
    if image.startswith("/") and article_url:
        parsed = urlparse(article_url)
        return f"{parsed.scheme}://{parsed.netloc}{image}"
    if image.startswith(("http://", "https://")):
        return image
    return ""

def enrich_missing_images(rows, max_workers=8):
    """Recover article-specific OG/Twitter images for NewsData rows."""
    # Prefer the image embedded by the publisher on the actual article page.
    # This prevents NewsData/source-level thumbnails from being reused across
    # unrelated stories. If extraction fails, retain NewsData's image_url.
    candidates = [r for r in rows if blank(r.get("image_url")) and bool(r.get("url"))]
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