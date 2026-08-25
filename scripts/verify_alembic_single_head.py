"""Fail CI when the migration graph has more than one head."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
config = Config(str(ROOT / "alembic.ini"))
config.set_main_option("script_location", str(ROOT / "alembic"))
heads = ScriptDirectory.from_config(config).get_heads()
if len(heads) != 1:
    raise SystemExit(f"Expected exactly one Alembic head, found {len(heads)}: {heads}")
print(f"Alembic graph has one head: {heads[0]}")
