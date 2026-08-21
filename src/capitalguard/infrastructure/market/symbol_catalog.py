"""Explicit venue and symbol metadata for market-data compatibility checks."""

from dataclasses import dataclass
from enum import StrEnum


class MarketVenue(StrEnum):
    BINANCE = "binance"


@dataclass(frozen=True)
class MarketSymbol:
    canonical: str
    venue: MarketVenue
    provider_symbol: str
    market: str


class SymbolCatalog:
    """Normalizes known symbols without claiming support for a new venue."""

    def __init__(self, symbols: tuple[MarketSymbol, ...] = ()) -> None:
        self._symbols = {entry.canonical.upper(): entry for entry in symbols}

    def resolve(self, symbol: str) -> MarketSymbol | None:
        return self._symbols.get((symbol or "").strip().upper())

    @classmethod
    def binance(cls, symbols: list[str], market: str = "spot") -> "SymbolCatalog":
        return cls(tuple(
            MarketSymbol(
                canonical=symbol.upper(),
                venue=MarketVenue.BINANCE,
                provider_symbol=symbol.upper(),
                market=market,
            )
            for symbol in symbols
        ))
