"""
Harmonic plot curves (.fspdb / .hfpdb) folded into the .HA1S they belong to.

ETAP writes a harmonic run as four files sharing a stem: the results
(.HA1S), a frequency-scan plot database (.fspdb), a waveform/spectrum plot
database (.hfpdb), and a proprietary binary per plot (.fsp/.hfp) holding the
plot's *settings* - axis ranges, colours, which curves are ticked. The
settings we ignore; the curve data is ordinary SQLite and is the part worth
having.

These are attached to the .HA1S rather than opened on their own because on
their own they are anonymous. A .fspdb is a pile of tables called things like
"Buses_PCC1_Z Magnitude_Hz_4116" - two columns, no header, no project, no
study case, no indication of which scan produced them. Next to the .HA1S they
become "the driving-point impedance ETAP swept at PCC1 in this run", which is
a thing an engineer can read.

What gets added to the cache:

- every curve table, under its original ETAP name, so All Tables shows what
  the file actually contains;
- HAPlotCurves, one row per point across all of them, long-format and named,
  so a plot can be exported or charted without knowing the naming scheme;
- HAPlotIndex, one row per curve, as the table of contents.
"""
import os
import re
import sqlite3

# <stem>.HA1S -> which sibling extensions carry its curves, and what kind of
# plot each holds. Frequency scan and waveform/spectrum are separate files
# because ETAP runs them as separate plots, not because they differ in format.
COMPANION_EXTENSIONS = {
    ".fspdb": "Frequency Scan",
    ".hfpdb": "Harmonic Waveform / Spectrum",
}

# Tables in a plot database that are metadata rather than a curve.
_NON_CURVE_TABLES = {"DeviceID_IID", "SystemFrequency"}

# A curve table name ends in the device's internal id, e.g. "..._4116".
_IID_SUFFIX = re.compile(r"_(\d+)$")

# What the trailing part of a curve name says the X axis is. "Waveform" has no
# axis segment at all - it is always against time.
_X_AXIS_SUFFIXES = {"Hz": "Frequency (Hz)", "Order": "Harmonic Order"}


def companions_for(ha1s_path: str):
    """Sibling plot databases sitting next to a .HA1S, as
    [(path, plot_kind), ...]. Empty when there are none - which is the normal
    case for an uploaded file, where only the .HA1S itself crossed the wire.

    Resolved by reading the directory rather than by testing constructed
    paths. ETAP writes the results upper-case (.HA1S) and the companions
    lower-case (.fspdb), so neither can be assumed - and os.path.isfile would
    paper over that on Windows while failing on the Linux container the
    hosted build runs in. Listing once behaves the same on both, and reports
    the name the file actually has.
    """
    folder = os.path.dirname(ha1s_path) or "."
    stem = os.path.splitext(os.path.basename(ha1s_path))[0].lower()
    try:
        entries = os.listdir(folder)
    except OSError:
        return []

    found = []
    for fn in entries:
        name, ext = os.path.splitext(fn)
        kind = COMPANION_EXTENSIONS.get(ext.lower())
        if kind and name.lower() == stem:
            found.append((os.path.join(folder, fn), kind))
    # Directory order is arbitrary; a stable order keeps HAPlotIndex stable
    # between imports of the same study.
    found.sort(key=lambda pair: os.path.basename(pair[0]).lower())
    return found


def _parse_curve_name(table: str, devices):
    """Split "Buses_PCC1_Z Magnitude_Hz_4116" into its parts.

    Driven by the file's own DeviceID_IID map rather than by splitting on
    underscores, because a device named "PCC_1" or "Bus_3" would make a naive
    split silently mis-attribute its curves.

    Returns (device_type, device_id, curve, x_axis) or None if the name does
    not look like a curve.
    """
    m = _IID_SUFFIX.search(table)
    if not m:
        return None
    iid = int(m.group(1))
    body = table[:m.start()]

    device_id = devices.get(iid)
    if not device_id:
        return None
    marker = "_" + device_id + "_"
    at = body.find(marker)
    if at < 0:
        return None

    device_type = body[:at]
    curve = body[at + len(marker):]

    x_axis = "Time (ms)"
    tail = curve.rsplit("_", 1)
    if len(tail) == 2 and tail[1] in _X_AXIS_SUFFIXES:
        curve, x_axis = tail[0], _X_AXIS_SUFFIXES[tail[1]]

    return device_type, device_id, curve, x_axis


def _read_devices(conn: sqlite3.Connection):
    """{IID: DeviceID}. The table holds one row per curve, so ids repeat."""
    try:
        rows = conn.execute("SELECT DeviceID, IID FROM DeviceID_IID").fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {int(iid): name for name, iid in rows if iid is not None}


def _system_frequency(conn: sqlite3.Connection):
    try:
        row = conn.execute("SELECT SystemFreq FROM SystemFrequency LIMIT 1").fetchone()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def attach(dst: sqlite3.Connection, ha1s_path: str) -> dict:
    """Fold every sibling plot database into an already-copied .HA1S cache.

    Returns a summary dict; {} when there were no companions, so a caller can
    tell "no plots were saved with this run" from "plots were attached".
    """
    companions = companions_for(ha1s_path)
    if not companions:
        return {}

    dst.execute("DROP TABLE IF EXISTS HAPlotCurves")
    dst.execute(
        "CREATE TABLE HAPlotCurves ("
        "Plot TEXT, DeviceType TEXT, DeviceID TEXT, Curve TEXT, "
        "XAxis TEXT, X REAL, Y REAL, Angle REAL, SourceTable TEXT)"
    )
    dst.execute("DROP TABLE IF EXISTS HAPlotIndex")
    dst.execute(
        "CREATE TABLE HAPlotIndex ("
        "Plot TEXT, DeviceType TEXT, DeviceID TEXT, Curve TEXT, "
        "XAxis TEXT, Points INTEGER, SystemFreq REAL, SourceFile TEXT, SourceTable TEXT)"
    )

    summary = {"files": [], "curves": 0, "points": 0}

    for path, plot_kind in companions:
        try:
            src = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
        except sqlite3.Error:
            # A companion that will not open is not worth failing the whole
            # import over - the results themselves are already in hand.
            continue
        try:
            curves, points = _attach_one(dst, src, path, plot_kind)
        except sqlite3.DatabaseError:
            continue
        finally:
            src.close()
        summary["files"].append({"file": os.path.basename(path), "plot": plot_kind,
                                 "curves": curves, "points": points})
        summary["curves"] += curves
        summary["points"] += points

    dst.commit()
    return summary if summary["curves"] else {}


def _attach_one(dst, src, path, plot_kind):
    devices = _read_devices(src)
    freq = _system_frequency(src)
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]

    curves = points = 0
    for table in tables:
        if table in _NON_CURVE_TABLES:
            continue
        parsed = _parse_curve_name(table, devices)
        if not parsed:
            continue
        device_type, device_id, curve, x_axis = parsed

        cols = [r[1] for r in src.execute(f'PRAGMA table_info("{table}")').fetchall()]
        has_angle = "Angle" in cols
        select = ('SELECT ValueX, ValueY, Angle FROM "%s"' if has_angle
                  else 'SELECT ValueX, ValueY, NULL FROM "%s"') % table
        rows = src.execute(select).fetchall()
        if not rows:
            continue

        # The original table, under ETAP's own name, so All Tables shows the
        # file's real contents rather than only our reshaped view of it.
        dst.execute(f'DROP TABLE IF EXISTS "{table}"')
        dst.execute(f'CREATE TABLE "{table}" (ValueX REAL, ValueY REAL'
                    + (", Angle REAL)" if has_angle else ")"))
        dst.executemany(
            f'INSERT INTO "{table}" VALUES ({"?,?,?" if has_angle else "?,?"})',
            [r if has_angle else r[:2] for r in rows],
        )

        dst.executemany(
            "INSERT INTO HAPlotCurves VALUES (?,?,?,?,?,?,?,?,?)",
            [(plot_kind, device_type, device_id, curve, x_axis, r[0], r[1], r[2], table)
             for r in rows],
        )
        dst.execute(
            "INSERT INTO HAPlotIndex VALUES (?,?,?,?,?,?,?,?,?)",
            (plot_kind, device_type, device_id, curve, x_axis, len(rows), freq,
             os.path.basename(path), table),
        )
        curves += 1
        points += len(rows)

    return curves, points
