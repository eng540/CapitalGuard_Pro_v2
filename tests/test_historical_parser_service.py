from decimal import Decimal

from capitalguard.application.services.historical_parser_service import HistoricalParserService
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.infrastructure.db.repository import ParsingRepository


def parser():
    return HistoricalParserService(ParsingService(ParsingRepository))


def test_historical_parser_parses_complete_signal_without_live_creation():
    result = parser().parse(
        "#BTCUSDT LONG Entry: 65000 Stop: 64000 TP1: 66000@50% TP2: 67000@50%"
    )

    assert result.parse_status == "PARSED"
    assert result.data["asset"] == "BTCUSDT"
    assert result.data["side"] == "LONG"
    assert result.data["entry"] == Decimal("65000")
    assert result.data["stop_loss"] == Decimal("64000")
    assert len(result.data["targets"]) == 2
    assert result.confidence_score == Decimal("1.0000")


def test_historical_parser_supports_arabic_labels_and_reports_partial_data():
    result = parser().parse("#ETHUSDT شراء دخول 3000 وقف 2900 الهدف 3100")

    assert result.data["asset"] == "ETHUSDT"
    assert result.data["side"] == "LONG"
    assert result.data["entry"] == Decimal("3000")
    assert result.data["stop_loss"] == Decimal("2900")
    assert result.parse_status == "PARTIAL"
    assert "TARGETS_NOT_FOUND" in result.errors


def test_historical_parser_rejects_non_signal_text_without_side_effects():
    result = parser().parse("announcement: maintenance window tomorrow")

    assert result.parse_status == "UNPARSED"
    assert result.data["asset"] is None
    assert result.data["entry"] is None
    assert result.data["targets"] == []
