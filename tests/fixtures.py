"""
Synthetic ETAP result files, built in a temp folder.

Real .HA1S/.GRDS files cannot go in the repo - they are hundreds of
kilobytes of a client's model. These fakes carry only the structure the code
under test actually reads, which also lets a test build shapes the one
project we have does not contain: a bus whose name has an underscore in it,
a companion with no curves, a grid that was never run.
"""
import os
import sqlite3


def _connect(path):
    if os.path.exists(path):
        os.remove(path)
    return sqlite3.connect(path)


def make_ha1s(path, freq_scan_rows=3, system_info_rows=0):
    """A harmonic result file. Defaults to the frequency-scan flavor; pass
    system_info_rows to get the harmonic-load-flow one instead."""
    conn = _connect(path)
    conn.execute("CREATE TABLE HAFreqScan (Code TEXT, BusID TEXT, Freq REAL, Mag REAL, Angle REAL)")
    conn.executemany("INSERT INTO HAFreqScan VALUES (?,?,?,?,?)",
                     [("A", "PCC1", 60.0 * i, 1.5 * i, 0.0) for i in range(1, freq_scan_rows + 1)])
    conn.execute("CREATE TABLE HASystemInfo (Code TEXT, FromBus TEXT, kV REAL, VTHD REAL)")
    conn.executemany("INSERT INTO HASystemInfo VALUES (?,?,?,?)",
                     [("A", f"Bus{i}", 0.208, 2.0 * i) for i in range(1, system_info_rows + 1)])
    conn.execute("CREATE TABLE Headr (Project TEXT, Loc TEXT)")
    conn.execute("INSERT INTO Headr VALUES ('Test', 'Nowhere')")
    conn.commit()
    conn.close()
    return path


def make_plot_db(path, curves, devices, system_freq=60.0):
    """A .fspdb/.hfpdb.

    `curves` is [(table_name, n_points, has_angle), ...] and `devices` is
    [(DeviceID, IID), ...]. Passing curves=[] builds the empty-companion case
    that a real project turned out to contain.
    """
    conn = _connect(path)
    conn.execute("CREATE TABLE DeviceID_IID (DeviceID TEXT, IID INTEGER)")
    # ETAP writes one row per curve table, so ids repeat - the code has to
    # cope with that rather than assuming a unique index.
    conn.executemany("INSERT INTO DeviceID_IID VALUES (?,?)", devices)
    conn.execute("CREATE TABLE SystemFrequency (SystemFreq REAL)")
    conn.execute("INSERT INTO SystemFrequency VALUES (?)", (system_freq,))

    for table, points, has_angle in curves:
        cols = "ValueX REAL, ValueY REAL" + (", Angle REAL" if has_angle else "")
        conn.execute(f'CREATE TABLE "{table}" ({cols})')
        placeholders = "?,?,?" if has_angle else "?,?"
        rows = [(float(i), float(i) * 2) + ((float(i) * 3,) if has_angle else ())
                for i in range(points)]
        conn.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})', rows)
    conn.commit()
    conn.close()
    return path


def make_grds(path, rows):
    """A ground grid file. `rows` is [(ID, RG, GPR, RunDate, data), ...]."""
    conn = _connect(path)
    conn.execute(
        "CREATE TABLE GroundGrid (IID TEXT, ID TEXT, StudyMode INTEGER, data, "
        "RG REAL, GPR REAL, RunDate REAL, RunBy TEXT)")
    conn.executemany(
        "INSERT INTO GroundGrid VALUES (?,?,?,?,?,?,?,?)",
        [(f"{i}-0", rid, 3, data, rg, gpr, run_date, "tester")
         for i, (rid, rg, gpr, run_date, data) in enumerate(rows)])
    conn.commit()
    conn.close()
    return path


def make_lf1s(path):
    """A load flow result, for checking the new code leaves other types be."""
    conn = _connect(path)
    conn.execute("CREATE TABLE LFR (Code TEXT, IDFrom TEXT, kV REAL)")
    conn.execute("INSERT INTO LFR VALUES ('A', 'Bus1', 13.8)")
    conn.commit()
    conn.close()
    return path
