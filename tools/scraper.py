"""Product URL scraper — requests + BS4 with Playwright fallback for JS-heavy pages."""

import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_text(soup: BeautifulSoup) -> str:
    """Extract readable text from BeautifulSoup, removing noise."""
    for tag in soup(["script", "style", "nav", "footer", "header", "meta", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def scrape_url(url: str, max_chars: int = 4000) -> Optional[str]:
    """
    Fetch a product page and return its text content.

    Falls back to Playwright if requests fails or content looks JS-rendered
    (less than 200 chars of meaningful text after cleaning).
    """
    text = _fetch_with_requests(url)
    if text and len(text) >= 200:
        return text[:max_chars]

    logger.info("requests returned short content, trying Playwright fallback")
    text = _fetch_with_playwright(url)
    if text:
        return text[:max_chars]

    logger.warning("Both scrapers failed for %s", url)
    return None


def _fetch_with_requests(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return _clean_text(soup)
    except Exception as exc:
        logger.warning("requests scrape failed: %s", exc)
        return None


def _fetch_with_playwright(url: str) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=_HEADERS)
            page.goto(url, wait_until="networkidle", timeout=20_000)
            time.sleep(2)
            html = page.content()
            browser.close()
        soup = BeautifulSoup(html, "html.parser")
        return _clean_text(soup)
    except Exception as exc:
        logger.warning("Playwright scrape failed: %s", exc)
        return None
