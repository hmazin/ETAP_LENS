"""
ETAP analysis result files (.SA1S, .SA2S, ...) are plain SQLite databases
(ETAP's Etaps.ini has OutputToSQLite=1) - unlike the project .OTI/.MDF, no
SQL Server LocalDB dance is needed. We just copy the file and index it the
same way as a project dump, so the rest of the app (categories, table
browser, etc.) treats it identically.
"""
import os
import sqlite3

from . import appconfig, ground_grid, ha_plots, time_domain, upload_guard

# extension -> (friendly label, which curated category set in categories.py applies)
STUDY_EXTENSIONS = {
    ".sa1s": ("Short Circuit - ANSI Duty", "sc_duty"),
    ".sa2s": ("Short Circuit - Fault Currents", "sc_fault"),
    ".lf1s": ("Load Flow - Balanced", "load_flow"),
    ".ul1s": ("Load Flow - Unbalanced (3-Phase)", "load_flow_unbalanced"),
    ".tu1s": ("Load Flow - Time Domain (TDLF)", "time_domain"),
    ".ha1s": ("Harmonic Analysis", "harmonics"),
    ".grds": ("Ground Grid", "ground_grid"),
}

# ETAP's own report generator writes literal spacer rows into these result
# tables to create blank lines in the printed report (e.g. FromBus =
# "###<<<BlankLine>>>###", ToBus = "abc", every numeric column 0). They carry
# no engineering data, so we drop them on import rather than showing every
# view a couple thousand rows of print-formatting noise.
BLANK_LINE_MARKER = "BlankLine"

# Only text-typed columns can hold the marker, and restricting the scan to
# them matters: a time-domain result set has ~half a million rows x 53 mostly
# numeric columns, and LIKE-ing every one of them costs minutes.
_TEXT_DECLS = ("CHAR", "TEXT", "CLOB")


def is_study_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in STUDY_EXTENSIONS


def study_info(path: str):
    label, category_set = STUDY_EXTENSIONS[os.path.splitext(path)[1].lower()]
    return {"label": label, "category_set": category_set}


def _strip_blank_line_rows(conn: sqlite3.Connection, tables) -> int:
    """Delete report-spacer rows (see BLANK_LINE_MARKER) from every table
    that has one. Returns the total number of rows removed."""
    removed = 0
    for t in tables:
        cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{t}")').fetchall()
                if any(d in (row[2] or "").upper() for d in _TEXT_DECLS)]
        if not cols:
            continue
        where = " OR ".join(f'"{c}" LIKE ?' for c in cols)
        params = [f"%{BLANK_LINE_MARKER}%"] * len(cols)
        cur = conn.execute(f'DELETE FROM "{t}" WHERE {where}', params)
        removed += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return removed


def import_study_to_sqlite(source_path: str, out_sqlite_path: str, progress_cb=None,
                           lite: bool = None) -> dict:
    """lite=None follows the deployment default (see appconfig.LITE_CACHE);
    pass a bool to force it either way."""
    if lite is None:
        lite = appconfig.LITE_CACHE

    def report(stage):
        if progress_cb:
            progress_cb(stage, 0, 0)

    report("copying")
    src = sqlite3.connect(f"file:{os.path.abspath(source_path)}?mode=ro", uri=True)
    try:
        if os.path.exists(out_sqlite_path):
            os.remove(out_sqlite_path)
        dst = sqlite3.connect(out_sqlite_path)
        try:
            src.backup(dst)
        finally:
            pass
    finally:
        src.close()

    # File size bounds how much data there can be but not how long working
    # over it takes, so a public instance puts a wall-clock ceiling on the
    # import rather than letting one pathological upload hold a worker.
    if appconfig.IS_HOSTED:
        upload_guard.deadline_guard(dst, appconfig.DERIVE_TIMEOUT_SECONDS)

    report("scanning")
    # Capture the real table list *before* creating _table_index, so the
    # index doesn't end up listing (and mis-counting) itself.
    tables = [r[0] for r in dst.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != '_table_index'"
    ).fetchall()]

    _strip_blank_line_rows(dst, tables)

    source_ext = os.path.splitext(source_path)[1].lower()

    # The one table a .GRDS holds needs its binary geometry column and its
    # epoch timestamp made presentable before anything tries to show it.
    if source_ext == ".grds":
        ground_grid.normalize(dst)

    # A harmonic run's curves live in sibling .fspdb/.hfpdb files (see
    # ha_plots). Folding them in here, before indexing, means they appear in
    # _table_index like any other table.
    plots = {}
    if source_ext == ".ha1s":
        report("attaching plots")
        plots = ha_plots.attach(dst, source_path)
        if plots:
            tables = [r[0] for r in dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != '_table_index'"
            ).fetchall()]

    # Time-domain results are keyed by integer IDs and are far too large to
    # read raw, so we add name-resolved and rolled-up tables before indexing
    # (they need to appear in _table_index like any other table).
    derived = {}
    is_td = time_domain.is_time_domain(dst)
    if is_td:
        report("deriving")
        derived = time_domain.derive(dst, lite=lite, progress_cb=progress_cb)
        tables = [r[0] for r in dst.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != '_table_index'"
        ).fetchall()]

    report("indexing")
    dst.execute("DROP TABLE IF EXISTS _table_index")
    dst.execute("CREATE TABLE _table_index (table_name TEXT, row_count INTEGER)")

    table_stats = []
    for t in tables:
        cnt = dst.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        dst.execute("INSERT INTO _table_index VALUES (?, ?)", (t, cnt))
        table_stats.append({"table": t, "rows": cnt})
    dst.commit()
    dst.close()

    return {
        "tables": len(table_stats),
        "rows_total": sum(t["rows"] for t in table_stats),
        "table_stats": table_stats,
        "derived_tables": derived,
        # Empty when the run had no plots saved with it, or - the usual case
        # for an upload - when only the .HA1S itself was available.
        "attached_plots": plots,
        # Only meaningful for time-domain results - nothing else has per-step
        # tables worth dropping, so the flag would be misleading elsewhere.
        "lite_cache": bool(lite and is_td),
    }
