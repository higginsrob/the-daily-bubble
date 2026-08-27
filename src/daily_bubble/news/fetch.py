"""HTTP fetch + main-content extraction for ingest."""

from __future__ import annotations

import httpx
import trafilatura

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
MAX_BODY_CHARS = 12_000
TIMEOUT = 15.0


def fetch_article(url: str) -> str:
    """Return extracted article text, or empty string on failure."""
    if not url.startswith("http://") and not url.startswith("https://"):
        return ""
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError:
        return ""

    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        url=url,
    )
    if not extracted:
        return ""
    return extracted.strip()[:MAX_BODY_CHARS]
