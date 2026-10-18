"""Alembic environment: async engine, URL from app settings, metadata from app models."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.db.models  # noqa: F401  (registers every table on the metadata)
from app.core.config import get_settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata
database_url = config.attributes.get("database_url") or get_settings().database_url
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def _include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # The generated tsvector column is managed through Computed(); ignore reflection noise.
    return not (type_ == "column" and name == "search_vector" and reflected and compare_to)


def _render_item(type_, obj, autogen_context):  # type: ignore[no-untyped-def]
    # Encrypted columns are stored as TEXT; keep migrations independent of app code.
    if type_ == "type" and type(obj).__name__ == "EncryptedString":
        return "sa.Text()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        render_item=_render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        do_run_migrations(connection)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
