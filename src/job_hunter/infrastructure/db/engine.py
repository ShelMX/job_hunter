"""Создание engine и контекстный менеджер сессии."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///") and ":memory:" not in database_url:
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Сессия с commit/rollback — единственное место управления транзакциями."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
