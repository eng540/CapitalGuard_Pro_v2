from decimal import Decimal

from capitalguard.interfaces.telegram.log_handler import (
    _contains_multiple_log_commands,
    _parse_log_text,
)


def test_log_parser_accepts_quick_command_and_marks_direct_source():
    parsed = _parse_log_text("BTCUSDT LONG 90000 89000 91000 92000")

    assert parsed is not None
    assert parsed["asset"] == "BTCUSDT"
    assert parsed["side"] == "LONG"
    assert parsed["entry"] == Decimal("90000")
    assert parsed["source_type"] == "DIRECT_INPUT"
    assert parsed["targets"][-1]["close_percent"] == 100.0


def test_log_parser_accepts_editor_command_with_arabic_digits():
    parsed = _parse_log_text(
        "Asset: ETHUSDT\nSide: SHORT\nEntry: ٣٠٠٠\nSL: ٣١٠٠\nTPs: ٢٩٠٠ ٢٨٠٠"
    )

    assert parsed is not None
    assert parsed["side"] == "SHORT"
    assert parsed["entry"] == Decimal("3000")
    assert parsed["targets"][0]["price"] == Decimal("2900")
    assert parsed["source_type"] == "DIRECT_INPUT"


def test_log_parser_rejects_incomplete_input():
    assert _parse_log_text("BTCUSDT LONG 90000") is None


def test_log_parser_rejects_pasted_batch_of_commands():
    batch = "/log BTCUSDT LONG 64270 64180 64335 64400\n/log ETHUSDT SHORT 1913 1917 1910 1905"
    assert _contains_multiple_log_commands(batch) is True
    assert _parse_log_text(batch) is None
