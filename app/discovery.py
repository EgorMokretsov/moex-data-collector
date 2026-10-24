"""Discover actual OrderLog links in a MOEX directory listing."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from app.markets import MARKET_URLS, Market

TIMEOUT = (10, 60)
ARCHIVE_NAME = re.compile(
    r"OrderLog(?P<date>\d{8})_(?P<type>[ABАВ])\.(?P<format>zip|7z)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Archive:
    """An archive found on the source, retaining its original filename."""

    market: Market
    trade_date: date
    archive_type: str
    filename: str
    source_url: str
    archive_format: str


def parse_listing(html: str, market: Market, base_url: str) -> list[Archive]:
    """Accept archive links in this directory, including Cyrillic А and В."""
    base = urlsplit(base_url)
    base_path = unquote(base.path)
    found: dict[str, Archive] = {}
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urlsplit(urljoin(base_url, link["href"]))
        if (url.scheme, url.netloc) != (base.scheme, base.netloc):
            continue
        path = unquote(url.path)
        if not path.startswith(base_path) or url.query or url.fragment:
            continue
        filename = path[len(base_path):]
        match = ARCHIVE_NAME.fullmatch(filename)
        if match is None:
            continue
        try:
            trade_date = datetime.strptime(match["date"], "%Y%m%d").date()
        except ValueError:
            continue
        archive_type = match["type"].upper().translate(str.maketrans("АВ", "AB"))
        source_url = urlunsplit(
            (url.scheme, url.netloc, quote(path, safe="/"), "", "")
        )
        found[filename] = Archive(
            market, trade_date, archive_type, filename, source_url,
            match["format"].lower(),
        )
    return sorted(
        found.values(),
        key=lambda item: (item.trade_date, item.archive_type, item.filename),
    )


def discover_archives(market: Market, session: requests.Session) -> list[Archive]:
    """Read a live listing; never guess a download URL from a requested date."""
    url = MARKET_URLS[market]
    with session.get(url, timeout=TIMEOUT) as response:
        response.raise_for_status()
        # IIS directory listings use UTF-8 even without an HTTP charset.
        response.encoding = "utf-8"
        return parse_listing(response.text, market, url)
