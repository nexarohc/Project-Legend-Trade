"""Dev entrypoint: `python backend/run.py`.

Puts the repo root (for database/, settings/, ai/, memory/, voice/,
automation/, browser/, vision/, mail/, scheduling/, booking/, shopping/,
coding/) and backend/ (for the `app` package) on sys.path, then runs uvicorn.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402

if __name__ == "__main__":
    # reload=False for the packaged app: end users don't edit source, and the
    # file-watcher can restart the server mid-request (dropping /chat calls as
    # "Failed to fetch"). Set LEGEND_RELOAD=1 to re-enable it during development.
    reload = os.environ.get("LEGEND_RELOAD") == "1"
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=reload)
