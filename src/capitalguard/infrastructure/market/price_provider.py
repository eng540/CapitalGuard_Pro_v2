from capitalguard.domain.ports import PriceFeedPort
from .binance_client import BinanceClient

class PriceProvider(PriceFeedPort):
    def __init__(self) -> None:
        self.client = BinanceClient()

    def get_price(self, symbol: str) -> float:
        return self.client.get_price(symbol)
