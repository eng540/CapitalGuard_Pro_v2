#--- START OF FILE: alembic/env.py ---
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os

# Import Base from the new central location
from capitalguard.infrastructure.db.models import Base

# This import is crucial: it ensures that all your model files are loaded
# so that Base.metadata knows about them.
from capitalguard.infrastructure.db.models import __all__ as all_models

config = context.config
db_url = (os.getenv("DATABASE_URL") or "").strip()
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
if db_url:
    config.set_section_option(config.config_ini_section, "sqlalchemy.url", db_url)

if config.config_file_name:
    fileConfig(config.config_file_name)

# Set the target metadata for Alembic
target_metadata = Base.metadata

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section),
                                     prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
#--- END OF FILE ---