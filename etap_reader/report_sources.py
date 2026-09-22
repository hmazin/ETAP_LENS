"""Preserve original result bytes before the browsing importer edits its copy."""
import hashlib
import os
from pathlib import Path
import shutil
import re
import sqlite3
import tempfile
import time
import uuid
from . import report_headers

STUDIES = {1: "Device Duty", 2: "Unbalanced Load Flow", 3: "ANSI Half-Cycle / Momentary",
           4: "ANSI 1.5–4 Cycle", 5: "ANSI 30-Cycle / Minimum Fault"}

# .UL1S has no per-run StudyType column the way ISCStudyCase does for SC - it's
# a single report family, so this table's presence/row-count stands in for
# "is this a genuine, non-empty unbalanced load flow result".
UL1S_STUDY_TYPE = 2
UL1S_CHECK_TABLE = "LFSumTotalLF3PH"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def preserve(path, session, objects):
    suffix = Path(path).suffix.lower()
    if suffix not in (".sa1s", ".sa2s", ".ul1s"):
        return None
    for sidecar in ("-wal", "-journal"):
        if os.path.exists(path + sidecar) and os.path.getsize(path + sidecar):
            raise ValueError("Close the study in ETAP and save it before generating reports.")
    before = os.stat(path)
    with tempfile.TemporaryDirectory(prefix="etap-source-") as directory:
        copy = os.path.join(directory, "study.sqlite")
        shutil.copyfile(path, copy)
        after = os.stat(path)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("The study changed while it was being read. Try again after saving it.")
        with open(copy, "rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                raise ValueError("Reports require a valid SQLite study result.")
        conn = sqlite3.connect(Path(copy).as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            deadline = time.monotonic() + 15
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
            conn.execute("PRAGMA trusted_schema=OFF")
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("The study failed its SQLite integrity check.")
            if suffix == ".ul1s":
                if UL1S_CHECK_TABLE not in tables:
                    raise ValueError("The study is missing its load flow result table.")
                count = conn.execute(f'SELECT COUNT(*) FROM "{UL1S_CHECK_TABLE}"').fetchone()[0]
                if not count:
                    raise ValueError("The study contains no load flow results.")
                study_type = UL1S_STUDY_TYPE
            else:
                rows = conn.execute('SELECT DISTINCT StudyType FROM ISCStudyCase LIMIT 3').fetchall()
                if any(not re.fullmatch(r"[+-]?\d+", str(r[0]).strip()) for r in rows):
                    raise ValueError("The study contains a missing or invalid StudyType.")
                types = {int(r[0]) for r in rows}
                if len(types) != 1:
                    raise ValueError("The study contains ambiguous StudyType values.")
                study_type = types.pop()
            headers = report_headers.read_values(conn)
        finally:
            conn.close()
        digest = sha256(copy)
        key = f"reports/sources/{session}/{uuid.uuid4().hex}{suffix}"
        objects.upload_from(copy, key)
    return {"key": key, "sha256": digest, "study_type": study_type,
            "study_name": STUDIES.get(study_type, f"Study type {study_type}"),
            "table_count": len(tables), "tables": tables, "bytes": before.st_size, "headers": headers}
