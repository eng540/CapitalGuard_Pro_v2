import importlib.util
from pathlib import Path
from types import SimpleNamespace


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic/versions/20260825_repair_historical_market_evidence_sequence.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("market_evidence_sequence_repair", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _Connection:
    class _Dialect:
        name = "postgresql"

    dialect = _Dialect()

    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))
        if "pg_get_serial_sequence" in sql:
            return _ScalarResult("historical_market_evidence_id_seq")
        return _ScalarResult(None)


class _Op:
    def __init__(self, connection):
        self.connection = connection

    def get_bind(self):
        return self.connection


def test_sequence_repair_sets_first_unused_id_without_touching_rows(monkeypatch):
    migration = _load_migration()
    connection = _Connection()
    monkeypatch.setattr(migration, "op", _Op(connection))
    monkeypatch.setattr(
        migration.sa,
        "inspect",
        lambda _bind: SimpleNamespace(get_table_names=lambda: ["historical_market_evidence"]),
    )

    migration.upgrade()

    assert len(connection.statements) == 2
    assert "pg_get_serial_sequence" in connection.statements[0][0]
    assert connection.statements[0][1] == {
        "table_name": "historical_market_evidence",
        "column_name": "id",
    }
    assert "setval" in connection.statements[1][0]
    assert connection.statements[1][1] == {
        "sequence_name": "historical_market_evidence_id_seq",
    }


def test_sequence_repair_is_noop_on_non_postgresql(monkeypatch):
    migration = _load_migration()
    connection = _Connection()
    connection.dialect = SimpleNamespace(name="sqlite")
    monkeypatch.setattr(migration, "op", _Op(connection))

    migration.upgrade()

    assert connection.statements == []
