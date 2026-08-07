import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from ingestion.models import Base

config = context.config
# Skip fileConfig under pytest: it resets root handlers and breaks caplog assertions.
if config.config_file_name is not None and "pytest" not in sys.modules:
    fileConfig(config.config_file_name)

config.set_main_option(
    "sqlalchemy.url",
    os.getenv("INGESTION_DATABASE_URL", config.get_main_option("sqlalchemy.url")),
)
target_metadata = Base.metadata

# alembic.ini is the single source of truth for the version-table location.
version_table = config.get_main_option("version_table", "alembic_version_ingestion")
version_table_schema = config.get_main_option("version_table_schema", "ingestion")


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Keep Ingestion autogeneration blind to tables owned by other services."""

    if type_ == "schema":
        return name == version_table_schema
    return parent_names.get("schema_name") in (None, version_table_schema)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        version_table=version_table,
        version_table_schema=version_table_schema,
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

        def run_with_connection(sync_connection: Connection) -> None:
            context.configure(
                connection=sync_connection,
                include_schemas=True,
                include_name=include_name,
                target_metadata=target_metadata,
                version_table=version_table,
                version_table_schema=version_table_schema,
            )
            with context.begin_transaction():
                context.run_migrations()

        await connection.run_sync(run_with_connection)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
