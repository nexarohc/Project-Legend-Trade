"""Load a `.env` file into the process environment.

There is a trap this exists to close. `backend/app/config.py` reads `.env`
through pydantic-settings, which populates the `Settings` *object* — it never
touches `os.environ`. But broker credentials are deliberately not Settings
fields: the adapters read `os.environ` directly, so that a key can never arrive
from the database or from a request body.

Those two facts are individually correct and jointly a trap. Under Docker it is
invisible, because `docker-compose.yml` passes `env_file: .env` and the
credentials become real environment variables before Python starts. Run the same
code straight from a shell and `.env` is silently ignored — the operator fills
the file in exactly as documented and is told the credentials are missing.

So: an explicit loader, called by the entry points that need it.

Values already present in the real environment always win. In a container the
orchestrator is the authority on configuration, and a stale `.env` left in a
working directory must not be able to override it.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `.env` text into a mapping. Malformed lines are skipped, not raised.

    Deliberately does **not** strip trailing `#` comments from unquoted values.
    A secret containing a `#` is entirely plausible, and silently truncating a
    credential produces an authentication failure whose cause is invisible —
    far worse than requiring the file to hold values rather than commentary.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Quotes delimit the value; they are not part of it.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path | None = None, *, override: bool = False) -> list[str]:
    """Populate `os.environ` from `path`. Returns the names actually set.

    Returns an empty list when the file does not exist, which is the normal case
    for a container deployment and not a condition worth complaining about.
    """
    path = Path(path) if path is not None else DEFAULT_ENV_FILE
    if not path.is_file():
        return []

    applied = []
    for key, value in parse_env_file(path.read_text(encoding="utf-8")).items():
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
        applied.append(key)
    return sorted(applied)
