from capitalguard.infrastructure.market.symbol_catalog import MarketVenue, SymbolCatalog


def test_binance_catalog_normalizes_symbol_without_adding_a_new_venue():
    catalog = SymbolCatalog.binance(["BTCUSDT"], market="Futures-USD-M")

    resolved = catalog.resolve(" btcusdt ")

    assert resolved is not None
    assert resolved.venue is MarketVenue.BINANCE
    assert resolved.provider_symbol == "BTCUSDT"
    assert resolved.market == "Futures-USD-M"


def test_catalog_returns_none_for_a_symbol_not_explicitly_supported():
    assert SymbolCatalog.binance(["BTCUSDT"]).resolve("XAUUSD") is None
