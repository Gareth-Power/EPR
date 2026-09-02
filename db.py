"""Database location, connections and schema migrations for EPR.

The *code* (this repo / the Docker image) is separate from the *data*. Everything
that is specific to a deployment lives under one directory:

    $EPR_DATA_DIR/           (default: ./data next to the code; /data in Docker)
      epr.db                 SQLite database
      epr.db-wal, epr.db-shm
      files/<patient_id>/    uploaded documents
      changes.log            audit log
      backups/               automatic pre-migration snapshots of epr.db

Nothing here is ever written back into the code directory, so pushing a new
version of the code (new image, new checkout) never touches patient data.
"""
import datetime
import glob
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")

DATA_DIR = os.path.abspath(os.environ.get("EPR_DATA_DIR") or os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "epr.db")
FILES_DIR = os.path.join(DATA_DIR, "files")
CHANGELOG_PATH = os.path.join(DATA_DIR, "changes.log")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)


def connect():
    """Open a connection with the pragmas every caller wants."""
    ensure_dirs()
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")   # readers don't block on a writer
    con.execute("PRAGMA busy_timeout = 15000")
    return con


def _migrations():
    """[(version:int, name:str, path:str), ...] sorted by version."""
    out = []
    for path in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql"))):
        head = os.path.basename(path).split("_", 1)[0]
        if head.isdigit():
            out.append((int(head), os.path.basename(path), path))
    return out


def migrate(con, log=print):
    """Bring the database up to the newest migration version.

    - A brand-new database (user_version 0) is built from scratch, no backup.
    - An existing database is snapshotted to backups/ before any change.
    - Each migration runs in its own transaction; a failure leaves the DB at
      the previous version with the backup intact.
    """
    steps = _migrations()
    if not steps:
        return
    target = steps[-1][0]
    current = con.execute("PRAGMA user_version").fetchone()[0]
    if current >= target:
        return

    if current > 0:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(BACKUP_DIR, f"epr.db.v{current}.{stamp}")
        con.commit()
        with sqlite3.connect(dest) as bkp:
            con.backup(bkp)
        log(f"[migrate] backed up v{current} database -> {dest}")

    for version, name, path in steps:
        if version <= current:
            continue
        log(f"[migrate] applying {name}  (v{current} -> v{version})")
        with open(path, "r", encoding="utf-8") as fh:
            body = fh.read()
        # One transaction per migration: the BEGIN/COMMIT must live inside the
        # script because executescript() does no implicit transaction control.
        script = f"BEGIN;\n{body}\nPRAGMA user_version = {version};\nCOMMIT;"
        try:
            con.executescript(script)
        except Exception:
            con.rollback()
            log(f"[migrate] FAILED on {name}; database left at v{current}, backup kept")
            raise
        current = version

    log(f"[migrate] database is at v{target}")
