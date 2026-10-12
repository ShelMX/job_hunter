"""Запуск миграций Alembic из кода (без alembic.ini)."""

from importlib import resources

from alembic import command
from alembic.config import Config


def upgrade_head(database_url: str) -> None:
    migrations_dir = resources.files("job_hunter.infrastructure.db") / "migrations"
    with resources.as_file(migrations_dir) as path:
        cfg = Config()
        cfg.set_main_option("script_location", str(path))
        cfg.set_main_option("sqlalchemy.url", database_url)
        command.upgrade(cfg, "head")
