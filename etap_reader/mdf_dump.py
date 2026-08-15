"""
Dump an ETAP SQL Server database (.MDF live file or .BAK backup) into a
portable SQLite file: one table per SQL Server table, schema + data.

Never touches the source file - always works off a throwaway copy, attached
to SQL Server and detached and deleted when done.

Two SQL Servers can be on the other end and the SQL is identical for both:

- **LocalDB**, the instance ETAP installs on Windows. Named-pipe address,
  Windows authentication, started on demand. What the desktop app uses.
- **A server**, addressed by host with SA credentials. What a Linux container
  uses, running the SQL Server engine next to the app.

Which one is decided by whether a host is configured, not by guessing from the
platform - so the container path can be exercised anywhere.
"""
import os
import shutil
import subprocess
import sqlite3
import tempfile
import uuid

from . import appconfig

# Imported lazily so a deployment that only serves study result files - which
# are plain SQLite and need none of this - does not have to carry pyodbc and
# the unixODBC headers it builds against just to start up.
pyodbc = None


def _require_pyodbc():
    global pyodbc
    if pyodbc is None:
        try:
            import pyodbc as _pyodbc
        except ImportError as e:
            raise RuntimeError(
                "Reading .MDF/.BAK project databases needs pyodbc and a SQL "
                "Server to attach them to, which this deployment does not "
                "have. Study result files (.SA1S/.SA2S/.LF1S/.UL1S/.TU1S) are "
                "read directly and need neither."
            ) from e
        pyodbc = _pyodbc
    return pyodbc


DEFAULT_INSTANCE = "ETAPLocalDB19"


def _server_mode() -> bool:
    return bool(appconfig.MSSQL_HOST and appconfig.MSSQL_SA_PASSWORD)


def _run_sqlcmd(instance: str, query: str, database: str = "master"):
    if _server_mode():
        # -C trusts the engine's self-signed certificate. It is the same
        # container over loopback, so there is no third party to be trusted by
        # a certificate chain here.
        cmd = ["sqlcmd", "-S", appconfig.MSSQL_HOST, "-U", "sa",
               "-P", appconfig.MSSQL_SA_PASSWORD, "-C", "-N",
               "-d", database, "-Q", query, "-b"]
    else:
        cmd = ["sqlcmd", "-S", f"(localdb)\\{instance}", "-d", database, "-Q", query, "-b"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # The SA password is in cmd; report the query and output, never the
        # command line.
        raise RuntimeError(f"sqlcmd failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def _connection_string(instance: str, db_name: str) -> str:
    if _server_mode():
        return (
            f"DRIVER={{{appconfig.MSSQL_ODBC_DRIVER}}};"
            f"SERVER={appconfig.MSSQL_HOST};DATABASE={db_name};"
            f"UID=sa;PWD={appconfig.MSSQL_SA_PASSWORD};"
            "Encrypt=yes;TrustServerCertificate=yes;"
        )
    return (
        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
        f"SERVER=(localdb)\\{instance};DATABASE={db_name};Trusted_Connection=yes;"
    )


def _ensure_instance_running(instance: str):
    """LocalDB instances start on demand; a server is already up."""
    if _server_mode():
        return
    subprocess.run(["sqllocaldb", "start", instance], capture_output=True, text=True)


def _attach_mdf(instance: str, mdf_path: str, db_name: str):
    q = (
        f"IF DB_ID('{db_name}') IS NOT NULL DROP DATABASE [{db_name}]; "
        f"CREATE DATABASE [{db_name}] ON (FILENAME = '{mdf_path}') FOR ATTACH_REBUILD_LOG;"
    )
    _run_sqlcmd(instance, q)


def _restore_bak(instance: str, bak_path: str, db_name: str, data_dir: str):
    # Discover logical file names inside the backup, then restore with MOVE
    # so files land in our scratch data_dir instead of the server's default path.
    filelist = _run_sqlcmd(instance, f"RESTORE FILELISTONLY FROM DISK = '{bak_path}';")
    lines = [ln for ln in filelist.splitlines() if ln.strip() and "LogicalName" not in ln and "---" not in ln]
    move_clauses = []
    for ln in lines:
        cols = ln.split()
        if len(cols) < 2:
            continue
        logical_name = cols[0]
        is_log = "_log" in logical_name.lower() or "_ldf" in logical_name.lower()
        ext = "ldf" if is_log else "mdf"
        phys_path = os.path.join(data_dir, f"{db_name}_{logical_name}.{ext}")
        move_clauses.append(f"MOVE '{logical_name}' TO '{phys_path}'")
    move_sql = ", ".join(move_clauses)
    q = (
        f"IF DB_ID('{db_name}') IS NOT NULL DROP DATABASE [{db_name}]; "
        f"RESTORE DATABASE [{db_name}] FROM DISK = '{bak_path}' WITH {move_sql}, REPLACE;"
    )
    _run_sqlcmd(instance, q)


def _detach(instance: str, db_name: str):
    q = (
        f"IF DB_ID('{db_name}') IS NOT NULL BEGIN "
        f"ALTER DATABASE [{db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
        f"EXEC sp_detach_db '{db_name}'; END"
    )
    _run_sqlcmd(instance, q)


def _sql_type_to_sqlite(type_name: str) -> str:
    t = type_name.lower()
    if t in ("int", "bigint", "smallint", "tinyint", "bit"):
        return "INTEGER"
    if t in ("float", "real", "decimal", "numeric", "money", "smallmoney"):
        return "REAL"
    if t in ("varbinary", "binary", "image", "timestamp", "rowversion"):
        return "BLOB"
    return "TEXT"


def dump_to_sqlite(kind: str, source_path: str, out_sqlite_path: str,
                    instance: str = DEFAULT_INSTANCE, progress_cb=None,
                    keep_copy: bool = False) -> dict:
    """
    kind: "mdf" or "bak"
    Returns a stats dict: {tables: int, rows_total: int, table_stats: [...]}
    progress_cb(stage: str, current: int, total: int) is called periodically.
    """
    def report(stage, cur=0, total=0):
        if progress_cb:
            progress_cb(stage, cur, total)

    source_path = os.path.abspath(source_path)
    workdir = tempfile.mkdtemp(prefix="etap_mdf_")
    db_name = "etap_dump_" + uuid.uuid4().hex[:8]

    try:
        report("copying")
        if kind == "mdf":
            work_file = os.path.join(workdir, db_name + ".mdf")
            shutil.copyfile(source_path, work_file)
        elif kind == "bak":
            work_file = os.path.join(workdir, db_name + ".bak")
            shutil.copyfile(source_path, work_file)
        else:
            raise ValueError(f"Unknown kind '{kind}'")

        report("starting_instance")
        _ensure_instance_running(instance)

        report("attaching")
        if kind == "mdf":
            _attach_mdf(instance, work_file, db_name)
        else:
            _restore_bak(instance, work_file, db_name, workdir)

        cnxn = _require_pyodbc().connect(_connection_string(instance, db_name))
        cursor = cnxn.cursor()

        cursor.execute(
            "SELECT t.name FROM sys.tables t "
            "INNER JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id IN (0,1) "
            "GROUP BY t.name ORDER BY t.name"
        )
        all_tables = [row[0] for row in cursor.fetchall()]

        if os.path.exists(out_sqlite_path):
            os.remove(out_sqlite_path)
        sconn = sqlite3.connect(out_sqlite_path)
        scur = sconn.cursor()
        scur.execute("CREATE TABLE _table_index (table_name TEXT, row_count INTEGER)")

        table_stats = []
        report("dumping", 0, len(all_tables))
        for i, table in enumerate(all_tables, 1):
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
                table,
            )
            cols = cursor.fetchall()
            if not cols:
                continue
            col_defs = ", ".join(f'"{c.COLUMN_NAME}" {_sql_type_to_sqlite(c.DATA_TYPE)}' for c in cols)
            safe_table = table.replace('"', '""')
            scur.execute(f'CREATE TABLE "{safe_table}" ({col_defs})')

            col_names = ", ".join(f'[{c.COLUMN_NAME}]' for c in cols)
            row_count = 0
            try:
                cursor.execute(f'SELECT {col_names} FROM [{table}]')
                rows = cursor.fetchall()
                clean_rows = [
                    tuple(bytes(v) if isinstance(v, (bytearray, memoryview)) else v for v in row)
                    for row in rows
                ]
                if clean_rows:
                    placeholders = ", ".join("?" * len(cols))
                    scur.executemany(f'INSERT INTO "{safe_table}" VALUES ({placeholders})', clean_rows)
                row_count = len(clean_rows)
            except pyodbc.Error:
                pass

            scur.execute("INSERT INTO _table_index VALUES (?, ?)", (table, row_count))
            table_stats.append({"table": table, "rows": row_count})
            if i % 50 == 0 or i == len(all_tables):
                report("dumping", i, len(all_tables))

        sconn.commit()
        sconn.close()
        cnxn.close()

        report("detaching")
        _detach(instance, db_name)

        return {
            "tables": len(table_stats),
            "rows_total": sum(t["rows"] for t in table_stats),
            "table_stats": table_stats,
        }
    finally:
        if not keep_copy:
            shutil.rmtree(workdir, ignore_errors=True)
