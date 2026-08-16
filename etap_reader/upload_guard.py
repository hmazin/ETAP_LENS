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

from . import ha_plots

# An extension is a claim, not evidence. Each accepted file type has a header
# that actually identifies it, and checking it costs a few bytes of read.
#
# Study results are SQLite. Project databases are SQL Server, and the two
# shapes there were confirmed against real ETAP files rather than taken from
# documentation:
#   .MDF  01 0f 00 00   page 0 of a data file is its file-header page
#   .BAK  "TAPE"        Microsoft Tape Format, which is what a backup is
SQLITE_MAGIC = b"SQLite format 3\x00"
MDF_MAGIC = b"\x01\x0f\x00\x00"
BAK_MAGIC = b"TAPE"

# Duplicated from study_result.STUDY_EXTENSIONS rather than imported: that
# module imports this one, and a guard that cannot run until the thing it
# guards has loaded is the wrong way round. Kept in step by a test.
STUDY_EXTENSIONS = {".sa1s", ".sa2s", ".lf1s", ".ul1s", ".tu1s", ".ha1s", ".grds"}
MODEL_EXTENSIONS = {".mdf", ".bak"}

# Files that may be uploaded but never opened on their own - a harmonic run's
# plot curves, which mean nothing without the .HA1S they belong to. ha_plots
# imports nothing from this package, so naming it here costs no cycle and
# saves keeping a second copy of the list.
COMPANION_EXTENSIONS = set(ha_plots.COMPANION_EXTENSIONS)

_MAGIC_BY_EXT = {
    ".mdf": (MDF_MAGIC, "a SQL Server project database (.MDF)"),
    ".bak": (BAK_MAGIC, "a SQL Server backup (.BAK)"),
}


class RejectedUpload(Exception):
    """Raised with a message meant to be shown to whoever uploaded the file."""


def check_extension(filename: str, allowed=None):
    ext = os.path.splitext(filename)[1].lower()
    allowed = STUDY_EXTENSIONS if allowed is None else allowed
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


def check_model_magic(path: str, ext: str):
    """Header check for a SQL Server project database.

    There is no cheap equivalent of check_opens() here - parsing an MDF means
    attaching it to a running engine, which is the import itself. So this is
    the last check before that, and it only has to catch the obvious: a file
    that was never a project database at all.
    """
    magic, description = _MAGIC_BY_EXT[ext]
    try:
        with open(path, "rb") as f:
            header = f.read(len(magic))
    except OSError as e:
        raise RejectedUpload(f"Could not read the uploaded file: {e}")
    if header != magic:
        raise RejectedUpload(
            f"That does not look like {description}. Check you picked the "
            "project database rather than a study result, a report, or the "
            ".oti pointer beside it."
        )


def validate(path: str, filename: str, allowed_extensions=None):
    """Full pre-flight. Raises RejectedUpload with a message for the user."""
    ext = check_extension(filename, allowed_extensions)
    if ext in MODEL_EXTENSIONS:
        return check_model_magic(path, ext)
    check_is_sqlite(path)
    return check_opens(path)


def check_companion_name(filename: str, primary_filename: str) -> str:
    """A companion is only ever "<the primary's stem>.<companion ext>".

    This is what bounds the feature: a session can upload a .HA1S and at most
    one file per companion extension beside it, because every other name is
    refused. Without the stem check, "upload something next to this study"
    would be an invitation to write arbitrary names into the session's
    directory, and a name is the one part of an upload the server does not
    otherwise control.
    """
    # A bare name, not a path. Comparing basenames would accept
    # "../FS_H01.fspdb" for a primary called FS_H01.HA1S, and the caller joins
    # the name it was given onto a directory - so the separator has to be
    # refused here rather than assumed away by whatever ran before.
    if filename != os.path.basename(filename) or any(c in filename for c in "\\/"):
        raise RejectedUpload(f"'{filename}' is not a plain file name.")

    ext = os.path.splitext(filename)[1].lower()
    if ext not in COMPANION_EXTENSIONS:
        raise RejectedUpload(
            f"{ext or 'That file type'} is not a study companion. Expected one of "
            f"{', '.join(sorted(COMPANION_EXTENSIONS))}.")

    stem = os.path.splitext(filename)[0]
    primary_stem = os.path.splitext(os.path.basename(primary_filename))[0]
    if stem.lower() != primary_stem.lower():
        raise RejectedUpload(
            f"'{filename}' does not belong to '{primary_filename}'. ETAP names a run's "
            f"plot files after the study case, so the two must share a name.")
    return ext


def validate_companion(path: str, filename: str, primary_filename: str):
    """Pre-flight for a plot companion. Same content checks as a study - it
    is opened with sqlite3 exactly the same way - plus the name rule."""
    check_companion_name(filename, primary_filename)
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
