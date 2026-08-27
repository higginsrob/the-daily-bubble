"""Google News RSS → title/url/snippet hits. Same path on every machine; no extra keys."""

from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from html import unescape
from urllib.parse import quote_plus, urlsplit

import httpx
from langsmith import traceable

from daily_bubble.models import SearchHit
from daily_bubble.news.fetch import TIMEOUT, USER_AGENT

MAX_HITS = 5
RSS_URL = "https://news.google.com/rss/search"
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_URL_IN_BYTES = re.compile(rb"https?://[^\x00-\x1f\x7f-\xff]{8,}")
_TAG = re.compile(r"<[^>]+>")
_SIG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')


def _strip_html(text: str) -> str:
    text = unescape(text or "")
    text = _TAG.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _unwrap_embedded_url(url: str) -> str:
    """Older Google News tokens sometimes embed the publisher URL in base64."""
    parsed = urlsplit(url)
    if parsed.netloc != "news.google.com":
        return url
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or parts[-2] not in {"articles", "read"}:
        return url
    token = parts[-1].split("?")[0]
    pad = "=" * ((4 - len(token) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + pad)
    except Exception:  # noqa: BLE001
        return url
    for match in _URL_IN_BYTES.findall(raw):
        candidate = match.decode("ascii", errors="ignore")
        if candidate.startswith("http") and "news.google.com" not in candidate:
            return candidate
    return url


def _article_id(url: str) -> str:
    return urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].split("?")[0]


def _resolve_via_batchexecute(url: str, client: httpx.Client) -> str:
    """Post-2024 tokens need a signature from the article page, then Fbv4je."""
    try:
        page = client.get(url)
        page.raise_for_status()
    except httpx.HTTPError:
        return url
    sig = _SIG.search(page.text)
    ts = _TS.search(page.text)
    if not sig or not ts:
        return url
    article_id = _article_id(url)
    rpc_inner = json.dumps(
        [
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
                 None, None, None, None, None, 0, 1],
                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
            ],
            article_id,
            int(ts.group(1)),
            sig.group(1),
        ],
        separators=(",", ":"),
    )
    f_req = json.dumps([[["Fbv4je", rpc_inner, None, "generic"]]], separators=(",", ":"))
    try:
        response = client.post(
            BATCH_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Referer": "https://news.google.com/",
            },
            data={"f.req": f_req},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return url
    body = response.text
    if body.startswith(")]}'"):
        body = body.split("\n", 1)[-1]
    body = body.lstrip()
    head, _, tail = body.partition("\n")
    if head.strip().isdigit():
        body = tail
    try:
        envelopes = json.loads(body)
    except json.JSONDecodeError:
        return url
    for env in envelopes:
        if not (isinstance(env, list) and len(env) >= 3):
            continue
        if env[0] != "wrb.fr" or env[1] != "Fbv4je":
            continue
        try:
            payload = json.loads(env[2])
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, list)
            and len(payload) >= 2
            and payload[0] == "garturlres"
            and isinstance(payload[1], str)
            and payload[1].startswith("http")
        ):
            return payload[1]
    return url


def _publisher_url(url: str, client: httpx.Client) -> str:
    decoded = _unwrap_embedded_url(url)
    if urlsplit(decoded).netloc != "news.google.com":
        return decoded
    return _resolve_via_batchexecute(url, client)


def _parse_rss(xml: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return hits
    for item in root.findall(".//{*}item"):
        title = _strip_html(item.findtext("{*}title") or "")
        link = (item.findtext("{*}link") or "").strip()
        snippet = _strip_html(item.findtext("{*}description") or "")
        if snippet == title:
            snippet = ""
        if not link.startswith("http"):
            continue
        hits.append(SearchHit(title=title or link, url=link, snippet=snippet))
    return hits


@traceable(name="news_search", tags=["ingest", "search"])
def search_news(query: str, limit: int = MAX_HITS) -> list[SearchHit]:
    """Search Google News RSS and normalize to SearchHit rows."""
    rss_url = f"{RSS_URL}?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=headers) as client:
        response = client.get(rss_url)
        response.raise_for_status()
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for hit in _parse_rss(response.text):
            url = _publisher_url(hit.url, client)
            key = url.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit.model_copy(update={"url": url}))
            if len(hits) >= limit:
                break
    return hits
