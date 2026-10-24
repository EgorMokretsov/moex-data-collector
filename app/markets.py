"""Market identifiers and configured HTTPS source directories."""

from enum import StrEnum


class Market(StrEnum):
    """Stable identifiers used for local paths and future database records."""

    EQUITIES = "equities"
    BONDS = "bonds"
    CURRENCIES = "currencies"
    DERIVATIVES = "derivatives"


MARKET_URLS: dict[Market, str] = {
    Market.EQUITIES: "https://ftp.moex.com/pub/info/data/Equities/",
    Market.BONDS: "https://ftp.moex.com/pub/info/data/Bonds/",
    Market.CURRENCIES: "https://ftp.moex.com/pub/info/data/Currencies/",
    Market.DERIVATIVES: "https://ftp.moex.com/pub/info/data/Derivatives%20Market/",
}
