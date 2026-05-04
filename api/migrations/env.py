from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, event

from api.settings import get_settings


config = context.config
if config.config_file_name is not None:
    # ``disable_existing_loggers=False`` is mandatory: the default flips
    # ``Logger.disabled=True`` on every previously-configured logger that
    # is not named in ``alembic.ini`` (we list only ``alembic`` /
    # ``sqlalchemy`` / ``root`` there). That silently disables our
    # importer logger (``bible_study.importer``) for the rest of the
    # process — observed under pytest where the importer test runs after
    # an Alembic migration test, but it would also bite ``make import``
    # in production if a migration ran in the same process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _resolved_url() -> str:
    override = config.get_main_option("sqlalchemy.url") or ""
    if override:
        return override
    settings = get_settings()
    return f"sqlite:///{settings.database_path_absolute}"


def run_migrations_offline() -> None:
    url = _resolved_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolved_url()
    db_path = url.replace("sqlite:///", "")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
