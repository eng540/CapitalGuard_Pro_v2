from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai_service.services.parsing_utils import normalize_targets, parse_decimal_token


def test_target_labels_and_activity_clocks_are_not_numeric_targets():
    assert parse_decimal_token("TP2") is None
    assert parse_decimal_token("17:46") is None
    targets = normalize_targets("17:46 Tp2 72300 TP3: 72400")
    assert [item["price"] for item in targets] == [72300, 72400]


def test_real_target_numbers_remain_unchanged():
    targets = normalize_targets("TP1: 71700 TP2: 71800")
    assert [item["price"] for item in targets] == [71700, 71800]


def test_clock_safety_accepts_non_clock_price_tokens():
    assert parse_decimal_token("71700") == 71700
    assert parse_decimal_token("1.2345") == 1.2345
