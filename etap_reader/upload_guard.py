"""
Checks applied to uploaded files before anything opens them properly.

A public instance runs sqlite3 against binaries strangers hand it, and the
import path is not shy: it reads every table, joins the big ones, and builds
new tables from the results. Everything here exists to make sure that only
happens to something that is actually an ETAP study result of a sane size.

What protects the service, in order of how much work each does:

1. The upload size cap, enforced by the signed URL at the bucket, so
   oversized files are refused before they cost anything.
2. The magic-byte check here - the cheapest way to reject "a .TU1S" that is
   really a video, and it costs 16 bytes of read.
3. The derive deadline, because file size bounds row count only loosely and
   a pathological schema can still make a join crawl.
"""
import os
import sqlite3
import time

# Every ETAP study result file is a SQLite database; this is SQLite's own
# header string, and it is the actual test of "is this the kind of file we
# think it is". An extension is a claim, not evidence.
SQLITE_MAGIC = b"SQLite format 3\x00"

# Extensions a hosted instance will accept. Project models (.oti/.mdf/.bak)
# are SQL Server and need Windows, so accepting them here would only produce
# a confusing failure later.
HOSTED_EXTENSIONS = {".sa1s", ".sa2s", ".lf1s", ".ul1s", ".tu1s"}


class RejectedUpload(Exception):
    """Raised with a message meant to be shown to whoever uploaded the file."""


def check_extension(filename: str, allowed=None):
    ext = os.path.splitext(filename)[1].lower()
    allowed = HOSTED_EXTENSIONS if allowed is None else allowed
    if ext not in allowed:
        raise RejectedUpload(
            f"{ext or 'That file type'} is not supported here. Upload an ETAP study "
            f"result file ({', '.join(sorted(allowed))})."
        )
    return ext


def check_is_sqlite(path: str):
    try:
        with open(path, "rb") as f:
            header = f.read(len(SQLITE_MAGIC))
    except OSError as e:
        raise RejectedUpload(f"Could not read the uploaded file: {e}")
    if header != SQLITE_MAGIC:
        raise RejectedUpload(
            "That does not look like an ETAP study result file. They are SQLite "
            "databases, and this one has a different format - check you picked the "
            "study output rather than the project model or a report."
        )


def check_opens(path: str, max_tables: int = 2000):
    """Confirm SQLite will actually parse the schema, and that it is not an
    absurd number of tables. Cheap - reads the schema, not the data."""
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise RejectedUpload(f"That file could not be opened as a database: {e}")
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    except sqlite3.DatabaseError as e:
        raise RejectedUpload(f"That file is not a readable database: {e}")
    finally:
        conn.close()

    if not names:
        raise RejectedUpload("That database has no tables in it.")
    if len(names) > max_tables:
        raise RejectedUpload(
            f"That database has {len(names)} tables, more than this app will process.")
    return names


def validate(path: str, filename: str, allowed_extensions=None):
    """Full pre-flight. Raises RejectedUpload with a message for the user."""
    check_extension(filename, allowed_extensions)
    check_is_sqlite(path)
    return check_opens(path)


def deadline_guard(conn: sqlite3.Connection, seconds: float):
    """Abort long-running SQL on a connection once `seconds` have passed.

    File size bounds how much data there can be, but not how long a query
    takes over it, so an import that would otherwise run for minutes gets
    stopped and reported instead of holding a worker open."""
    if not seconds or seconds <= 0:
        return
    stop_at = time.monotonic() + seconds

    def _handler():
        # Non-zero aborts the running statement with sqlite3.OperationalError.
        return 1 if time.monotonic() > stop_at else 0

    # Checked every N virtual-machine instructions; small enough to be
    # responsive, large enough not to matter.
    conn.set_progress_handler(_handler, 100_000)
