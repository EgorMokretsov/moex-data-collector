"""Command-line entry point for the initial project scaffold."""

import argparse

from app import __version__
from app.markets import MARKET_URLS


def main(argv: list[str] | None = None) -> int:
    """Display help or the configured MOEX source directories."""
    parser = argparse.ArgumentParser(
        prog="moex-collector",
        description="MOEX Data Collector — initial project scaffold.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("markets", help="Show configured MOEX market URLs")
    args = parser.parse_args(argv)

    if args.command == "markets":
        for market, url in MARKET_URLS.items():
            print(f"{market.value}: {url}")
    else:
        parser.print_help()
    return 0
