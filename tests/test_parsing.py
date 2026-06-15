# --- tests/test_parsing.py ---
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock

# Assuming ParsingService is now in the correct location
from capitalguard.application.services.parsing_service import ParsingService, ParsingResult
# Import repository class for potential mocking or setup
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.models import ParsingTemplate # Import model for fixture setup

# Mark tests as async because extract_trade_data is async
pytestmark = pytest.mark.asyncio

@pytest.fixture
def mock_parsing_repo():
    """Provides a mock ParsingRepository."""
    mock = MagicMock(spec=ParsingRepository)
    # Configure mock methods as needed for tests
    mock.get_active_templates.return_value = [] # Default: no templates
    mock.add_attempt.return_value = MagicMock(id=123) # Simulate adding attempt
    mock.update_attempt.return_value = None
    return mock

@pytest.fixture
def parsing_service(mock_parsing_repo):
    """Provides a ParsingService instance with a mocked repository."""
    # Pass the *class* type to the constructor if that's how it's designed
    # Or pass the mock instance if it expects an instance
    # Based on boot.py, it expects the class
    class MockParsingRepoClass:
         def __init__(self, session):
              # Return the mock instance when the service instantiates the repo
              return mock_parsing_repo
    return ParsingService(parsing_repo_class=MockParsingRepoClass)


# --- Test Cases for Internal Helpers ---
@pytest.mark.parametrize("input_str, expected", [
    ("100", Decimal("100")), ("65k", Decimal("65000")), ("1.5M", Decimal("1500000")),
    ("0.5", Decimal("0.5")), ("1,500.50", Decimal("1500.50")), ("١٢٣", Decimal("123")),
    ("٢٥ك", Decimal("25000")), ("-100", None), ("0", None), ("abc", None), ("", None), (None, None),
    ("100.123456789", Decimal("100.123456789")), # Precision test
    ("1b", Decimal("1000000000")), # Test 'B' suffix
])
def test_parse_one_number(parsing_service: ParsingService, input_str, expected):
    """Tests the internal number parsing helper."""
    assert parsing_service._parse_one_number(input_str) == expected

@pytest.mark.parametrize("input_tokens, expected", [
    (["50k", "52k"], [{"price": Decimal("50000"), "close_percent": 0.0}, {"price": Decimal("52000"), "close_percent": 100.0}]),
    (["60000@50", "62000@50"], [{"price": Decimal("60000"), "close_percent": 50.0}, {"price": Decimal("62000"), "close_percent": 50.0}]),
    (["70000"], [{"price": Decimal("70000"), "close_percent": 100.0}]),
    (["Invalid", "50000"], [{"price": Decimal("50000"), "close_percent": 100.0}]), # Skip invalid
    ([], []),
    (["50k@"], [{"price": Decimal("50000"), "close_percent": 0.0}]), # Treat as 0% if percentage missing after @
    (["55.5@25.5"], [{"price": Decimal("55.5"), "close_percent": 25.5}]),
    (["٦٠٠٠٠@٥٠", "٦٢٠٠٠"], [{"price": Decimal("60000"), "close_percent": 50.0}, {"price": Decimal("62000"), "close_percent": 50.0}]), # Arabic numerals in targets
])
def test_parse_targets_list(parsing_service: ParsingService, input_tokens, expected):
    """Tests the internal target list parsing helper."""
    assert parsing_service._parse_targets_list(input_tokens) == expected

@pytest.mark.parametrize("input_text, expected_asset, expected_side", [
    ("Signal #BTCUSDT LONG Entry 60k", "BTCUSDT", "LONG"),
    ("Short ETHUSDT now", "ETHUSDT", "SHORT"),
    ("Buy #SOLANA at 150", "SOLANA", "LONG"), # Hashtagged non-standard
    ("#AVAXUSDT Sell target 30", "AVAXUSDT", "SHORT"),
    ("شراء بيتكوين عند 60000", None, "LONG"), # Side detected, asset not standard
    ("ETH/USDT LONG", "ETHUSDT", "LONG"), # Slash separator
    ("XRP-PERP SHORT", "XRPPERP", "SHORT"), # Dash separator
    ("Try LONG for LINKUSDT maybe?", "LINKUSDT", "LONG"), # Asset later in text
])
def test_find_asset_and_side(parsing_service: ParsingService, input_text, expected_asset, expected_side):
    """Tests the internal asset and side detection helper."""
    cleaned = parsing_service._clean_text(input_text)
    asset, side = parsing_service._find_asset_and_side(cleaned)
    assert asset == expected_asset
    assert side == expected_side

# --- Test Cases for extract_trade_data (Main Method) ---

async def test_extract_data_no_templates_ner_fallback_success(parsing_service: ParsingService, mock_parsing_repo: MagicMock):
    """Tests successful parsing via NER when no regex templates match."""
    mock_parsing_repo.get_active_templates.return_value = [] # Ensure no templates are returned
    text = "Signal: LONG BTCUSDT Entry 60000 SL 59000 Targets 61k, 62.5k@50"
    user_id = 1

    result = await parsing_service.extract_trade_data(text, user_id)

    assert result.success is True
    assert result.parser_path_used == "ner" # Expect NER fallback
    assert result.template_id_used is None
    assert result.attempt_id == 123 # From mock add_attempt
    assert result.data is not None
    assert result.data['asset'] == "BTCUSDT"
    assert result.data['side'] == "LONG"
    assert result.data['entry'] == Decimal("60000")
    assert result.data['stop_loss'] == Decimal("59000")
    assert result.data['targets'] == [
        {"price": Decimal("61000"), "close_percent": 0.0}, # First target gets 0%
        {"price": Decimal("62500"), "close_percent": 50.0}  # Second target keeps specified %
        # If only these two, the _parse_targets_list logic doesn't auto-assign 100%
    ]
    # Check if attempt was updated correctly
    mock_parsing_repo.update_attempt.assert_called_once()
    # Get the arguments passed to update_attempt
    update_args = mock_parsing_repo.update_attempt.call_args[1]
    assert update_args['attempt_id'] == 123
    assert update_args['was_successful'] is True
    assert update_args['parser_path_used'] == "ner"
    assert update_args['latency_ms'] is not None and update_args['latency_ms'] >= 0


async def test_extract_data_regex_template_success(parsing_service: ParsingService, mock_parsing_repo: MagicMock):
    """Tests successful parsing using a matching regex template."""
    # Define a mock template
    template1 = MagicMock(spec=ParsingTemplate)
    template1.id = 1
    # Example regex (adjust based on actual template structure)
    template1.pattern_value = r"ASSET:\s*(?P<asset>\w+)\s*SIDE:\s*(?P<side>\w+)\s*ENTRY:\s*(?P<entry>[\d.,kmb]+)\s*SL:\s*(?P<sl>[\d.,kmb]+)\s*TARGETS:\s*(?P<targets_str>.*)"
    mock_parsing_repo.get_active_templates.return_value = [template1]

    text = "ASSET: ETHUSDT SIDE: SHORT ENTRY: 3000 SL: 3100 TARGETS: 2900 2800@100"
    user_id = 2

    result = await parsing_service.extract_trade_data(text, user_id)

    assert result.success is True
    assert result.parser_path_used == "regex"
    assert result.template_id_used == 1 # Matched template1
    assert result.attempt_id == 123
    assert result.data is not None
    assert result.data['asset'] == "ETHUSDT"
    assert result.data['side'] == "SHORT"
    assert result.data['entry'] == Decimal("3000")
    assert result.data['stop_loss'] == Decimal("3100")
    assert result.data['targets'] == [
        {"price": Decimal("2900"), "close_percent": 0.0},
        {"price": Decimal("2800"), "close_percent": 100.0}
    ]
    # Check DB update call
    mock_parsing_repo.update_attempt.assert_called_once()
    update_args = mock_parsing_repo.update_attempt.call_args[1]
    assert update_args['was_successful'] is True
    assert update_args['parser_path_used'] == "regex"
    assert update_args['used_template_id'] == 1


async def test_extract_data_all_paths_fail(parsing_service: ParsingService, mock_parsing_repo: MagicMock):
    """Tests the case where neither regex nor NER can parse the text."""
    mock_parsing_repo.get_active_templates.return_value = []
    text = "Just discussing the market, maybe check bitcoin later?"
    user_id = 3

    result = await parsing_service.extract_trade_data(text, user_id)

    assert result.success is False
    assert result.parser_path_used == "failed" # Should explicitly be 'failed'
    assert result.template_id_used is None
    assert result.attempt_id == 123
    assert result.data is None
    assert result.error_message is not None
    # Check DB update call
    mock_parsing_repo.update_attempt.assert_called_once()
    update_args = mock_parsing_repo.update_attempt.call_args[1]
    assert update_args['was_successful'] is False
    assert update_args['parser_path_used'] == "failed"
    assert update_args['used_template_id'] is None


async def test_extract_data_db_error_on_start(parsing_service: ParsingService, mock_parsing_repo: MagicMock):
    """Tests handling of DB error during initial attempt creation."""
    mock_parsing_repo.add_attempt.side_effect = Exception("DB Connection Error")

    text = "Signal: LONG BTCUSDT Entry 60k SL 59k TP 61k"
    user_id = 4

    result = await parsing_service.extract_trade_data(text, user_id)

    assert result.success is False
    assert result.error_message == "Database error during initialization."
    assert result.attempt_id is None # No attempt record created
    # Ensure update_attempt was NOT called
    mock_parsing_repo.update_attempt.assert_not_called()

# --- Tests for Correction Recording (Optional but recommended) ---

# Mock data for correction tests
mock_attempt_id = 456
original_parsed_data_dict = {
    'asset': 'BTCUSDT', 'side': 'LONG', 'entry': Decimal('60000'),
    'stop_loss': Decimal('59000'), 'targets': [{'price': Decimal('61000'), 'close_percent': 100.0}]
}
corrected_data_dict = {
    'asset': 'BTCUSDT', 'side': 'LONG', 'entry': Decimal('60100'), # Corrected entry
    'stop_loss': Decimal('59000'), 'targets': [{'price': Decimal('61500'), 'close_percent': 100.0}] # Corrected target
}

@pytest.mark.skip(reason="Requires session_scope/UOW setup for async correction method")
async def test_record_correction_saves_diff(parsing_service: ParsingService, mock_parsing_repo: MagicMock):
    """Tests that corrections are recorded with the correct diff."""
    await parsing_service.record_correction(mock_attempt_id, corrected_data_dict, original_parsed_data_dict)

    mock_parsing_repo.update_attempt.assert_called_once()
    update_args = mock_parsing_repo.update_attempt.call_args[1]
    assert update_args['attempt_id'] == mock_attempt_id
    assert update_args['was_corrected'] is True
    assert 'corrections_diff' in update_args
    diff = update_args['corrections_diff']
    # Check specific diffs (converting Decimals to float/str for comparison if needed)
    assert 'entry' in diff
    assert diff['entry']['old'] == '60000' # Stored as string from Decimal
    assert diff['entry']['new'] == '60100'
    assert 'targets' in diff
    # Diff format for lists might vary, check presence and basic structure
    assert isinstance(diff['targets']['old'], list)
    assert isinstance(diff['targets']['new'], list)
    assert diff['targets']['new'][0][0] == 61500.0 # Example check on float price

# --- END of test_parsing.py ---