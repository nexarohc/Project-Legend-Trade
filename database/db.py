"""SQLite engine/session setup shared by every module."""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# LEGEND_DB_PATH lets tests (and anyone else) point at an isolated database
# instead of the real one in the user's home directory. DEX_DB_PATH is the name
# this setting had before the repo split and still works, so an existing
# deployment does not silently start writing to a brand-new empty database.
_override = os.environ.get("LEGEND_DB_PATH") or os.environ.get("DEX_DB_PATH")
if _override:
    DB_PATH = Path(_override)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
else:
    DATA_DIR = Path.home() / ".legend-trade"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH = DATA_DIR / "legend.db"

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def init_db() -> None:
    from database import models  # noqa: F401  (registers models on Base)
    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
