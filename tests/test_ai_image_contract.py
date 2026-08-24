import sys
from decimal import Decimal, getcontext
from pathlib import Path

_original_decimal_precision = getcontext().prec
sys.path.insert(0, str(Path(__file__).parents[1] / "ai_service"))
from main import _serialize_data_for_response
from schemas import ParsedDataResponse
getcontext().prec = _original_decimal_precision


def test_image_response_preserves_optional_leverage_and_decimal_strings():
    payload = _serialize_data_for_response(
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "entry": Decimal("77000"),
            "stop_loss": Decimal("76000"),
            "targets": [{"price": Decimal("78000"), "close_percent": 100.0}],
            "leverage": "5",
        }
    )
    response = ParsedDataResponse(**payload)

    assert response.entry == "77000"
    assert response.stop_loss == "76000"
    assert response.targets[0].price == "78000"
    assert response.leverage == "5"


def test_image_response_does_not_infer_missing_leverage():
    payload = _serialize_data_for_response(
        {
            "asset": "BTCUSDT",
            "side": "LONG",
            "entry": Decimal("77000"),
            "stop_loss": Decimal("76000"),
            "targets": [{"price": Decimal("78000"), "close_percent": 100.0}],
        }
    )

    assert ParsedDataResponse(**payload).leverage is None
