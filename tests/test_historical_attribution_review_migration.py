import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260825_repair_historical_attribution_review_state.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("review_state_repair", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_legacy_attribution_review_state_is_repaired_and_reversible():
    engine = create_engine("sqlite:///:memory:")
    migration = _load_migration()
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE historical_signal_attributions "
                "(id INTEGER PRIMARY KEY, status VARCHAR(24))"
            )
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        columns = {
            column["name"]
            for column in inspect(connection).get_columns("historical_signal_attributions")
        }
        assert {"reviewed_at", "review_note"} <= columns

        migration.downgrade()
        columns_after = {
            column["name"]
            for column in inspect(connection).get_columns("historical_signal_attributions")
        }
        assert not {"reviewed_at", "review_note"} & columns_after
