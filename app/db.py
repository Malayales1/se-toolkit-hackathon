from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'planner.sqlite3'

engine = create_engine(
    f'sqlite:///{DB_PATH}',
    echo=False,
    future=True,
    connect_args={'check_same_thread': False},
)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
    expire_on_commit=False,
)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def initialize_runtime_schema() -> None:
    with engine.begin() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(users)").fetchall()
        }
        if "media_enabled" not in columns:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN media_enabled BOOLEAN NOT NULL DEFAULT 1")
            )
        if "wowles_enabled" not in columns:
            connection.execute(
                text("ALTER TABLE users ADD COLUMN wowles_enabled BOOLEAN NOT NULL DEFAULT 0")
            )
