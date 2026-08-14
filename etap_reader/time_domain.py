"""
Post-import derivation for ETAP Time-Domain Load Flow results (.TU1S).

A TDLF run is a load flow solved once per time step across a whole profile -
typically 8760 hourly steps for a calendar year. ETAP stores that as narrow
fact tables keyed by integers: TDBranchResult rows carry a DeviceIID and a
ResultID and nothing else identifying, so the raw tables are unreadable on
their own (and TDBranchResult alone is steps x devices rows - half a million
for a mid-size collector system).

So on import we resolve those keys into names/timestamps and roll the year up
into the three views an engineer actually opens first:

    TDStudySummary   - 1 row per metric: annual headline numbers
    TDMonthlySummary - 12 rows: energy, peak, losses, extremes per month
    TDDeviceSummary  - 1 row per branch (+ per winding for 3-winding
                       transformers): peak/average loading and when it peaked
    TDSystemHourly   - 1 row per step: the system-wide time series
    TDBranchHourly   - 1 row per step per branch: the full per-device series,
                       denormalized with names and timestamps

plus the annual AC loss report (see LOSS CONVENTION below):

    TDEnergyBalance  - generation at the units, losses, output at the POI
    TDLossSummary    - annual loss energy per equipment class
    TDDeviceLosses   - annual loss energy per individual branch
    TDLossHourly     - 1 row per step: loss split by equipment class

The raw TD* tables are left untouched alongside these.

LOSS CONVENTION
---------------
ETAP reports both terminals of a branch as power flowing *into* the branch
from the bus at that end, so the loss in the branch is simply From + To
(Prim + Sec + Ter for a three-winding transformer) - the two nearly-equal
magnitudes carry opposite signs and what survives is the loss. Summing that
across every branch reproduces ETAP's own MWLossPh* system total to within
float rounding, which is the check that pins the convention down.
"""
import sqlite3

# ETAP writes -999 into worst-case tables for devices it could not evaluate
# (e.g. cables with no ampacity entered, so no loading percentage exists).
NO_RESULT = -900.0

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def is_time_domain(conn: sqlite3.Connection) -> bool:
    """A .TU1S always carries the step index and the system-wide result set."""
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return {"TDTimeID", "TDSysResult"}.issubset(names)


def _scalar(conn, sql, default=None):
    row = conn.execute(sql).fetchone()
    return row[0] if row and row[0] is not None else default


def _hours_per_step(conn) -> float:
    """Energy = sum(MW) * hours-per-step. Almost always 1.0, but ETAP allows
    sub-hourly and multi-hour steps, and getting this wrong silently scales
    every MWh figure."""
    try:
        v = _scalar(conn, "SELECT SimulationHoursperStep FROM TDStudyCaseInfo LIMIT 1", 1.0)
        return float(v) if v and float(v) > 0 else 1.0
    except sqlite3.Error:
        return 1.0


def _r(v, places):
    """Round for presentation. These are terminal summary tables - nobody
    re-aggregates them, and 1 kWh (3 dp on MWh) is far finer than ETAP's
    single-precision storage supports anyway."""
    return None if v is None else round(v, places)


def _has(conn, table) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


# --------------------------------------------------------------------------
# system-wide hourly series
# --------------------------------------------------------------------------

_SYSTEM_HOURLY_SQL = """
CREATE TABLE TDSystemHourly AS
SELECT
    t.TimeID                                                   AS Step,
    t.Time                                                     AS Time,
    CAST(substr(t.Time, 1, 2) AS INTEGER)                      AS MonthNo,
    CAST(substr(t.Time, 12, 2) AS INTEGER)                     AS HourOfDay,
    s.WindGeneration                                           AS WindGenMW,
    s.SolarGeneration                                          AS SolarGenMW,
    s.WindGeneration + s.SolarGeneration                        AS RenewableGenMW,
    s.TotalLoadMWPhA + s.TotalLoadMWPhB + s.TotalLoadMWPhC      AS LoadMW,
    s.TotalLoadMvarPhA + s.TotalLoadMvarPhB + s.TotalLoadMvarPhC AS LoadMvar,
    s.TotalSourceMWPhA + s.TotalSourceMWPhB + s.TotalSourceMWPhC AS SourceMW,
    s.TotalSourceMvarPhA + s.TotalSourceMvarPhB + s.TotalSourceMvarPhC AS SourceMvar,
    s.MWLossPhA + s.MWLossPhB + s.MWLossPhC                     AS LossMW,
    s.MvarLossPhA + s.MvarLossPhB + s.MvarLossPhC               AS LossMvar,
    s.MaxLLBusVPhAB                                            AS MaxBusVPercent,
    s.MaxLLBusVDeviceID                                        AS MaxBusVDevice,
    s.MaxLLBusNominalKV                                        AS MaxBusNominalKV,
    s.MinLLBusVPhAB                                            AS MinBusVPercent,
    s.MinLLBusVDeviceID                                        AS MinBusVDevice,
    s.MinLLBusNominalKV                                        AS MinBusNominalKV,
    s.MaxBranchLoadingPhA                                      AS MaxBranchLoadingPercent,
    s.MaxBranchLoadingDeviceID                                 AS MaxBranchLoadingDevice,
    s.MaxVdPhA                                                 AS MaxVoltageDropPercent,
    s.MaxVdDeviceID                                            AS MaxVoltageDropDevice,
    s.CriticalOverVNum, s.MarginalOverVNum,
    s.CriticalUnderVNum, s.MarginalUnderVNum,
    s.CriticalOverLoadNum, s.MarginalOverLoadNum
FROM TDSysResult s
JOIN TDTimeID t ON t.ResultID = s.ResultID
ORDER BY t.TimeID
"""


# --------------------------------------------------------------------------
# per-branch hourly series (denormalized)
# --------------------------------------------------------------------------

_BRANCH_HOURLY_SQL = """
CREATE TABLE TDBranchHourly AS
SELECT
    t.TimeID                                          AS Step,
    t.Time                                            AS Time,
    d.DeviceName                                      AS DeviceID,
    d.DeviceType                                      AS DeviceType,
    d.RatedKVFrom, d.RatedKVTo,
    d.Capacity                                        AS CapacityMVA,
    b.FromMWPhA + b.FromMWPhB + b.FromMWPhC            AS FromMW,
    b.FromMvarPhA + b.FromMvarPhB + b.FromMvarPhC      AS FromMvar,
    b.ToMWPhA + b.ToMWPhB + b.ToMWPhC                  AS ToMW,
    b.ToMvarPhA + b.ToMvarPhB + b.ToMvarPhC            AS ToMvar,
    b.FromAmpPhA, b.FromAmpPhB, b.FromAmpPhC,
    b.LoadingPercentA, b.LoadingPercentB, b.LoadingPercentC,
    max(b.LoadingPercentA, b.LoadingPercentB, b.LoadingPercentC) AS MaxLoadingPercent,
    b.VdPhA                                           AS VoltageDropPercent,
    b.LIUR, b.IUF2, b.IUF0
FROM TDBranchResult b
JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = b.DeviceIID
JOIN TDTimeID t ON t.ResultID = b.ResultID
ORDER BY d.DeviceName, t.TimeID
"""


# --------------------------------------------------------------------------
# per-device annual summary
# --------------------------------------------------------------------------

_DEVICE_AGG_SQL = """
SELECT d.DeviceName, d.DeviceType, d.RatedKVFrom, d.RatedKVTo, d.Capacity,
       MAX(m) AS PeakLoading, AVG(m) AS AvgLoading, MIN(m) AS MinLoading,
       MAX(mw) AS PeakMW, MIN(mw) AS MinMW, AVG(mw) AS AvgMW, SUM(mw) AS SumMW,
       MAX(amp) AS PeakAmp,
       SUM(CASE WHEN m >= 90 THEN 1 ELSE 0 END)  AS HoursOver90,
       SUM(CASE WHEN m >= 100 THEN 1 ELSE 0 END) AS HoursOver100
FROM (
    SELECT b.DeviceIID,
           max(b.LoadingPercentA, b.LoadingPercentB, b.LoadingPercentC) AS m,
           b.FromMWPhA + b.FromMWPhB + b.FromMWPhC AS mw,
           max(b.FromAmpPhA, b.FromAmpPhB, b.FromAmpPhC) AS amp
    FROM TDBranchResult b
) x
JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = x.DeviceIID
GROUP BY x.DeviceIID
"""

# Bare columns alongside a single MAX() aggregate resolve to the row that
# produced the maximum - a documented SQLite behaviour, and the cheapest way
# to answer "when did this device peak?" without a window function.
_DEVICE_PEAK_TIME_SQL = """
SELECT d.DeviceName,
       MAX(max(b.LoadingPercentA, b.LoadingPercentB, b.LoadingPercentC)),
       t.Time
FROM TDBranchResult b
JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = b.DeviceIID
JOIN TDTimeID t ON t.ResultID = b.ResultID
GROUP BY b.DeviceIID
"""

_DEVICE_SUMMARY_DDL = """
CREATE TABLE TDDeviceSummary (
    DeviceID           TEXT,
    DeviceType         TEXT,
    Winding            TEXT,
    RatedKVFrom        DOUBLE,
    RatedKVTo          DOUBLE,
    CapacityMVA        DOUBLE,
    PeakLoadingPercent DOUBLE,
    PeakLoadingAt      TEXT,
    AvgLoadingPercent  DOUBLE,
    MinLoadingPercent  DOUBLE,
    HoursOver90        INTEGER,
    HoursOver100       INTEGER,
    PeakMW             DOUBLE,
    MinMW              DOUBLE,
    AvgMW              DOUBLE,
    EnergyMWh          DOUBLE,
    PeakAmp            DOUBLE,
    LoadingEvaluated   TEXT
)
"""

# The 3-winding transformer result table splits everything by winding, so it
# needs one pass per winding rather than the single pass used for branches.
_TRANS3X_WINDINGS = [
    ("Primary",   "Prim", "PrimRatedMVA", "RatedKVPrim"),
    ("Secondary", "Sec",  "SecRatedMVA",  "RatedKVSec"),
    ("Tertiary",  "Ter",  "TerRatedMVA",  "RatedKVTer"),
]


def _device_summary(conn, hours_per_step):
    rows = []

    if _has(conn, "TDBranchResult") and _has(conn, "TDTwoTermDevicesInfo"):
        peak_at = {r[0]: r[2] for r in conn.execute(_DEVICE_PEAK_TIME_SQL)}
        for (name, dtype, kv_from, kv_to, cap, peak, avg, mn, peak_mw, min_mw,
             avg_mw, sum_mw, peak_amp, over90, over100) in conn.execute(_DEVICE_AGG_SQL):
            # A branch with no capacity entered gets no loading percentage
            # from ETAP - report that rather than an authoritative-looking 0%.
            evaluated = bool(cap and cap > 0)
            rows.append((
                name, dtype, "", kv_from, kv_to, cap,
                _r(peak, 3) if evaluated else None,
                peak_at.get(name) if evaluated else None,
                _r(avg, 3) if evaluated else None,
                _r(mn, 3) if evaluated else None,
                over90 if evaluated else None,
                over100 if evaluated else None,
                _r(peak_mw, 4), _r(min_mw, 4), _r(avg_mw, 4),
                _r((sum_mw or 0.0) * hours_per_step, 2), _r(peak_amp, 1),
                "Yes" if evaluated else "No - no capacity in model",
            ))

    if _has(conn, "TDTrans3XResult") and _has(conn, "TDTrans3XDevicesInfo"):
        for label, pfx, mva_col, kv_col in _TRANS3X_WINDINGS:
            sql = f"""
                SELECT d.DeviceName, d.DeviceType, d.{kv_col}, d.{mva_col},
                       MAX(m), AVG(m), MIN(m),
                       MAX(mw), MIN(mw), AVG(mw), SUM(mw), MAX(amp),
                       SUM(CASE WHEN m >= 90 THEN 1 ELSE 0 END),
                       SUM(CASE WHEN m >= 100 THEN 1 ELSE 0 END)
                FROM (
                    SELECT r.DeviceIID,
                           max(r.{pfx}LoadingPercentA, r.{pfx}LoadingPercentB,
                               r.{pfx}LoadingPercentC) AS m,
                           r.{pfx}MWPhA + r.{pfx}MWPhB + r.{pfx}MWPhC AS mw,
                           max(r.{pfx}AmpPhA, r.{pfx}AmpPhB, r.{pfx}AmpPhC) AS amp
                    FROM TDTrans3XResult r
                ) x
                JOIN TDTrans3XDevicesInfo d ON d.DeviceIID = x.DeviceIID
                GROUP BY x.DeviceIID
            """
            peak_sql = f"""
                SELECT d.DeviceName,
                       MAX(max(r.{pfx}LoadingPercentA, r.{pfx}LoadingPercentB,
                               r.{pfx}LoadingPercentC)),
                       t.Time
                FROM TDTrans3XResult r
                JOIN TDTrans3XDevicesInfo d ON d.DeviceIID = r.DeviceIID
                JOIN TDTimeID t ON t.ResultID = r.ResultID
                GROUP BY r.DeviceIID
            """
            peak_at = {r[0]: r[2] for r in conn.execute(peak_sql)}
            for (name, dtype, kv, mva, peak, avg, mn, peak_mw, min_mw, avg_mw,
                 sum_mw, peak_amp, over90, over100) in conn.execute(sql):
                rows.append((
                    name, dtype, label, kv, None, mva,
                    _r(peak, 3), peak_at.get(name), _r(avg, 3), _r(mn, 3),
                    over90, over100,
                    _r(peak_mw, 4), _r(min_mw, 4), _r(avg_mw, 4),
                    _r((sum_mw or 0.0) * hours_per_step, 2), _r(peak_amp, 1), "Yes",
                ))

    conn.execute("DROP TABLE IF EXISTS TDDeviceSummary")
    conn.execute(_DEVICE_SUMMARY_DDL)
    conn.executemany(
        "INSERT INTO TDDeviceSummary VALUES (" + ",".join("?" * 18) + ")", rows)
    return len(rows)


# --------------------------------------------------------------------------
# monthly rollup
# --------------------------------------------------------------------------

_MONTHLY_DDL = """
CREATE TABLE TDMonthlySummary (
    MonthNo            INTEGER,
    Month              TEXT,
    Steps              INTEGER,
    GenEnergyMWh       DOUBLE,
    PeakGenMW          DOUBLE,
    AvgGenMW           DOUBLE,
    MinGenMW           DOUBLE,
    LossEnergyMWh      DOUBLE,
    LossPercentOfGen   DOUBLE,
    MaxBusVPercent     DOUBLE,
    MinBusVPercent     DOUBLE,
    MaxBranchLoadingPercent DOUBLE
)
"""

_MONTHLY_AGG_SQL = """
SELECT MonthNo, COUNT(*), SUM(RenewableGenMW), MAX(RenewableGenMW),
       AVG(RenewableGenMW), MIN(RenewableGenMW), SUM(LossMW),
       MAX(MaxBusVPercent), MIN(MinBusVPercent), MAX(MaxBranchLoadingPercent)
FROM TDSystemHourly
GROUP BY MonthNo
ORDER BY MonthNo
"""


def _monthly_summary(conn, hours_per_step):
    rows = []
    for (mno, steps, sum_gen, peak, avg, mn, sum_loss,
         vmax, vmin, load_max) in conn.execute(_MONTHLY_AGG_SQL):
        gen_mwh = (sum_gen or 0.0) * hours_per_step
        loss_mwh = (sum_loss or 0.0) * hours_per_step
        rows.append((
            mno,
            MONTH_NAMES[mno - 1] if mno and 1 <= mno <= 12 else str(mno),
            steps, _r(gen_mwh, 2), _r(peak, 4), _r(avg, 4), _r(mn, 4), _r(loss_mwh, 3),
            _r((loss_mwh / gen_mwh * 100.0) if gen_mwh else None, 4),
            _r(vmax, 4), _r(vmin, 4), _r(load_max, 4),
        ))
    conn.execute("DROP TABLE IF EXISTS TDMonthlySummary")
    conn.execute(_MONTHLY_DDL)
    conn.executemany(
        "INSERT INTO TDMonthlySummary VALUES (" + ",".join("?" * 12) + ")", rows)
    return len(rows)


# --------------------------------------------------------------------------
# headline summary
# --------------------------------------------------------------------------

def _study_summary(conn, hours_per_step):
    """A flat Metric/Value/Unit table - this is what the Overview page shows,
    and what most people want before they open anything else."""
    g = conn.execute("""
        SELECT COUNT(*), MIN(Time), MAX(Time),
               SUM(RenewableGenMW), MAX(RenewableGenMW), AVG(RenewableGenMW),
               MIN(RenewableGenMW), SUM(WindGenMW), SUM(SolarGenMW),
               SUM(LossMW), AVG(LossMW), MAX(LossMW), SUM(LossMvar),
               MAX(MaxBusVPercent), MIN(MinBusVPercent),
               MAX(MaxBranchLoadingPercent), MAX(MaxVoltageDropPercent),
               SUM(CriticalOverVNum), SUM(CriticalUnderVNum), SUM(CriticalOverLoadNum),
               SUM(MarginalOverVNum), SUM(MarginalUnderVNum), SUM(MarginalOverLoadNum)
        FROM TDSystemHourly
    """).fetchone()
    (steps, t_first, t_last, sum_gen, peak_gen, avg_gen, min_gen, sum_wind,
     sum_solar, sum_loss, avg_loss, max_loss, sum_loss_q, vmax, vmin,
     load_max, vd_max, c_ov, c_uv, c_ol, m_ov, m_uv, m_ol) = g

    gen_mwh = (sum_gen or 0.0) * hours_per_step
    loss_mwh = (sum_loss or 0.0) * hours_per_step
    hours = (steps or 0) * hours_per_step

    def device_at(col, agg):
        """Name the device behind a system-wide extreme."""
        r = conn.execute(
            f"SELECT {col}Device, {col}Percent, Time FROM TDSystemHourly "
            f"ORDER BY {col}Percent {'DESC' if agg == 'max' else 'ASC'} LIMIT 1"
        ).fetchone()
        return r if r else (None, None, None)

    v_hi = device_at("MaxBusV", "max")
    v_lo = device_at("MinBusV", "min")
    ld = device_at("MaxBranchLoading", "max")

    rows = [
        ("Simulation steps", steps, "steps"),
        ("Step size", hours_per_step, "hours"),
        ("Simulated period", hours, "hours"),
        ("First step", t_first, ""),
        ("Last step", t_last, ""),
        ("Total generation", round(gen_mwh, 1), "MWh"),
        ("  of which wind", round((sum_wind or 0.0) * hours_per_step, 1), "MWh"),
        ("  of which solar", round((sum_solar or 0.0) * hours_per_step, 1), "MWh"),
        ("Peak generation", round(peak_gen, 3) if peak_gen is not None else None, "MW"),
        ("Average generation", round(avg_gen, 3) if avg_gen is not None else None, "MW"),
        ("Minimum generation", round(min_gen, 3) if min_gen is not None else None, "MW"),
        ("Capacity factor (vs. peak output)",
         round(avg_gen / peak_gen * 100.0, 2) if peak_gen else None, "%"),
        ("Total real losses", round(loss_mwh, 1), "MWh"),
        ("Losses as share of generation",
         round(loss_mwh / gen_mwh * 100.0, 3) if gen_mwh else None, "%"),
        ("Average real losses", round(avg_loss, 4) if avg_loss is not None else None, "MW"),
        ("Peak real losses", round(max_loss, 4) if max_loss is not None else None, "MW"),
        ("Net reactive losses", round((sum_loss_q or 0.0) * hours_per_step, 1), "Mvarh"),
        ("Highest bus voltage", round(vmax, 3) if vmax is not None else None, "% of nominal"),
        ("  at bus", f"{v_hi[0]} ({v_hi[2]})" if v_hi[0] else None, ""),
        ("Lowest bus voltage", round(vmin, 3) if vmin is not None else None, "% of nominal"),
        ("  at bus", f"{v_lo[0]} ({v_lo[2]})" if v_lo[0] else None, ""),
        ("Highest branch loading", round(load_max, 3) if load_max is not None else None, "%"),
        ("  at branch", f"{ld[0]} ({ld[2]})" if ld[0] else None, ""),
        ("Highest voltage drop", round(vd_max, 3) if vd_max is not None else None, "%"),
        ("Critical over-voltage violations", c_ov, "device-steps"),
        ("Critical under-voltage violations", c_uv, "device-steps"),
        ("Critical overload violations", c_ol, "device-steps"),
        ("Marginal over-voltage violations", m_ov, "device-steps"),
        ("Marginal under-voltage violations", m_uv, "device-steps"),
        ("Marginal overload violations", m_ol, "device-steps"),
    ]

    n_uneval = conn.execute(
        "SELECT COUNT(*) FROM TDDeviceSummary WHERE LoadingEvaluated LIKE 'No%'"
    ).fetchone()[0]
    if n_uneval:
        rows.append(("Branches with no loading check", n_uneval,
                     "no capacity entered in model"))

    conn.execute("DROP TABLE IF EXISTS TDStudySummary")
    conn.execute("CREATE TABLE TDStudySummary (Metric TEXT, Value, Unit TEXT)")
    conn.executemany("INSERT INTO TDStudySummary VALUES (?,?,?)", rows)
    return len(rows)


# --------------------------------------------------------------------------
# annual AC loss report
# --------------------------------------------------------------------------

# Real/reactive loss in a two-terminal branch, three-phase (see LOSS
# CONVENTION in the module docstring).
_L2_MW = ("(b.FromMWPhA+b.FromMWPhB+b.FromMWPhC)"
          "+(b.ToMWPhA+b.ToMWPhB+b.ToMWPhC)")
_L2_MVAR = ("(b.FromMvarPhA+b.FromMvarPhB+b.FromMvarPhC)"
            "+(b.ToMvarPhA+b.ToMvarPhB+b.ToMvarPhC)")
_L3_MW = ("(r.PrimMWPhA+r.PrimMWPhB+r.PrimMWPhC)"
          "+(r.SecMWPhA+r.SecMWPhB+r.SecMWPhC)"
          "+(r.TerMWPhA+r.TerMWPhB+r.TerMWPhC)")
_L3_MVAR = ("(r.PrimMvarPhA+r.PrimMvarPhB+r.PrimMvarPhC)"
            "+(r.SecMvarPhA+r.SecMvarPhB+r.SecMvarPhC)"
            "+(r.TerMvarPhA+r.TerMvarPhB+r.TerMvarPhC)")

# Throughput used to express a branch's loss as a percentage of what passed
# through it: the larger-magnitude terminal, i.e. the inlet.
_L2_THRU = ("max(abs(b.FromMWPhA+b.FromMWPhB+b.FromMWPhC),"
            "abs(b.ToMWPhA+b.ToMWPhB+b.ToMWPhC))")

# ETAP's DeviceType strings vary by element ("Line", "Transmission Line",
# "Cable", "Two-winding Transformer", ...), so bucket them by keyword.
_CLASS_CASE = """CASE
    WHEN d.DeviceType LIKE '%Cable%'       THEN 'Cables'
    WHEN d.DeviceType LIKE '%Line%'        THEN 'Transmission Lines'
    WHEN d.DeviceType LIKE '%Transformer%' THEN 'Transformers (Unit / 2-Winding)'
    WHEN d.DeviceType LIKE '%Reactor%'     THEN 'Reactors'
    WHEN d.DeviceType LIKE '%Duct%'        THEN 'Bus Ducts'
    ELSE 'Other Branches'
END"""

_XFMR3_CLASS = "Transformers (Main Power / 3-Winding)"

# Loss classes in report order. Anything a model produces that isn't listed
# still shows up - this only fixes the ordering of the common ones.
_CLASS_ORDER = ["Transmission Lines", "Cables", "Transformers (Unit / 2-Winding)",
                _XFMR3_CLASS, "Reactors", "Bus Ducts", "Other Branches"]

_LOSS_HOURLY_SQL = f"""
CREATE TABLE TDLossHourly AS
SELECT
    t.TimeID                                   AS Step,
    t.Time                                     AS Time,
    CAST(substr(t.Time, 1, 2) AS INTEGER)      AS MonthNo,
    COALESCE(b.LineLossMW, 0)                  AS LineLossMW,
    COALESCE(b.CableLossMW, 0)                 AS CableLossMW,
    COALESCE(b.Xfmr2LossMW, 0)                 AS Xfmr2LossMW,
    COALESCE(x.Xfmr3LossMW, 0)                 AS Xfmr3LossMW,
    COALESCE(b.OtherLossMW, 0)                 AS OtherLossMW,
    COALESCE(b.LineLossMW, 0) + COALESCE(b.CableLossMW, 0)
      + COALESCE(b.Xfmr2LossMW, 0) + COALESCE(x.Xfmr3LossMW, 0)
      + COALESCE(b.OtherLossMW, 0)             AS TotalLossMW,
    s.MWLossPhA + s.MWLossPhB + s.MWLossPhC    AS EtapSystemLossMW,
    COALESCE(b.LossMvar, 0) + COALESCE(x.LossMvar, 0) AS TotalLossMvar,
    s.WindGeneration + s.SolarGeneration       AS GenAtUnitsMW,
    s.TotalLoadMWPhA + s.TotalLoadMWPhB + s.TotalLoadMWPhC AS AuxLoadMW,
    (s.WindGeneration + s.SolarGeneration)
      - (s.MWLossPhA + s.MWLossPhB + s.MWLossPhC)
      - (s.TotalLoadMWPhA + s.TotalLoadMWPhB + s.TotalLoadMWPhC) AS PlantOutputMW
FROM TDTimeID t
JOIN TDSysResult s ON s.ResultID = t.ResultID
LEFT JOIN (
    SELECT b.ResultID,
        SUM(CASE WHEN {_CLASS_CASE} = 'Transmission Lines' THEN {_L2_MW} ELSE 0 END) AS LineLossMW,
        SUM(CASE WHEN {_CLASS_CASE} = 'Cables' THEN {_L2_MW} ELSE 0 END) AS CableLossMW,
        SUM(CASE WHEN {_CLASS_CASE} = 'Transformers (Unit / 2-Winding)' THEN {_L2_MW} ELSE 0 END) AS Xfmr2LossMW,
        SUM(CASE WHEN {_CLASS_CASE} IN ('Reactors','Bus Ducts','Other Branches') THEN {_L2_MW} ELSE 0 END) AS OtherLossMW,
        SUM({_L2_MVAR}) AS LossMvar
    FROM TDBranchResult b
    JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = b.DeviceIID
    GROUP BY b.ResultID
) b ON b.ResultID = t.ResultID
LEFT JOIN (
    SELECT r.ResultID, SUM({_L3_MW}) AS Xfmr3LossMW, SUM({_L3_MVAR}) AS LossMvar
    FROM TDTrans3XResult r GROUP BY r.ResultID
) x ON x.ResultID = t.ResultID
ORDER BY t.TimeID
"""

_LOSS_SUMMARY_DDL = """
CREATE TABLE TDLossSummary (
    LossClass            TEXT,
    Devices              INTEGER,
    EnergyLossMWh        DOUBLE,
    ShareOfLossesPercent DOUBLE,
    ShareOfGenPercent    DOUBLE,
    AvgLossMW            DOUBLE,
    PeakLossMW           DOUBLE,
    PeakLossAt           TEXT,
    MinLossMW            DOUBLE,
    NetReactiveMvarh     DOUBLE
)
"""

_DEVICE_LOSSES_DDL = """
CREATE TABLE TDDeviceLosses (
    DeviceID             TEXT,
    DeviceType           TEXT,
    LossClass            TEXT,
    RatedKVFrom          DOUBLE,
    RatedKVTo            DOUBLE,
    EnergyLossMWh        DOUBLE,
    ShareOfLossesPercent DOUBLE,
    AvgLossMW            DOUBLE,
    PeakLossMW           DOUBLE,
    PeakLossAt           TEXT,
    ThroughputMWh        DOUBLE,
    LossPercentOfThroughput DOUBLE,
    NetReactiveMvarh     DOUBLE
)
"""


def _loss_report(conn, hours_per_step):
    """Annual loss energy by equipment class and by device, plus the
    generation/loss/output energy balance."""
    h = hours_per_step

    # ---- per class -------------------------------------------------------
    per_class = {}
    if _has(conn, "TDBranchResult") and _has(conn, "TDTwoTermDevicesInfo"):
        rows = conn.execute(f"""
            SELECT {_CLASS_CASE} AS cls, COUNT(DISTINCT b.DeviceIID),
                   SUM({_L2_MW}), AVG({_L2_MW}), MAX({_L2_MW}), MIN({_L2_MW}),
                   SUM({_L2_MVAR})
            FROM TDBranchResult b
            JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = b.DeviceIID
            GROUP BY cls
        """).fetchall()
        for cls, n, s, a, mx, mn, q in rows:
            per_class[cls] = [n, s, a, mx, mn, q]

    if _has(conn, "TDTrans3XResult"):
        r = conn.execute(f"""
            SELECT COUNT(DISTINCT r.DeviceIID), SUM({_L3_MW}), AVG({_L3_MW}),
                   MAX({_L3_MW}), MIN({_L3_MW}), SUM({_L3_MVAR})
            FROM TDTrans3XResult r
        """).fetchone()
        if r and r[0]:
            per_class[_XFMR3_CLASS] = list(r)

    # Peak-loss timestamps come from the per-step table, which already has one
    # column per class - simpler and cheaper than re-grouping the fact table.
    peak_at = {}
    col_for = {
        "Transmission Lines": "LineLossMW",
        "Cables": "CableLossMW",
        "Transformers (Unit / 2-Winding)": "Xfmr2LossMW",
        _XFMR3_CLASS: "Xfmr3LossMW",
    }
    for cls, col in col_for.items():
        row = conn.execute(
            f"SELECT {col}, Time FROM TDLossHourly ORDER BY {col} DESC LIMIT 1").fetchone()
        if row:
            peak_at[cls] = row[1]

    total_loss_mwh = sum(v[1] for v in per_class.values() if v[1]) * h
    gen_mwh = (_scalar(conn, "SELECT SUM(GenAtUnitsMW) FROM TDLossHourly", 0.0) or 0.0) * h

    ordered = [c for c in _CLASS_ORDER if c in per_class]
    ordered += [c for c in per_class if c not in _CLASS_ORDER]

    class_rows = []
    for cls in ordered:
        n, s, a, mx, mn, q = per_class[cls]
        e = (s or 0.0) * h
        class_rows.append((
            cls, n, _r(e, 3),
            _r((e / total_loss_mwh * 100.0) if total_loss_mwh else None, 3),
            _r((e / gen_mwh * 100.0) if gen_mwh else None, 4),
            _r(a, 6), _r(mx, 6), peak_at.get(cls), _r(mn, 6), _r((q or 0.0) * h, 3),
        ))

    peak_total = conn.execute(
        "SELECT TotalLossMW, Time FROM TDLossHourly ORDER BY TotalLossMW DESC LIMIT 1").fetchone()
    agg = conn.execute(
        "SELECT AVG(TotalLossMW), MIN(TotalLossMW), SUM(TotalLossMvar) FROM TDLossHourly").fetchone()
    class_rows.append((
        "TOTAL", sum(v[0] or 0 for v in per_class.values()), _r(total_loss_mwh, 3),
        100.0 if total_loss_mwh else None,
        _r((total_loss_mwh / gen_mwh * 100.0) if gen_mwh else None, 4),
        _r(agg[0], 6), _r(peak_total[0] if peak_total else None, 6),
        peak_total[1] if peak_total else None, _r(agg[1], 6), _r((agg[2] or 0.0) * h, 3),
    ))

    conn.execute("DROP TABLE IF EXISTS TDLossSummary")
    conn.execute(_LOSS_SUMMARY_DDL)
    conn.executemany(
        "INSERT INTO TDLossSummary VALUES (" + ",".join("?" * 10) + ")", class_rows)

    # ---- per device ------------------------------------------------------
    dev_rows = []
    if _has(conn, "TDBranchResult") and _has(conn, "TDTwoTermDevicesInfo"):
        peak_dev = {r[0]: r[2] for r in conn.execute(f"""
            SELECT d.DeviceName, MAX({_L2_MW}), t.Time
            FROM TDBranchResult b
            JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = b.DeviceIID
            JOIN TDTimeID t ON t.ResultID = b.ResultID
            GROUP BY b.DeviceIID
        """)}
        for (name, dtype, cls, kvf, kvt, s, a, mx, thru, q) in conn.execute(f"""
            SELECT d.DeviceName, d.DeviceType, {_CLASS_CASE}, d.RatedKVFrom, d.RatedKVTo,
                   SUM({_L2_MW}), AVG({_L2_MW}), MAX({_L2_MW}),
                   SUM({_L2_THRU}), SUM({_L2_MVAR})
            FROM TDBranchResult b
            JOIN TDTwoTermDevicesInfo d ON d.DeviceIID = b.DeviceIID
            GROUP BY b.DeviceIID
        """):
            e = (s or 0.0) * h
            thru_mwh = (thru or 0.0) * h
            dev_rows.append((
                name, dtype, cls, kvf, kvt, _r(e, 3),
                _r((e / total_loss_mwh * 100.0) if total_loss_mwh else None, 3),
                _r(a, 6), _r(mx, 6), peak_dev.get(name), _r(thru_mwh, 1),
                _r((e / thru_mwh * 100.0) if thru_mwh else None, 4),
                _r((q or 0.0) * h, 3),
            ))

    if _has(conn, "TDTrans3XResult") and _has(conn, "TDTrans3XDevicesInfo"):
        peak_dev = {r[0]: r[2] for r in conn.execute(f"""
            SELECT d.DeviceName, MAX({_L3_MW}), t.Time
            FROM TDTrans3XResult r
            JOIN TDTrans3XDevicesInfo d ON d.DeviceIID = r.DeviceIID
            JOIN TDTimeID t ON t.ResultID = r.ResultID
            GROUP BY r.DeviceIID
        """)}
        for (name, dtype, kvp, kvs, s, a, mx, thru, q) in conn.execute(f"""
            SELECT d.DeviceName, d.DeviceType, d.RatedKVPrim, d.RatedKVSec,
                   SUM({_L3_MW}), AVG({_L3_MW}), MAX({_L3_MW}),
                   SUM(abs(r.SecMWPhA+r.SecMWPhB+r.SecMWPhC)), SUM({_L3_MVAR})
            FROM TDTrans3XResult r
            JOIN TDTrans3XDevicesInfo d ON d.DeviceIID = r.DeviceIID
            GROUP BY r.DeviceIID
        """):
            e = (s or 0.0) * h
            thru_mwh = (thru or 0.0) * h
            dev_rows.append((
                name, dtype, _XFMR3_CLASS, kvp, kvs, _r(e, 3),
                _r((e / total_loss_mwh * 100.0) if total_loss_mwh else None, 3),
                _r(a, 6), _r(mx, 6), peak_dev.get(name), _r(thru_mwh, 1),
                _r((e / thru_mwh * 100.0) if thru_mwh else None, 4),
                _r((q or 0.0) * h, 3),
            ))

    dev_rows.sort(key=lambda r: -(r[5] or 0.0))
    conn.execute("DROP TABLE IF EXISTS TDDeviceLosses")
    conn.execute(_DEVICE_LOSSES_DDL)
    conn.executemany(
        "INSERT INTO TDDeviceLosses VALUES (" + ",".join("?" * 13) + ")", dev_rows)

    return len(class_rows), len(dev_rows), total_loss_mwh, gen_mwh


def _energy_balance(conn, hours_per_step, total_loss_mwh, gen_mwh):
    """Generation at the units -> losses -> output at the point of
    interconnection, as one auditable chain."""
    h = hours_per_step
    g = conn.execute("""
        SELECT SUM(GenAtUnitsMW), MAX(GenAtUnitsMW), AVG(GenAtUnitsMW),
               SUM(PlantOutputMW), MAX(PlantOutputMW), AVG(PlantOutputMW),
               MIN(PlantOutputMW), SUM(AuxLoadMW), COUNT(*),
               MAX(abs(TotalLossMW - EtapSystemLossMW))
        FROM TDLossHourly
    """).fetchone()
    (sum_gen, peak_gen, avg_gen, sum_out, peak_out, avg_out, min_out,
     sum_aux, steps, max_resid) = g

    out_mwh = (sum_out or 0.0) * h
    aux_mwh = (sum_aux or 0.0) * h

    def when(col, order="DESC"):
        r = conn.execute(
            f"SELECT Time FROM TDLossHourly ORDER BY {col} {order} LIMIT 1").fetchone()
        return r[0] if r else None

    def tied(col, peak):
        """How many steps share this extreme, within ETAP's float32 storage
        granularity. A wind farm running against its cap sits at the same
        output for hundreds of hours, so a single peak timestamp is a
        coin-flip between them - say how many there were."""
        if peak is None:
            return None
        return conn.execute(
            f"SELECT COUNT(*) FROM TDLossHourly WHERE {col} >= ? - 1.5e-5",
            (peak,)).fetchone()[0]

    # ETAP's own Time Series report integrates only the first N-1 steps - it
    # treats the series as the intervals *between* samples, so the final
    # sample contributes nothing. Reproduce that here so these numbers tie out
    # against GM_Complete.xlsx, while the rows above keep the full-sample
    # total (every one of the N samples represents one step of energy).
    peak_loss = _scalar(conn, "SELECT MAX(TotalLossMW) FROM TDLossHourly")

    last_step = _scalar(conn, "SELECT MAX(Step) FROM TDLossHourly")
    e = conn.execute(
        "SELECT SUM(GenAtUnitsMW), SUM(TotalLossMW), SUM(PlantOutputMW) "
        "FROM TDLossHourly WHERE Step < ?", (last_step,)).fetchone()
    etap_gen, etap_loss, etap_out = [(v or 0.0) * h for v in e]

    def cls_energy(name):
        r = conn.execute(
            "SELECT EnergyLossMWh FROM TDLossSummary WHERE LossClass = ?", (name,)).fetchone()
        return r[0] if r else 0.0

    rows = [
        ("Gross generation at the units", round(gen_mwh, 1), "MWh"),
        ("Peak generation at the units", round(peak_gen, 3) if peak_gen else None, "MW"),
        ("  at", when("GenAtUnitsMW"), ""),
        ("  steps sharing that peak", tied("GenAtUnitsMW", peak_gen), "steps"),
        ("Average generation at the units", round(avg_gen, 3) if avg_gen else None, "MW"),
        ("", None, ""),
        ("Transmission line losses", round(cls_energy("Transmission Lines"), 2), "MWh"),
        ("Cable (collector) losses", round(cls_energy("Cables"), 2), "MWh"),
        ("Unit transformer losses", round(cls_energy("Transformers (Unit / 2-Winding)"), 2), "MWh"),
        ("Main power transformer losses", round(cls_energy(_XFMR3_CLASS), 2), "MWh"),
        ("Total AC losses", round(total_loss_mwh, 2), "MWh"),
        ("Losses as share of gross generation",
         round(total_loss_mwh / gen_mwh * 100.0, 3) if gen_mwh else None, "%"),
        ("Peak total losses", round(peak_loss, 4) if peak_loss else None, "MW"),
        ("  at", when("TotalLossMW"), ""),
        ("  steps sharing that peak", tied("TotalLossMW", peak_loss), "steps"),
        ("Auxiliary / station load", round(aux_mwh, 2), "MWh"),
        ("", None, ""),
        ("Net plant output at the POI", round(out_mwh, 1), "MWh"),
        ("Peak plant output", round(peak_out, 3) if peak_out else None, "MW"),
        ("  at", when("PlantOutputMW"), ""),
        ("  steps sharing that peak", tied("PlantOutputMW", peak_out), "steps"),
        ("Average plant output", round(avg_out, 3) if avg_out else None, "MW"),
        ("Minimum plant output", round(min_out, 3) if min_out is not None else None, "MW"),
        ("Capacity factor at the units (vs. peak)",
         round(avg_gen / peak_gen * 100.0, 2) if peak_gen else None, "%"),
        ("Capacity factor at the POI (vs. peak)",
         round(avg_out / peak_out * 100.0, 2) if peak_out else None, "%"),
        ("", None, ""),
        ("Balance check: gen - losses - aux", round(gen_mwh - total_loss_mwh - aux_mwh, 3), "MWh"),
        ("Balance check: net output", round(out_mwh, 3), "MWh"),
        ("", None, ""),
        ("-- ETAP report convention (first N-1 steps) --", None, ""),
        ("Gross generation at the units", round(etap_gen, 3), "MWh"),
        ("Total AC losses", round(etap_loss, 3), "MWh"),
        ("Net plant output at the POI", round(etap_out, 3), "MWh"),
        ("Final step excluded by that convention", when("Step"), ""),
        # If summing branch losses ever drifts from ETAP's own system total,
        # the classification or the sign convention is wrong - surface it
        # rather than quietly publishing a loss report that doesn't add up.
        ("Worst step mismatch vs. ETAP system loss",
         round(max_resid, 9) if max_resid is not None else None, "MW"),
    ]

    conn.execute("DROP TABLE IF EXISTS TDEnergyBalance")
    conn.execute("CREATE TABLE TDEnergyBalance (Metric TEXT, Value, Unit TEXT)")
    conn.executemany("INSERT INTO TDEnergyBalance VALUES (?,?,?)", rows)
    return len(rows)


# --------------------------------------------------------------------------

def derive(conn: sqlite3.Connection, progress_cb=None) -> dict:
    """Build the derived tables. Safe to re-run: every table is dropped first."""
    def report(stage):
        if progress_cb:
            progress_cb(stage, 0, 0)

    hours_per_step = _hours_per_step(conn)
    built = {}

    report("summarizing system hourly")
    conn.execute("DROP TABLE IF EXISTS TDSystemHourly")
    conn.execute(_SYSTEM_HOURLY_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tdsysh_step ON TDSystemHourly(Step)")
    built["TDSystemHourly"] = _scalar(conn, "SELECT COUNT(*) FROM TDSystemHourly", 0)

    report("summarizing devices")
    built["TDDeviceSummary"] = _device_summary(conn, hours_per_step)

    report("summarizing months")
    built["TDMonthlySummary"] = _monthly_summary(conn, hours_per_step)

    report("summarizing study")
    built["TDStudySummary"] = _study_summary(conn, hours_per_step)

    report("computing losses")
    conn.execute("DROP TABLE IF EXISTS TDLossHourly")
    conn.execute(_LOSS_HOURLY_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tdlossh_step ON TDLossHourly(Step)")
    built["TDLossHourly"] = _scalar(conn, "SELECT COUNT(*) FROM TDLossHourly", 0)

    n_cls, n_dev, total_loss_mwh, gen_mwh = _loss_report(conn, hours_per_step)
    built["TDLossSummary"] = n_cls
    built["TDDeviceLosses"] = n_dev
    built["TDEnergyBalance"] = _energy_balance(
        conn, hours_per_step, total_loss_mwh, gen_mwh)

    if _has(conn, "TDBranchResult") and _has(conn, "TDTwoTermDevicesInfo"):
        report("denormalizing branch hourly")
        conn.execute("DROP TABLE IF EXISTS TDBranchHourly")
        conn.execute(_BRANCH_HOURLY_SQL)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_tdbrh_dev "
                     "ON TDBranchHourly(DeviceID, Step)")
        built["TDBranchHourly"] = _scalar(conn, "SELECT COUNT(*) FROM TDBranchHourly", 0)

    conn.commit()
    return built
