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
| `<name>.SA1S` / `.SA2S` / `.LF1S` / `.UL1S` / etc. | Study result files. Unlike the project database, **these are already plain SQLite** (ETAP's `Etaps.ini` has `OutputToSQLite=1`) - no SQL Server involved, read directly. |
| `classification.xml` | Small, mostly empty in projects we've seen (`<root schema="2"><Item Name="Classifications" .../></root>`) - equipment classification tree metadata, not yet found to be load-bearing for anything this tool does. |

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
- **Open problem (see Testing Log):** a controlled single-bus length
  change produced a real, structured diff, but several fields scaled by
  an exact ×100 factor *identically across every symbol in the file*, not
  just the one that changed. Working theory: coordinates are stored
  relative to a view-level auto-fit scale that gets recalculated for the
  whole diagram whenever content size changes, which convolves "this
  element's real length" with "the view rescaled because of it." Still
  being isolated - see the log for the current test plan.

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

### Round 3 - planned

Goal: a change that does **not** grow the diagram's overall bounding box,
so the suspected auto-fit view scale has no reason to recalculate, giving
a cleaner, smaller diff isolated to just the changed element.

_(Fill in result here after the next test.)_
