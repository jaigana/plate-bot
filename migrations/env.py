import asyncio
import os
import re
from logging.config import fileConfig

import app.infrastructure.db.models  # noqa: F401 - register every mapped table
from alembic import context
from app.infrastructure.db.base import Base
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.getenv("DATABASE_URL")
if database_url:
    database_url = database_url.replace("postgres://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    config.set_main_option("sqlalchemy.url", database_url)
database_schema = os.getenv("DATABASE_SCHEMA", "cpm2")
if not re.fullmatch(r"[a-z_][a-z0-9_]*", database_schema):
    raise ValueError("DATABASE_SCHEMA must be a lowercase ASCII PostgreSQL identifier")

target_metadata = Base.metadata


def prepare_schema(connection: object) -> None:
    """Create and select the application's private schema before Alembic touches tables."""
    from sqlalchemy import Connection, text

    assert isinstance(connection, Connection)
    quoted_schema = f'"{database_schema}"'
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quoted_schema}"))
    connection.execute(text(f"SET search_path TO {quoted_schema}"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.execute(f'CREATE SCHEMA IF NOT EXISTS "{database_schema}"')
        context.execute(f'SET search_path TO "{database_schema}"')
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(prepare_schema)
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
