# ETAP File Format Notes

A living reverse-engineering log for what's actually inside ETAP's project
files, and where. This exists because ETAP's file formats aren't publicly
documented anywhere we could find - everything here was determined by
directly inspecting real `.OTI`/`.MDF`/`.LDF` files and ETAP result files,
cross-checked against ETAP's own PDF exports and behavior. Corrections and
additions welcome - see [CONTRIBUTING.md](../CONTRIBUTING.md).

Tested against ETAP 24 (build referenced in a project's `ETAPDumpInfo.txt`
as `C:\ETAP 2400`). Table/column names may differ in other versions.

## The file set

A project folder typically contains:

| File | What it is |
|---|---|
| `<name>.OTI` | An OLE2/Compound File Binary (the same container format as old `.doc`/`.xls`) holding a connection pointer and user/permission records. **Not the project data.** |
| `<name>.MDF` | The live SQL Server database file - this is where the actual project data lives. |
| `<name>.BAK` | A SQL Server backup of the same database. Used as a fallback if no `.MDF` is present. |
| `<name>_log.LDF` | SQL Server's transaction log (write-ahead log for crash recovery). **Not a second copy of the data** - nothing useful to extract from it directly. Not needed to read the `.MDF`; we attach with `FOR ATTACH_REBUILD_LOG` rather than using the original log. |
| `<name>.SA1S` / `.SA2S` / `.LF1S` / `.UL1S` / `.HA1S` / `.GRDS` / etc. | Study result files. Unlike the project database, **these are already plain SQLite** (ETAP's `Etaps.ini` has `OutputToSQLite=1`) - no SQL Server involved, read directly. |
| `<case>.fspdb` / `<case>.hfpdb` | Plot curve data for a harmonic run, also plain SQLite. See [`.HA1S` and its plot companions](#ha1s---harmonic-analysis-and-its-plot-companions). |
| `<case>.fsp` / `<case>.hfp` | The binary plot *settings* beside each of the above - axis ranges, which curves are ticked. Undocumented, and not worth decoding: the curve data itself is in the `db` files. Header is `62 C5 00 00` (`0x0000C562`) then `30 1D 00 00`, then a plot-type word - 7 for `.fsp`, 6 for `.hfp`. No strings anywhere in the file. |
| `<case>.XL1S` | Seen once, named `AmpacityReport.XL1S`, 55 tables whose schema is the arc-flash / short-circuit family (`BusArcFlash`, `PDArcFlash`, `SCIEC*`, `SeqOfOper`) - **every table empty except `DBVersion`**, and dated a year before the rest of the project. Not enough to say what it is for, so not wired up. |
| `classification.xml` | Small, mostly empty in projects we've seen (`<root schema="2"><Item Name="Classifications" .../></root>`) - equipment classification tree metadata, not yet found to be load-bearing for anything this tool does. |
| `Plots/<module>/~OTI_ETAP#temp.plotDeviceIDList_*.json` | Which devices are ticked in a plot dialog (`{"schema": 1, "listDeviceTypeDeviceIDData": [...]}`). UI state, not results. |
| `Area Reports.txt`, `CableVd.txt` | ETAP's printed text reports. A text rendering of data already in the corresponding `.LF1S`, so nothing to gain by parsing them. `CableVd.txt` was zero bytes in the project we have. |
| `ETAPDumpInfo.txt` | UTF-16 host/environment dump ETAP writes on start - OS, CPU, disks, and the ETAP install directory. Useful for one thing: it names the ETAP build a project was last touched by. |

## `.OTI` structure

Confirmed via `etap_reader/oti_parser.py`. An OLE compound file with these
streams:

- `/Administrators/*`, `/Users/*`, `/Permissions/*` - user accounts and
  permission records (binary, look like hashed credentials - not decoded,
  not needed).
- `/ODBCInfo/ConnectionString` - the important one. A plain-text ODBC
  connection string, e.g.:
  ```
  ODBC;DBQ=etapmodel;DSN=otilocaldb19;FIL=Local SQL DB;MaxBufferSize=4096;PageTimeout=600;UID=WS1;PWD=;
  ```
  `DBQ=` gives the real database name - look for `<DBQ>.MDF` (preferred) or
  `<DBQ>.BAK` next to the `.oti`. `DSN=` matches a SQL Server LocalDB
  instance name ETAP itself installs (e.g. `otilocaldb19` -> LocalDB
  instance `ETAPLocalDB19`).
- `/Version/Counter`, `/Version/Counter2` - small version/timestamp
  counters.
- `/MultiUser/MultiUserStream` - multi-user locking state, empty/zeroed in
  every project seen so far.

## `.TU1S` - Time-Domain Load Flow (TDLF) results

Plain SQLite, like the other study result files. A TDLF run solves a load
flow once per time step over a profile - typically 8760 hourly steps for a
calendar year - and stores the answer as narrow fact tables keyed by
integers.

**Shape.** `TDTimeID` maps `ResultID` -> timestamp (`MM-DD-YYYY HH:MM:SS.mmm`)
and step number; `TDTwoTermDevicesInfo` / `TDTrans3XDevicesInfo` map
`DeviceIID` -> device name, type and rating. The result tables carry only
those two integers plus numbers, so nothing in them is readable without
joining both. Row counts multiply fast: `TDBranchResult` is
steps x branches (473,040 rows for 54 branches over a year).

`TDStudyCaseInfo` holds the run's setup, including `SimulationHoursperStep` -
needed to turn MW into MWh, and easy to silently get wrong if assumed to be 1.

**Sign convention (the thing worth writing down).** Both terminals of a
branch are reported as power flowing *into* the branch from the bus at that
end. So the loss in a branch is just the sum of its terminals:

```
two-terminal    loss = From + To
three-winding   loss = Prim + Sec + Ter
```

The two terms are nearly equal with opposite signs, and what survives is the
loss. Summing that over every branch reproduces ETAP's own `MWLossPh*`
system total - that agreement is what pins the convention down, and it's
worth re-checking on any new model rather than trusting the formula blind.

Consequence: loss is a small difference of two large numbers, and ETAP
stores results as **single precision** (every sampled value round-trips
through float32 unchanged). At ~48 MW per terminal, one ulp is ~3e-6 MW, so
summed across 55 branches the per-step residual against ETAP's own total
lands around 1e-5 MW - about 14 microwatts on a 3.8 MW peak. That is the
precision floor of the stored data, not an arithmetic error. Don't chase it,
and don't present per-step losses to more digits than it supports.

Same floor makes extremes degenerate: a wind farm pinned at its cap sits at
an identical output for hundreds of steps, and their losses differ by less
than one ulp. "Peak loss occurred at <timestamp>" is then a coin flip among
tied steps - ETAP's report and an independent pass will disagree on which
one, while agreeing on the value. Report the tie count alongside.

**Energy integration - ETAP's report drops the last sample.** ETAP's own
Time Series report (`GM_Complete.xlsx`, `SystemSummary` sheet) integrates
only the first N-1 steps, treating the series as the intervals *between*
samples. For an 8760-sample year that is 8759 hours of energy: reproducing
it exactly requires excluding the final step. Verified against a real run -
wind energy matched to 0.0000 MWh with the final step dropped, and differed
by exactly that step's output (137.08 MWh) with it included. Summing all N
samples is the defensible annual figure; matching ETAP's published number
requires the N-1 convention, so it's worth reporting both.

Two more traps in that report:
- The `SystemSummary` sheet's **"AC Generation"** row is the swing-source
  energy, which for a generation-only plant equals the losses - not
  generation. Its **"Total Energy Loss"** row repeats System Generation
  (generation + swing), so it is not the loss figure it appears to be.
- ETAP ships a blank copy of the same workbook as a template (ETAP version
  18.0.0C, 2018 placeholder dates, "Yes / No" literals in the Info sheet).
  Check that the `TDLF-Result` sheet actually has rows before trusting it.

**Branches with no capacity entered** get no loading percentage: ETAP writes
`-999` into `TD2TWorstOverLoadCases` and leaves `LoadingPercent*` at zero.
That is "not evaluated", not "0% loaded", and reporting it as the latter is
misleading - cables in particular often have no ampacity in the model.

## `.HA1S` - Harmonic Analysis, and its plot companions

Plain SQLite, 52 tables. The thing to know about `.HA1S` before anything else:

**One extension, two studies.** ETAP writes a *frequency scan* and a
*harmonic load flow* into the same `.HA1S` schema and simply leaves the other
one's tables empty. From a real project (case names are ETAP's, and the
`FS_`/`HLF_` prefixes are the engineer's convention, not something ETAP
enforces):

| | `FS_H01.HA1S` (frequency scan) | `HLF_H01.HA1S` (harmonic load flow) |
|---|---|---|
| `HAFreqScan` | 2378 rows | 0 |
| `HAFSAlert` | 2 rows | 0 |
| `HASystemInfo` | 0 | 74 rows |
| `LFR` | 0 | 74 rows |
| `HABusTabulationNom` / `Fund` | 0 | 30 rows each |
| `HASourceTabulationFund` | 0 | 31 rows |

So the `harmonics` category set in `categories.py` lists both halves and each
file populates the one it ran. Deciding by extension is impossible, and
deciding by content would mean opening the file to work out how to open it -
which is exactly what the board is built not to do.

The tables worth knowing:

- `HAFreqScan` - the scan itself: `BusID`, `Freq`, `Mag`, `Angle`. One row per
  bus per frequency step. This is where resonance shows up.
- `HASystemInfo` - per-bus harmonic load flow result: fundamental and RMS
  voltage, `VTHD`, `VTIF`, `VTIHD`/`VTSHD` telephone-influence indices.
- `HABusTabulation*` / `HABranchTabulation*` / `HASourceTabulationFund` -
  magnitude per harmonic order. The `Fund` and `Nom`/`1MVA` variants are the
  same numbers against different bases.
- `IHAStudyCase` - `FromHz`/`ToHz`/`StepHz` and method. Worth reading first:
  it tells you what the scan actually swept.
- `IHASource` / `IHASourceData` - the injecting devices and their spectra.

### Plot companions (`.fspdb` / `.hfpdb`)

A harmonic run writes up to four files sharing a stem: `<case>.HA1S` (results),
`<case>.fspdb` (frequency-scan curves), `<case>.hfpdb` (waveform and spectrum
curves), and a `.fsp`/`.hfp` binary of plot settings for each.

The two `db` files are plain SQLite holding one table per curve, plus
`DeviceID_IID` and `SystemFrequency`:

```
Buses_PCC1_Z Magnitude_Hz_4116      ValueX, ValueY          1189 rows
Buses_PCC1_Z Angle_Order_4116       ValueX, ValueY          1189 rows
Buses_PCC1_Spectrum_Hz_4116         ValueX, ValueY            16 rows
Cables_Cable25_Waveform_4705        ValueX, ValueY, Angle   2501 rows
```

The name is `<DeviceType>_<DeviceID>_<Curve>[_<XAxis>]_<IID>`, where `IID` is
the device's internal id and `XAxis` is `Hz` or `Order` (absent on `Waveform`,
which is against time). **Do not parse this by splitting on underscores** - a
bus named `PCC_1` breaks it. `etap_reader/ha_plots.py` resolves the device
from the file's own `DeviceID_IID` table instead, then locates that name in
the string.

These are attached to the `.HA1S` at import rather than opened on their own,
because alone they are anonymous: two unlabelled columns with no project, no
study case, and no indication of which run produced them. Attached, they gain
`HAPlotIndex` (one row per curve) and `HAPlotCurves` (long format, one row per
point with the device named).

An **empty companion is normal** - `FS_H01.hfpdb` in our sample project holds
only `DeviceID_IID` and `SystemFrequency` with no curve tables at all, left
over from an earlier run. Zero curves is not an error.

## `.GRDS` - Ground Grid

Plain SQLite, one table (`GroundGrid`), one row per grid. Holds what an
IEEE 80 study is for: `RG` (resistance to remote earth), `GPR` (ground
potential rise), and calculated vs. tolerable mesh and step voltages
(`EMeshC`/`EMeshT`, `EStepC`/`EStepT`) with the coordinates of each worst
point. Two columns need handling before display, both done in
`etap_reader/ground_grid.py`:

- `data` - ETAP's serialized conductor and rod geometry, 13-25 KB of hex per
  row, layout not decoded. Replaced with `GeometryBytes` (its length) so its
  presence is visible without filling the table view with one unreadable cell.
- `RunDate` - seconds since the epoch stored as a REAL, which renders as
  `1,778,879,078`. Converted to ISO; `0` means the grid was never run.

Rows for grids that were never run are all zeros with a null `data` - present
in the file but carrying no result.

## `.MDF` table conventions

~870 tables per project. Broad patterns:

- One "current" table per element type (`Bus`, `Cable`, `XFMR2`, ...).
- `<Table>H1` through `<Table>H8` - per-revision-type snapshot/history
  variants. Different H-numbers seem to correspond to different property
  pages of that element's edit dialog (e.g. arc-flash fields cluster in
  `BusH1`) rather than a strict revision sequence - not fully mapped.
- `<Table>_R` - rating sub-tables. Empty in every project checked so far.
- Report-generation "print formatting" leaks into real result tables: ETAP
  writes literal spacer rows into some short-circuit/load-flow result
  tables to create blank lines in the printed report (`FromBus =
  "###<<<BlankLine>>>###"`, `ToBus = "abc"`, every numeric column `0`).
  `etap_reader/study_result.py` strips these on import.

### Connectivity (`FromBus`/`ToBus`/`FromElement`/`ToElement`/`Bus`)

These text fields are how `etap_reader/categories.py`'s
`CONNECTIVITY_TABLES` and the Single Line Explorer / auto-layout SLD
resolve which equipment connects to what. Important caveats discovered the
hard way:

- **Equipment often connects through other equipment, not straight to a
  bus** (breaker -> transformer -> breaker -> bus). A naive "does this
  reference match a Bus.ID" filter misses most of the network. You have to
  build one graph over bus IDs *and* every branch/leaf equipment ID, then
  trace through non-bus nodes until you hit a bus. See
  `etap_reader/sld_graph.py`.
- **These fields appear to populate as a side effect of running a study**,
  not automatically while editing the diagram. A hand-built test project
  that had *never* had a Load Flow or Short Circuit study run on it had
  **100% blank** connectivity fields across every single piece of
  equipment (`FromBus`/`ToBus`/`Bus` all `NULL`), even for elements that
  were clearly wired up on the canvas. A separately-tested real project
  with SC and LF study history had populated (if sometimes stale)
  connectivity.
- **Mass-duplicated equipment can carry stale, shared connectivity.** In a
  real 87-turbine wind farm project, 86 of 87 turbine step-up transformers
  all listed the *identical* `FromBus`/`ToBus` pair - not possible
  electrically. Working theory: when equipment is copied to create many
  similar instances (a common ETAP workflow for repetitive elements like
  wind turbines), the clone inherits the original's last-cached
  connectivity text, and unless a study is re-run in a way that refreshes
  every instance individually, the copies keep sharing that stale value.
  **A plain "Save" does not appear to fix this** - it looks tied to
  actually re-running studies, not a caching bug that resaving flushes.
  Not yet confirmed whether re-running the study *does* fix it.
- Some references are just genuinely dangling - equipment renamed or
  deleted over a project's edit history, leaving an orphaned string behind
  in another element's terminal field, matching nothing anywhere in the
  current database. Nothing to recover there; the source data itself is
  missing that link.
- `GroundSwitch` legitimately has a blank second terminal for every row -
  a ground switch connects to earth, not another named bus. That's correct
  data, not missing data.

### Bus-specific diagram properties that *are* plainly accessible

- **`BusH1.BusOrientation`** - real per-bus data (not a generic default).
  Values seen: `0` (the overwhelming majority - presumably "Horizontal")
  and `3` (a handful of buses, notably a project's main collector bus -
  presumably "Vertical" or a rotated variant). Exact enum mapping for
  every value not confirmed - no vendor documentation found.
- Ruled out as *not* being diagram bus-bar length/size: `BusH1.Width` /
  `Height` / `Depth`. These are switchgear **enclosure** dimensions for
  arc-flash incident-energy calculations (that whole table is dominated by
  arc-flash fields - `WorkingDistance`, `ArcCurrent`, `PPEStandard`,
  enclosure reflectivity). Confirmed by checking actual values: only 3
  distinct values across 202 buses, tied to voltage class, not to how long
  you drew the bus.

### Where element position/size/rotation on the one-line diagram actually lives

Short answer: **`OLV.Stream`** (table `OLV`, "One-Line View"). A `BLOB`
holding the entire diagram's graphics for one view/page. Confirmed real by
size alone - proportional to diagram complexity (22.9 KB for a ~10-element
hand-built test model, 1.8 MB for a real 202-bus wind farm project).

Tables that looked promising by name but turned out to be **empty or
near-empty** in every project checked (don't waste time here first):
`DiagramRec` (near-empty; the one populated row we found decodes to short
study-case tag text like `"LF_Charging"`/`"SC"`/`"MS"`, shown on the
canvas as an info box, not element geometry), `LayersDisplay` (513 rows,
**zero** with data), `Star`/`StarProps`/`StarZ`, `CompositeNetwork`,
`PresProxyProps`, `Junction`/`JunctionExt`/`Edge`/`EdgeExt`/`Pole` (GIS/
underground-routing features, 1 row each - unused in every project
checked), `DevAssoc` (1 row, a global settings record, not per-element).

`OLV.Stream` binary structure, as decoded so far (see the Testing Log
below for how):

- Not a documented format - looks like a custom MFC/GDI-era serialization
  (Windows `CArchive`-style). UTF-16LE strings appear with a
  `\xff\xfe\xff<len-byte>` prefix pattern.
- Header region: short study-type abbreviation tags (`LF`, `SC`, `MS`,
  `TS`, `OPF`, `ULF`, `HA`, ...) each followed by `Prompt`/`Normal` -
  looks like per-study-mode display settings, not geometry.
- A run of repeating `DBID` + `{00000000-0000-0000-0000-000000000000}`
  blocks - looked like empty per-object GUID placeholders in every sample
  so far; not yet understood.
- The real content: one block per **placed symbol**, each containing a
  `::VERSION::` marker followed by the symbol's graphic resource filename
  (e.g. `autil3.emf` = utility source, `abusduct3.emf`, `axform23.emf` =
  2-winding transformer, `aimp3.emf` = impedance/reactor, `asynmtr3.emf`,
  `astat3.emf` = static load). Plain buses don't get an `.emf` reference -
  they're presumably drawn parametrically as a line, not a fixed icon.
- Scanning for 4×int32 clusters near these blocks turns up very plausible
  **bounding rectangles** - both apparent placed/absolute ones (4-5 digit
  values, e.g. `(21950, 5550, 22050, 5650)`, a clean 100x100 box) and
  apparent local/template ones centered on the origin (e.g.
  `(-100,-300,100,300)`, `(-300,-300,300,300)`) which look like a symbol's
  own internal geometry before translation to its canvas position.
- **Wire routing is a separate flat list of `(X, Y)` waypoint pairs**,
  tagged by a recurring `9a 00 2d 80` sentinel, distinct from the
  per-symbol bounding rects above. Confirmed directly (not just inferred)
  in Testing Log Rounds 3 and 6: bending a wire inserts a 24-byte
  coordinate-pair entry into this list, and straightening it removes the
  exact same entry.
- **Open problem:** a field pair that scales by an exact ×100 factor
  keeps showing up at symbol blocks *unrelated* to whatever was actually
  edited, on every round tested so far except a genuine no-op save
  (Round 4, where nothing in the stream changed at all). So it's tied to
  *some* edit happening somewhere, not to which element moved or whether
  the canvas grew - still not identified. May be a print/paper-fit scale
  or a dependency/layout checksum recomputed globally on any change. Not
  yet load-bearing for reading real positions, but worth ruling out
  before writing a renderer that assumes bounding-rect values are stable
  across saves.

## Testing log

Method: hand-build (or edit) a small test project in ETAP, export it,
diff the `OLV.Stream` bytes against a prior version where exactly one
known change was made. Each entry below is one round.

### Round 1 - baseline capture

Built a minimal test project by hand (never had a study run on it):
4 buses (`Bus`, `Bus1`, `Bus2`, `Bus3`), 2 cables, 3 transformers (incl.
one real instance `T1`), 1 breaker, 1 switch, 2 utility sources, 2 static
loads, 1 sync generator, 1 wind turbine generator.

Finding: **100% of connectivity fields blank** across every element -
confirms connectivity text populates via study runs, not live editing (see
above). Captured `OLV.Stream` as the baseline for Round 2.

### Round 2 - single bus, length only

Change requested: lengthen exactly one bus bar (roughly doubled), nothing
else touched, re-saved in place.

Result: `OLV.Stream` stayed the same total length (22,922 bytes both
before/after - a clean in-place value change, no structural
insertion/deletion). 131 bytes differed across 60 separate spots.

Two distinct patterns in the diff:
1. A field pair repeated at all 8 symbol blocks scaled by **exactly
   ×100** (e.g. `20600` -> `2,060,000`; `3494` -> `349,400`) - identical
   everywhere, not just near the changed bus. Read as a global "fit to
   view" scale recalculated for the whole diagram, not per-element data.
2. A field near a `"notation"` label changed by a non-round ratio
   (`26074` -> `37474`, ×1.44) - looks like a derived view bounding-box
   extent, not the raw length value itself.
3. Scattered single-byte toggles between `0x04` and `0x06` at many
   unrelated offsets - looks like a revision/modification counter
   incrementing on save, not geometry.

**Conclusion:** the length change is real and produced a real, structured
response, but it's convolved with a global view-rescale side effect
that a single diff can't cleanly separate out.

### Round 3 - move one bus within existing bounds

Change requested: drag `Bus3` to a new position without growing the
diagram's overall bounding box (goal: starve the suspected auto-fit
rescale of a reason to fire, isolating a cleaner diff). The move also
forced the wire from `Z1` to `Bus3` to take a bent (orthogonal) path
instead of a straight line, visible in the "after" screenshot.

Result: unlike Round 2, `OLV.Stream` **grew** this time - 22,922 -> 23,018
bytes (+96). A same-length diff no longer applies; used
`difflib.SequenceMatcher` (insertion/deletion-aware) instead of a
positional byte compare.

Three distinct findings:

1. **A clean, isolated 48-byte insertion**, at old-stream offset ~21554
   (one contiguous new block, confirmed via `SequenceMatcher` opcodes -
   not scattered). Read as 4-byte little-endian int32 fields, it's a
   repeating record shape containing plausible coordinate values - `8600`,
   `14698` (repeated twice), `9400` (repeated three times), `14648` - each
   preceded by a recurring `9a 00 2d 80` sentinel/tag also present
   (unchanged) in the *original* stream at a nearby offset. Working read:
   this is a new **wire waypoint / bend-point record**, added because the
   `Z1`-to-`Bus3` connector now needs an elbow to reach the relocated bus,
   consistent with the screenshot. This is the strongest evidence yet for
   real, decodable per-element geometry in this stream.

2. **A handful of direct, unscaled coordinate-field swaps** near offsets
   14431-14623 - e.g. one field went `0x55f0` (22000) -> `0x3938` (14648)
   with no scale artifact, a clean value replacement. `14648` is the exact
   same value that shows up inside the new waypoint block above, which is
   a good cross-check: the bus's own bounding-rect field and the new
   wire-waypoint both agree on a value that plausibly *is* Bus3's new
   coordinate. Nearby paired fields shifted `22000/9188` -> `15248/8600`,
   consistent with one clean (X, Y)-style translation, not a rescale.

3. **The Round 2 "×100 paired field" reappeared here too, but at symbol
   blocks unrelated to Bus3** (e.g. offsets ~3222 and ~10891, far from the
   edited element) - and this time the diagram's bounding box did **not**
   grow. That weakens Round 2's "auto-fit rescale on bounding-box growth"
   theory: if it only fired when content outgrew the view, it shouldn't
   have changed here. Still unexplained; open possibilities: a
   print/plot-scale value cached and recomputed on every save regardless
   of what changed, or some other document-level field unrelated to
   per-element geometry. **Needs its own isolated test** - see Round 4.

**Conclusion:** two separate phenomena are now distinguishable in this
stream: (a) real per-element position data (bounding-rect coordinate
fields, and now wire waypoint records) that updates cleanly and locally
when you move something, and (b) a mystery globally-duplicated ×100 field
pair that changes on every save for reasons still unrelated to which
element moved or whether the canvas grew. (a) is the useful part for a
future SLD renderer; (b) can probably be ignored once understood, but
needs to be ruled out as noise rather than assumed.

### Round 4 - zero-edit control

Goal: isolate the mystery ×100 field from Round 3. Opened the project in
ETAP, changed nothing, closed it (ETAP appears to write on close even
without an explicit Save - the file's mtime advanced, same size).

Result: **`OLV.Stream` is byte-for-byte identical to Round 3's end state.
Zero differing bytes across all 23,018 bytes.**

This rules out the "save-time counter/checksum/timestamp" theory from
Round 3 outright - if it were noise generated on every write, it would
have changed here too, and it didn't. Combined with Round 3's finding
(the ×100 field changed at symbol blocks *unrelated* to the bus that
moved), the picture is now:

- `OLV.Stream` only changes when the diagram content actually changes -
  confirmed stable across a real no-op save.
- Round 3's ×100 field change, even though it touched unrelated symbols,
  was therefore a genuine *consequence* of that round's edit - not
  save-time noise, and not (per Round 3's own evidence) simply "the view
  rescaling because the bounding box grew" either, since Round 3 didn't
  grow the bounding box. Best current theory: moving one element causes
  ETAP to recompute some diagram-wide derived value (e.g. a print
  layout/paper-fit scale, or a dependency graph checksum) for *every*
  symbol, not just the one that moved - which would explain "changes
  everywhere" without requiring "changes only when the canvas grows."
  Still not confirmed either way.

### Round 5 - move a different, more-connected bus

Change requested: move `Bus2` (not `Bus3`) a small amount, save.

Result: same stream length as Round 4's baseline (23,018 bytes, no new
insertion this time - unlike Round 3, this move didn't need a new wire
waypoint). But the diff is much bigger and more widespread: 242 bytes
across 110 separate spans, versus Round 3's one clean 48-byte block.

Findings:

- **The ×100 mystery field changed again** (same offsets as before, ~3222,
  ~10891, ~13105) - consistent with Round 4's conclusion that it's
  recomputed on *any* edit, not tied to bounding-box growth or to which
  element moved.
- **A cluster of ~12 separate 2-byte coordinate fields, spread across
  offsets 10537-12929 and 21700-21870, all shifted by a consistent
  delta** - most by exactly **+450**, a handful by +400, +388, or +501
  (close variants, likely different field roles - e.g. a rect's left edge
  vs. right edge vs. a label anchor - rounding slightly differently).
  Decoded as uint16 LE, e.g. `22001 -> 22401`, `21950 -> 22460`,
  `20500 -> 20950`, all a consistent rightward/downward shift.

**Read:** unlike Round 3's `Bus3` move (fairly isolated - one element's
bounding rect plus one new wire bend point), moving `Bus2` shifted a
*whole cluster* of other symbols' coordinates together, by a near-uniform
delta. Working theory: `Bus2` is more centrally connected in this test
topology, and ETAP drags every element directly wired to a moved bus
along with it (standard one-line-editor behavior), rather than only
updating the bus itself and letting wires stretch. The slightly-varying
deltas (450 vs 400 vs 388 vs 501) probably reflect different anchor
points per field (e.g. rect corners) rather than noise. This is a solid,
independent confirmation that these clustered fields are real, decodable
per-element position data - just fanned out further than Round 3's more
isolated case.

### Round 6 - move Z1 to remove a bend (confirms the waypoint theory)

Change requested: move `Z1` closer to `Bus2`, straightening out the wire
that Round 3 had bent. Confirmed via before/after screenshots.

Result: `OLV.Stream` **shrank** this time - 23,018 -> 22,994 bytes (-24),
the mirror image of Round 3's +96 growth. An insertion-aware diff located
almost all of the change as one clean 24-byte deletion at offset ~21614 -
inside the exact same region Round 3 had inserted into.

Decoding both versions of that region as a flat list of int32 LE values:

- **Round 5 (bent wire):** `14698, 8600, 0, <marker>, 14698, 8600, 14698,
  9400, 0, <marker>, 14698, 9400, 14648, 9400, 0, 154`
- **Round 6 (straightened):** `14600, 8600, 0, <marker>, 14600, 8600,
  14600, 9400, 0, 154`

Round 6 is Round 5's list with one 6-value / 24-byte chunk removed:
`0, <marker>, 14698, 9400, 14648, 9400` - and those last two values,
`(14698, 9400)` and `(14648, 9400)`, are **the exact same coordinate pair
Round 3 originally inserted** when the Z1-to-Bus3 wire first needed a
bend. The X value that survives in both records also shifted slightly
across every occurrence (`14698` -> `14600`), matching Z1's small
horizontal move in the screenshot.

**This confirms the theory directly, not just by inference:** this region
of `OLV.Stream` is a flat list of `(X, Y)` wire-waypoint coordinate pairs
(separated/tagged by a recurring `9a 00 2d 80` sentinel), and ETAP
appends/removes entries from this list as bends are created or removed
by (re-)routing a connector - Round 3 added one, Round 6 removed the same
one. Combined with Round 3/5's bounding-rect coordinate findings, the
practical geometry story is now: **bus/symbol positions live in per-block
bounding-rect fields, and wire routing lives in this separate waypoint
list** - both are real, both are decodable at the value level for simple
cases like this test model.

### Round 7 - planned

Open question before attempting a real renderer: confirm the waypoint
list's exact record framing (where each entry starts/ends, and what the
`9a 00 2d 80` marker and the `0`/`154` trailing values mean structurally)
against a case with more than one bend, and pin down which `.emf` symbol
block each waypoint list "belongs" to (i.e. how the file associates a
waypoint run with a specific wire/connector rather than just floating in
the stream).

### Round 9 - the missing link: element tags, bus pins, and wire segments

The renderer up to Round 8 labelled elements by generic *type* (from the
`.emf` filename) rather than by their real tag. Chasing that gap turned
out to unlock the rest of the format.

**1. Elements are identified by their database `IID`, stored inline.**
Each element's `IID` from the SQL tables (e.g. `XFMR2.IID = 2339` for
`T1`) appears verbatim as a little-endian int32 in `OLV.Stream`, a short
distance *before* that element's `::VERSION::` block. Resolving each
block to the nearest preceding `IID` gave a clean 1:1 mapping for all 9
elements, independently corroborated by symbol type (the block that
resolved to `U1` is the one holding `autil3.emf`, `T1` -> `axform23.emf`,
and so on). Filter out prototype rows (`IID < 1000`) and any `IID` whose
byte pattern occurs more than once, or you get collisions with ordinary
small integers.

**2. Bus geometry: 50x50 "pin" boxes.** Every point where something
attaches to a bus is stored as an exactly-50x50 rect. Grouping these by
their Y gives one row per bus, and the X values give the attachment
points along it. In the test model this produced Bus1 with 2 pins, Bus2
with **1**, and Bus3 with 3 - matching the real diagram exactly, where
Bus2 is drawn as a bare junction dot with no visible bar. True bus bar
*length* still isn't decoded (ETAP draws the bar wider than its outermost
pin), so the renderer still pads a fixed amount either side.

**3. Element anchors: 100x100 boxes.** Placed symbols get an
exactly-100x100 rect. Note these also appear for terminal stubs, so a
block can contain several - see below for picking the right one.

**4. Wire routing: `0x802b` / `0x802d` records.** A connector is a
`0x802b` header (bounding box + segment count) followed by one or more
`0x802d` segment records, each holding `x1,y1,x2,y2` as int32 LE. Every
segment decoded to a clean orthogonal line landing exactly on element and
bus-pin coordinates. This is the same structure whose 24-byte record was
inserted in Round 3 and removed again in Round 6.

**5. The anchor-picking rule (this was the subtle one).** Naively taking
the first 100x100 box in an element's block puts `U1`, `Syn1`, and
`Load1` in visibly wrong places, because a block also contains boxes for
terminal stubs. The rule that works for every element: each block holds a
**thin (<=2 unit wide) connector bbox** - the element's stem. The element
sits at whichever end of that stem is **not** on a bus row; if neither end
is on a bus (an in-line element such as a bus duct or transformer sitting
between two other things), it sits at the top end. Applying this placed
all six iconed elements correctly, including `U1` above `Bus1` and
`Syn1`/`Load1` hanging below `Bus3` in the right left-to-right order.

**Result:** a complete single-line diagram rendered end-to-end from the
`.MDF` alone - correct topology, real bus bars, real wire routing, real
ETAP symbols, and real equipment tags - validated against an ETAP
screenshot of the same model. Remaining known gaps: true bus-bar length,
element label placement (ETAP positions these deliberately; the renderer
just offsets them), and the fact that ETAP stores no connector spanning
an in-line symbol itself, so short gaps have to be closed heuristically.

### Bus bar length - solved

Bus bar extent **is** stored, and this closes the long-standing gap where
the renderer had to guess bar length from pin spread plus padding.

A bus bar is a box **exactly 100 units tall, vertically centred on the bus
row**, whose *width is the bar's drawn length*:

```
(x_left, y-50) - (x_right, y+50)
```

Confirmed by a controlled edit - one bus resized and a different one moved,
with nothing else touched:

| Bus | Before | After | Change |
|---|---|---|---|
| Bus1 | `19999..25103` (w 5104) | unchanged | - |
| Bus3 | `20599..26433` (w 5834) | `20599..30350` (w **9751**) | the resize |
| Bus7 | row `13600`, w 800 | row `14200`, w 800 | the relocation |

Every bus has one, including stubs: a junction-dot bus (drawn as a tiny
tick, not a bar) has width exactly 100. In the test model Bus2/Bus4/Bus5/
Bus6 are all 100 wide, matching how ETAP draws them.

Caveat when reading these: ordinary 100x100 **icon anchors** can also sit
on a bus row and match the "100 tall, centred" test. Take the *widest*
candidate on the row - a real bar is never narrower than an icon anchor.

### Element anchors: keyed off the symbol filename, and direction depends on type

Earlier rounds located a symbol's placement box by scanning its
`::VERSION::` block. That is unreliable, because **a block also carries
neighbouring objects' geometry** - one element's block was found to hold
another's connector stem, which put a utility source on a transmission
line's terminal instead of its own position above the feeding bus.

The robust rule keys off the element's own `.emf` filename instead, but
the direction differs by element class:

- **In-line elements** (breaker, bus duct, transformer, cable, line,
  reactor): placement box is the first 100x100 rect **after** the
  filename, within a few hundred bytes.
- **Leaf elements** (utility, motor, load, capacitor): placement box is
  the last 100x100 rect **before** the filename, consistently ~1600-2200
  bytes earlier.

Verified against a real ETAP screenshot for all 13 elements in the test
model. This single rule replaced several accumulated block-scanning
heuristics.

### Bus rows: pair blocks to rows in serialization order

Two buses can occupy the **same Y** (side by side on the canvas), so
keying bus rows by Y alone silently drops one. Picking "the first 50x50
pin inside the bus's block" fails for the same shared-geometry reason as
above. ETAP serializes objects in creation order and pin geometry follows
that order, so pairing the Nth bus block with the Nth distinct bus row is
both simpler and correct.

### Connector records are NOT identified by a fixed marker byte

An earlier round matched wire segments on a literal `0x802d` tag. That is
an MFC `CArchive` **class index**, assigned per file in the order classes
first appear - it was `0x802d` in one save and `0x8075` in the next, and
wire detection silently returned zero segments. Detect segments
structurally instead: any 4 consecutive int32 forming an axis-aligned
segment whose *both* endpoints land on an independently-known bus pin or
element anchor. That also rejects the look-alike 305-unit-tall text label
boxes (which are 0 units wide, versus a real connector stem's exactly 1).

### Equipment ratings - and a units trap worth knowing about

Nameplate ratings live in each element's own table, keyed by `IID`/`ID`,
and are what ETAP renders in the databox next to each symbol:

| Element | Table.Column | Displayed as |
|---|---|---|
| Utility | `Utility.ThreePhase`, `.KV` | MVAsc, kV |
| Bus | `Bus.NominalkV` | kV |
| 2-winding transformer | `XFMR2.AnsiMVA`, `.PrimkV`, `.SeckV` | MVA, kV/kV |
| Synchronous motor/generator | `SynMotor.HP`, `.KV` | HP, kV |
| Static load | `StaticLoad.KVA`, `.KV` | MVA, kV |
| Bus duct | `BusDuct.ContinuousAmp` | A |

**The trap: several columns named `...MVA` actually hold kVA.** Reading
them at face value overstates equipment by 1000x. `XFMR2.AnsiMVA` reads
`1000` for a 1 MVA transformer; `Utility.ThreePhase` reads `250000` for a
250 MVAsc source.

Don't take that on faith - ETAP stores derived values alongside, so every
rating can be checked against physics rather than assumed:

- `T1`: 1000 kVA at `PrimkV` 25 -> 23.09 A, matching the stored
  `PrimFLA` exactly (1000 *MVA* would imply 23,094 A). Same for
  `SecFLA` at 0.6 kV -> 962.25 A.
- `U1`: 250,000 kVA at 25 kV -> 5.7735 kA, matching stored `kAsc3p`.
- `Load1`: 300 kVA at 0.6 kV -> 288.7 A, matching stored `Amps`.
- `Syn1`: 100 HP through the stored PF (92.04%) and efficiency (82.01%)
  -> 98.8 kVA, matching the stored `MVA` column - which is therefore also
  kVA, not MVA.

Two elements legitimately show no rating: a bus duct with
`ContinuousAmp = 0` (simply unset), and `Impedance`, whose only
size-like column is `MVAbase` - an impedance base, not a nameplate
rating. ETAP doesn't label impedance elements either.

### Round 7 - locating each symbol's absolute placement rect (no ETAP edit, pure parsing)

Instead of another edit/diff round, parsed the Round 6 stream directly to
find, for each placed symbol, its **absolute canvas bounding rect** (not
just the local/template one centered on the origin from earlier rounds).

Method: find each `.emf` filename's exact byte offset (as a UTF-16LE
string - earlier ASCII-only searches had missed these because the
correct decode is UTF-16LE, not byte-pattern matching; re-decoding the
*whole* stream with `.decode('utf-16le', errors='ignore')` and running
text regexes on the result is the reliable way to find any string in
this format, including the real 202-bus project's stream which does
contain plenty of `.emf` references once decoded correctly). Right after
each filename: a local rect `(-300,-300,300,300)` (a 600x600
template-space box, same for every symbol), then - after a few
intervening fields (a 2-byte tag, some zeros) - a second 4×int32-LE rect
with realistic canvas-scale values.

Result for this test model's four `.emf`-referenced elements:

| Element | `.emf` file | Absolute rect (canvas units) |
|---|---|---|
| `BusDuct1` | `abusduct3.emf` | `(21950, 5550) - (22050, 5650)` |
| `T1` | `axform23.emf` | `(21950, 7150) - (22050, 7250)` |
| `Z1` | `aimp3.emf` | `(20150, 8550) - (20250, 8650)` |
| `Load1` | `astat3.emf` | not cleanly isolated - see below |

Cross-checks that make these trustworthy, not just plausible-looking
numbers: `BusDuct1`'s rect is the *exact* `(21950, 5550, 22050, 5650)`
example already documented earlier (independently found in the real
202-bus project) - same values, same "clean 100x100 box" shape.
`Z1`'s Y value (`8600` center) matches the value independently decoded
from the wire-waypoint diffs in Rounds 5-6. And the relative layout is
right: `BusDuct1` and `T1` share the same X (22000) and are stacked
vertically, exactly matching the screenshot where BusDuct1 sits directly
above T1 on the Bus1-to-Bus2 chain; `Z1` sits left and below, matching
its post-move position.

`Load1` didn't yield a clean isolated rect with the same simple scan -
its block interleaves what looks like wire-waypoint data (same
`14648`/`9400`-family values seen in the Z1 waypoint list) with its own
placement fields, likely because its connecting wire has its own bend(s)
routed through a similar area. Needs a more careful parse than the
"first 4 sane-looking int32s after the filename" heuristic used here.

**Proof-of-concept render:** using these three rects, rendered the
matching `.emf` icons via native Windows GDI (`PlayEnhMetaFile` through
`ctypes`, no external tools needed - Inkscape/ImageMagick weren't
available, pywin32's high-level wrappers didn't expose `GetEnhMetaFile`
so this goes through `gdi32.dll` directly) and composited them at their
decoded positions. The result visually matches the real diagram's
topology (BusDuct1 stacked above T1, Z1 offset down-left, wired
together) using **real decoded position data and real ETAP artwork**,
not placeholders. This is strong end-to-end validation that a graphical
SLD renderer is buildable from this data - the pieces (bus/symbol
position, wire routing, symbol icons) are each individually decodable
for a simple model like this one.

### Round 8 - blind comparison against real ETAP (renderer prototype vs. ground truth)

Built a standalone parser+renderer script (`render_sld.py`, local
scratch tool, not committed - see the copyright note above) that: finds
every `::VERSION::` block, extracts its `.emf` filename if present,
locates its absolute placement rect, renders the real icon via GDI, and
composites everything by position. Ran it blind (without seeing ETAP)
against a file the user changed without telling me first, then compared
the render to a real ETAP screenshot of the same file. Two results:

1. **Confirmed correct:** overall topology and vertical column alignment
   (`U1` -> `Bus1` -> `BusDuct1` -> `T1` -> `Bus2` -> `Z1` -> `Bus3`) matched
   the real diagram exactly, including a subtle detail - `Bus2`'s decoded
   placement box was the smallest of the three buses, and in the real
   diagram `Bus2` has no visible bar at all, just a junction dot. Small
   decoded footprint -> genuinely short bus, self-consistent.

2. **Bug found and fixed:** `Bus1` and `Bus3` decoded to nearly identical
   X coordinates, which looked like noise at first. The real screenshot
   showed why it's real data: both buses' **left edges** are genuinely
   vertically aligned in this diagram (both bars start at about the same
   X and extend rightward by different amounts). The bus coordinate this
   parser extracts is the bus's **left endpoint, not its center** - the
   renderer's placeholder (a short line centered on the point) was wrong;
   it should start at that point and extend right. Bus bar *length*
   itself still isn't decoded (see Round 3/6 - it's plausibly tied to the
   wire-waypoint list rather than the bus's own block), so the renderer
   still can't draw true-to-scale bus bars, but the anchor point's
   meaning is now understood correctly.

**Not yet solved / next steps toward a real renderer:**
- Extracting rects for elements *without* an `.emf` reference (buses,
  drawn presumably as parametric lines rather than icons) - the same
  9 `::VERSION::` blocks exist for all 9 elements including buses, but
  the fixed-offset heuristic that worked for iconed symbols didn't
  locate a rect for the non-iconed ones in a first pass.
- A general, robust per-block parser (current method is "guess a filename
  offset, scan forward for 4 plausible int32s" - works for isolated
  cases, breaks down when a block's own rect is interleaved with
  waypoint data, as with `Load1`).
- The distribution question for symbol icons - **not resolved, and not
  to be resolved by unilaterally deciding**: the `.emf` files used above
  are ETAP's own copyrighted artwork (from a local company symbol
  library, not this project), rendered and composited **locally only**
  for this test - nothing from that source was committed to the repo.
  Any real feature needs an explicit decision on where icons come from
  before it ships (candidates discussed: read from the user's own local
  ETAP install at runtime, or draw original simplified equivalents).
