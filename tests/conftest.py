"""Points every test at an isolated, throwaway SQLite file instead of the
real ~/.legend-trade/legend.db. Must run before anything imports database.db.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

os.environ["LEGEND_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "legend_test.db")

import pytest  # noqa: E402

from database.db import Base, engine, SessionLocal  # noqa: E402
from database import models  # noqa: E402,F401


@pytest.fixture()
def session():
    Base.metadata.create_all(bind=engine)
    db_session = SessionLocal()
    try:
        yield db_session
    finally:
        db_session.close()
        Base.metadata.drop_all(bind=engine)
