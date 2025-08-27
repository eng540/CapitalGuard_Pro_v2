#--- START OF FILE: alembic/env.py ---
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import os

# استيراد Base من الموقع المركزي الجديد
from capitalguard.infrastructure.db.models.base import Base

# هذا الاستيراد هو المفتاح: يقوم بتنفيذ ملف __init__.py في حزمة models،
# والذي بدوره يستورد جميع ملفات الموديلات الفردية، مما يجعلها معروفة لـ Base.metadata.
from capitalguard.infrastructure.db import models

config = context.config

# تعيين target_metadata لـ Alembic
target_metadata = Base.metadata

db_url = (os.getenv("DATABASE_URL") or "").strip()
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
if db_url:
    config.set_section_option(config.config_ini_section, "sqlalchemy.url", db_url)

if config.config_file_name:
    fileConfig(config.config_file_name)

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