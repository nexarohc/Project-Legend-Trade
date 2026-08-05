#!/usr/bin/env python3
"""Back up the Legend Trade database safely, while the server is running.

Why this exists rather than `cp legend.db backup.db`:

A plain copy of a live SQLite file is not a backup. SQLite writes in pages, and
`cp` reads them over a period during which the server may be committing a
transaction — the result can be a file that opens fine and is silently corrupt
in the middle, which you discover on the day you need it. SQLite's online-backup
API takes a read lock per page and produces a consistent snapshot of a database
that is actively being written to.

Every backup this script writes is verified with `PRAGMA integrity_check` before
it is kept. An unverified backup is a guess.

Usage:
    python scripts/backup.py                      # -> ./backups/legend-<UTC>.db
    python scripts/backup.py --dest /var/backups  # somewhere else
    python scripts/backup.py --keep 14            # prune to the newest 14
    python scripts/backup.py --verify-only FILE   # check an existing backup

Exit status is 0 only if a verified backup exists at the end, so this is safe to
run from cron with `MAILTO=` and rely on the failure mail.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def database_path() -> Path:
    """Where the running server keeps its database.

    Mirrors `database/db.py` exactly, including the legacy name, so a backup can
    never quietly snapshot a different (empty) database than the one in use —
    which would look like a success and leave you with nothing.
    """
    override = os.environ.get("LEGEND_DB_PATH") or os.environ.get("DEX_DB_PATH")
    if override:
        return Path(override)
    return Path.home() / ".legend-trade" / "legend.db"


def verify(path: Path) -> str:
    """Return SQLite's own verdict on a database file. 'ok' means ok."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()


def back_up(source: Path, destination: Path) -> None:
    """Snapshot `source` to `destination` using SQLite's online-backup API."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Read-only on the source: a backup must never be able to modify the thing
    # it is protecting, even through a bug in this script.
    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    snapshot = sqlite3.connect(str(destination))
    try:
        with snapshot:
            live.backup(snapshot)
    finally:
        live.close()
        snapshot.close()


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups. Returns what was removed.

    Ordered by modification time, not by filename. Sorting by name looks
    equivalent because the names are timestamps, but the collision suffix breaks
    it: `-` (0x2D) sorts before `.` (0x2E), so `legend-<stamp>-1.db` compares as
    *older* than `legend-<stamp>.db` even though it was written second. Caught
    by running this three times in one second and watching prune keep the oldest
    file and delete the two newest. mtime is what actually orders them.
    """
    backups = sorted(directory.glob("legend-*.db"), key=lambda p: p.stat().st_mtime)
    doomed = backups[:-keep] if keep > 0 and len(backups) > keep else []
    for path in doomed:
        path.unlink()
    return doomed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", default="backups", help="directory to write into")
    parser.add_argument(
        "--keep",
        type=int,
        default=0,
        help="keep only the newest N backups (0 = keep everything)",
    )
    parser.add_argument(
        "--verify-only",
        metavar="FILE",
        help="check an existing backup and exit, without writing a new one",
    )
    args = parser.parse_args()

    if args.verify_only:
        target = Path(args.verify_only)
        if not target.exists():
            print(f"no such file: {target}", file=sys.stderr)
            return 1
        verdict = verify(target)
        print(f"{target}: {verdict}")
        return 0 if verdict == "ok" else 1

    source = database_path()
    if not source.exists():
        print(f"no database at {source} — nothing to back up", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(args.dest) / f"legend-{stamp}.db"
    # Two runs inside the same second would otherwise land on the same filename
    # and the second would silently overwrite the first. Caught by running this
    # script twice in a row: no error, one file. A backup tool that destroys a
    # backup without saying so is worse than one that fails.
    suffix = 1
    while destination.exists():
        destination = Path(args.dest) / f"legend-{stamp}-{suffix}.db"
        suffix += 1

    back_up(source, destination)

    verdict = verify(destination)
    if verdict != "ok":
        # Keep the bad file for inspection but fail loudly. Deleting it would
        # destroy the evidence of what went wrong.
        print(f"BACKUP FAILED integrity check: {verdict}", file=sys.stderr)
        print(f"the suspect file is at {destination}", file=sys.stderr)
        return 1

    size_mb = destination.stat().st_size / 1_048_576
    print(f"{destination} ({size_mb:.1f} MB) verified ok")

    for removed in prune(destination.parent, args.keep):
        print(f"pruned {removed.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
