"""Manual archive discovery, download and extraction commands."""

import argparse
import logging
from datetime import date
from pathlib import Path

import requests

from app import __version__
from app.config import load_settings
from app.discovery import discover_archives
from app.downloader import download_archive
from app.errors import CollectorError
from app.extractor import extract_archive
from app.http import create_session
from app.markets import MARKET_URLS, Market

LOGGER = logging.getLogger(__name__)


def _trade_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a valid date in YYYY-MM-DD format.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="moex-collector",
        description="Find, download and extract MOEX OrderLog archives on demand.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("markets", help="Show configured MOEX market URLs")
    for name, help_text in (
        ("archives", "List archives actually available on MOEX"),
        ("download", "Download one archive and extract its files"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--market", required=True, choices=[m.value for m in Market])
        command.add_argument("--date", type=_trade_date, required=name == "download")
        command.add_argument(
            "--type", dest="archive_type", type=str.upper, choices=("A", "B"),
            required=name == "download",
        )
        command.add_argument("--ca-bundle", type=Path, help="Trusted CA bundle in PEM format")
        if name == "download":
            command.add_argument("--downloads-dir", type=Path, help="Override archive folder")
            command.add_argument("--extracted-dir", type=Path, help="Override extraction folder")
            command.add_argument("--download-only", action="store_true", help="Skip extraction")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a requested operation; expected failures use a nonzero exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "markets":
        for market, url in MARKET_URLS.items():
            print(f"{market.value}: {url}")
        return 0
    if args.command is None:
        parser.print_help()
        return 0

    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(levelname)s: %(message)s",
    )
    try:
        ca_bundle = str(args.ca_bundle.resolve()) if args.ca_bundle else settings.moex_ca_bundle
        market = Market(args.market)
        with create_session(ca_bundle) as session:
            archives = discover_archives(market, session)
            selected = [
                item for item in archives
                if (args.date is None or item.trade_date == args.date)
                and (args.archive_type is None or item.archive_type == args.archive_type)
            ]
            if args.command == "archives":
                for item in selected:
                    print(f"{item.trade_date}  {item.archive_type}  {item.filename}")
                if not selected:
                    print("No matching archives are available on MOEX.")
                return 0
            if len(selected) != 1:
                dates = ", ".join(sorted({item.trade_date.isoformat() for item in archives}))
                raise CollectorError(
                    f"Expected one matching archive, found {len(selected)}. "
                    f"Available dates: {dates or 'none'}. Use the archives command to inspect the source."
                )
            archive = selected[0]
            path = download_archive(archive, args.downloads_dir or settings.downloads_dir, session)
            print(f"Archive: {path}")
            if not args.download_only:
                root = args.extracted_dir or settings.extracted_dir
                destination = root / market.value / path.stem
                extract_archive(path, destination)
                print(f"Extracted: {destination}")
        return 0
    except requests.exceptions.SSLError:
        LOGGER.error(
            "Cannot verify the MOEX TLS certificate. Provide a trusted PEM CA "
            "bundle using --ca-bundle or MOEX_CA_BUNDLE. See README.md."
        )
        return 1
    except (CollectorError, requests.RequestException, OSError, ValueError) as exc:
        LOGGER.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.error("Operation interrupted; run the same command to retry.")
        return 130
