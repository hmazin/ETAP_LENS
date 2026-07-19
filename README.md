<p align="center">
  <img src="docs/banner.png" alt="ETAP Lens" width="100%">
</p>

# ETAP Lens

**Browse ETAP project models and study results in your browser — no ETAP license required.**

ETAP Lens is a local web app for power systems engineers to explore ETAP project data (buses, cables, transformers, generators, loads, protective devices) and study results (short circuit, load flow) without needing ETAP itself installed. Point it at a project folder, pick a file, and get a searchable, filterable, exportable view of everything inside it.

> **Unofficial / independent project.** ETAP Lens is not affiliated with, endorsed by, or associated with ETAP® or Operation Technology, Inc. "ETAP" is a trademark of its respective owner; it's referenced here only to describe compatibility with ETAP's file formats.

## Why this exists

An ETAP `.oti` file is **not** the project database — it's a small container holding a connection pointer (ODBC DSN/DBQ) plus user and permission records. The real engineering data lives in a SQL Server database file next to it:

- `<name>.MDF` — the live database (preferred)
- `<name>.BAK` — a backup, used as a fallback if no `.MDF` is found

ETAP Lens figures out which file actually has the data, reads it (via a local, throwaway copy — your original files are never modified), and shows everything in a browsable web UI.

Study result files (`.SA1S`, `.SA2S`, `.LF1S`, `.UL1S`) are a separate case: they're already plain SQLite files (ETAP writes them that way), so they're read directly — no SQL Server involved, and loading is near-instant.

## Features

- **Load a project model** (`.oti`/`.mdf`/`.bak`) or **study results** — short circuit (`.SA1S` ANSI duty, `.SA2S` fault currents by type) and load flow (`.LF1S` balanced, `.UL1S` unbalanced/3-phase)
- **In-app folder browser** — navigate real folders and drives from inside the page (no native OS dialog limitations), with quick-access shortcuts to Desktop/Documents/Downloads/Home, and a "Select This Folder" action to scan and pick from everything loadable in it
- **Load an entire folder at once** — paste or browse to a folder and get a pick-list of every `.oti`/`.mdf`/`.bak`/study-result file in it
- **Curated category views** tailored to what's loaded:
  - Project model: Buses, Cables & Lines, Transformers, Generators & Sources, Loads & Motors, Protective Devices, Capacitors & Reactors, Meters
  - Short-circuit study: Device Duty, Bus Duty (Interrupting/Momentary), Fault Currents by type, Clipping Current, Alerts
  - Load flow study: Bus Voltage & Loading, Branch Loading/Losses, System Totals, Voltage Violations, Alerts
- **Single Line (Bus Explorer)** — pick a bus, see every cable, transformer, breaker/switch, load, and source connected to it (a tabular topology view)
- **Every table view has**: search (scoped to one column or all), sortable columns, pagination, a column-visibility panel (wide tables auto-hide audit-trail noise columns like `IID`/`Revision`/`Checker*`), and a Sum/Average/Min/Max/Count summary row
- **Export** any view to CSV or Excel (`.xlsx`), respecting your current filter/search/column-visibility state
- **Violations Report** — one click bundles every alert/violation table in a loaded study into a single formatted Excel workbook
- **Unload** — remove any loaded project from the cache individually, or clear everything at once
- **All Tables (raw)** — browse any underlying table directly, including per-revision history (`H1`–`H8`) and rating (`_R`) tables not part of the curated categories

## Screenshot

*(Add a screenshot here — run the app, load a project, and drop an image at `docs/screenshot.png`.)*

## Requirements

- Windows (this project relies on SQL Server LocalDB and the Windows registry for a couple of features — see [Platform notes](#platform-notes))
- Python 3.10+
- A local SQL Server / SQL Server LocalDB instance. ETAP itself normally installs one named `ETAPLocalDB19` (matches the `DSN=otilocaldb19` seen in `.oti` connection strings) — if that's present, no extra setup is needed. Study result files (`.SA1S`/`.SA2S`/`.LF1S`/`.UL1S`) don't need this at all, since they're already SQLite.
- `sqlcmd` and the "ODBC Driver 17 for SQL Server" (both ship with SQL Server / SSMS tooling)

## Quick start

```bash
git clone https://github.com/hmazin/ETAP_LENS.git
cd ETAP_LENS
pip install -r requirements.txt
python app.py
```

Then open **http://127.0.0.1:5151**. Click **Browse Folders...** to navigate to your ETAP project directory, or paste a path directly. The first load of an `.mdf`/`.bak` takes a minute or two (it copies the database, attaches it, and reads every table); study result files load in a couple of seconds. Results are cached in `cache/` (git-ignored) and reload instantly unless the source file changes.

## Command-line tools

If you just want a quick dump without the browser UI:

```bash
python read_oti.py "path\to\etapmodel.oti"                     # dump the .OTI container itself
python dump_mdf.py "path\to\etapmodel.oti" --out model.sqlite   # full project -> one portable .sqlite file
```

`dump_mdf.py` accepts any of `.oti`/`.mdf`/`.bak`/`.sa1s`/`.sa2s`/`.lf1s`/`.ul1s` directly and auto-resolves `.oti` → `.mdf`/`.bak` the same way the web app does.

## How it works

1. **`etap_reader/locate.py`** — given an `.oti`, parses its `ODBCInfo/ConnectionString` stream to get the real database name (`DBQ=`), then looks for `<name>.MDF` / `<name>.BAK` next to it. Study result extensions (`etap_reader/study_result.py`) are recognized directly.
2. **`etap_reader/mdf_dump.py`** — for `.mdf`/`.bak`: copies the source file, attaches/restores it on a local SQL Server LocalDB instance, walks `INFORMATION_SCHEMA` to read every table's schema and rows, writes it all into a single SQLite file, then detaches and deletes the working copy.
   **`etap_reader/study_result.py`** — for study result files: copies the (already-SQLite) file directly via SQLite's own backup API, then strips ETAP's print-formatting spacer rows (`###<<<BlankLine>>>###`) before indexing. Much faster since there's no SQL Server step.
3. **`etap_reader/project_cache.py`** — caches the resulting `.sqlite` per source file (keyed by path + size + mtime) so repeat loads are instant, and handles unloading.
4. **`etap_reader/browse_fs.py`** / **`etap_reader/folder_scan.py`** — server-side filesystem browsing, since browsers don't expose real paths from native file pickers and a native "select folder" dialog doesn't show files while browsing.
5. **`etap_reader/xlsx_export.py`** — formats CSV-shaped data into styled `.xlsx` workbooks for export and the violations report.
6. **`app.py`** — a Flask API over the cached SQLite file; **`static/app.js`** is a plain-JS single-page frontend with no build step (no npm, no bundler — just open and edit).

## Project structure

```
app.py                      Flask app / API routes
etap_reader/
  locate.py                 .oti -> .mdf/.bak resolution, study file recognition
  mdf_dump.py                SQL Server LocalDB attach + dump to SQLite
  study_result.py            direct SQLite copy + cleanup for study result files
  categories.py               curated table groupings per project/study type
  project_cache.py            caching, loading, unloading
  folder_scan.py               flat "what's loadable in this folder" scan
  browse_fs.py                 in-app folder browser backend
  xlsx_export.py               Excel export formatting
  oti_parser.py                 raw .OTI (OLE compound file) parser
templates/index.html         page shell
static/app.js                 frontend logic (tables, search, export, browser modal)
static/style.css               styling
read_oti.py, dump_mdf.py         standalone CLI tools
```

## Platform notes

This was built and tested on Windows against ETAP 24. A few pieces are Windows-specific:

- SQL Server LocalDB (for `.mdf`/`.bak` project models)
- The Windows registry lookup for Desktop/Documents/Downloads quick-access folders (handles OneDrive Known Folder Move redirection)
- Drive-letter enumeration (`C:\`, `D:\`, ...) in the folder browser

Study result file support (`.SA1S`/`.SA2S`/`.LF1S`/`.UL1S`) has no Windows-specific dependencies and should work anywhere Python + Flask run. Cross-platform support for the rest is a good first contribution — see below.

## Known limitations

- The "Single Line" view is a connectivity summary built from each element's `FromBus`/`ToBus`/`Bus`/`FromElement`/`ToElement` fields, not a rendering of the actual one-line diagram graphics (bus positions, symbol placement, wire routing). A graphical SLD renderer would be a substantial follow-on project.
- Only tested against ETAP 24 output. Table/column names may differ across ETAP versions — if something doesn't map correctly, please open an issue with the version you're on.
- `_R` rating sub-tables were empty in every project this was tested against; they're still browsable under "All Tables" if your project populates them.

## Ideas for contribution

Some directions that would add real value, roughly in order of impact vs. effort:

- **Cross-link the model to its study results** — click a bus in the Single Line Explorer and see its short-circuit duty / load flow results inline, pulled from whichever studies are also loaded
- **Cross-platform support** — swap the SQL Server LocalDB dependency for something portable (e.g. `mdbtools`/a pure-Python MDF reader) so this runs on macOS/Linux
- **Graphical one-line diagram** rendering
- **Case comparison view** — same bus/device across multiple loaded study cases side by side
- **Charts** — voltage profile, loading margin, duty-vs-rating visualizations instead of just tables
- **Conditional formatting** in table views — color cells by % of rating
- Support for more ETAP result file types (transient stability, arc flash, reliability, protective coordination)

Found a bug or have a feature idea? Open an issue. Pull requests welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 [Hooman Mazin](https://github.com/hmazin)

## Disclaimer

This is an independent, community-built tool for reading ETAP's file formats. It is not produced, reviewed, or endorsed by ETAP / Operation Technology, Inc. Use it at your own risk, and always cross-check critical engineering results against ETAP itself.
