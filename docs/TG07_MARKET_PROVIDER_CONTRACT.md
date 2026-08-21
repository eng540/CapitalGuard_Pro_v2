# TG-07 — Market Provider and Symbol Catalog Foundation

TG-07 makes the existing read-only price boundary explicit through `PriceFeedPort` and introduces a typed `MarketVenue` and `SymbolCatalog`. Binance remains the only catalogued venue in this delivery; CoinGecko remains a runtime fallback used by the existing symbol-cache circuit breaker and is not presented as a separately supported trading venue.

The catalog does not place orders, alter recommendation lifecycle, claim Forex/metal/equity support, or change the deployed price source. A future venue requires provider-symbol mapping, market-session rules, freshness semantics, fallback behavior, quality tests, and an explicit gate decision.
