"""
Ground grid results (.GRDS) - two fixes applied at import so the one table in
the file is readable.

A .GRDS is a single GroundGrid table, one row per grid, holding what an
IEEE 80 study is actually for: resistance to remote earth, ground potential
rise, and calculated vs. tolerable mesh and step voltages with the coordinates
of each worst point. Those columns are fine as they stand. Two are not:

- `data` is ETAP's serialized conductor and rod geometry - tens of kilobytes
  of hex per row, in a layout we cannot read. Left in place it fills the table
  view with one unreadable cell and pushes the results off the screen. We drop
  it and keep its size, so the geometry's absence is visible rather than
  silent.
- `RunDate` is a Unix timestamp stored as a float, which renders as
  "1,778,879,078" - a number an engineer has no way to read as a date.
"""
import sqlite3
import time

_BLOB_COLUMN = "data"
_BLOB_REPLACEMENT = "GeometryBytes"
_DATE_COLUMN = "RunDate"


def _iso(value):
    """ETAP writes seconds since the epoch as a float. 0 means never run."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return value
    if seconds <= 0:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(seconds))
    except (ValueError, OSError, OverflowError):
        return None


def normalize(conn: sqlite3.Connection) -> bool:
    """Rewrite GroundGrid in place. False if the file has no such table."""
    cols = [r[1] for r in conn.execute('PRAGMA table_info("GroundGrid")').fetchall()]
    if not cols:
        return False

    rows = conn.execute('SELECT * FROM "GroundGrid"').fetchall()

    out_cols = [_BLOB_REPLACEMENT if c == _BLOB_COLUMN else c for c in cols]
    blob_at = cols.index(_BLOB_COLUMN) if _BLOB_COLUMN in cols else None
    date_at = cols.index(_DATE_COLUMN) if _DATE_COLUMN in cols else None

    out_rows = []
    for row in rows:
        values = list(row)
        if blob_at is not None:
            values[blob_at] = len(values[blob_at]) if values[blob_at] else 0
        if date_at is not None:
            values[date_at] = _iso(values[date_at])
        out_rows.append(values)

    conn.execute('DROP TABLE "GroundGrid"')
    conn.execute("CREATE TABLE GroundGrid (%s)" % ", ".join(f'"{c}"' for c in out_cols))
    conn.executemany(
        "INSERT INTO GroundGrid VALUES (%s)" % ",".join("?" * len(out_cols)), out_rows)
    conn.commit()
    return True
