# ETAP Crystal Report Generator

A Windows companion to ETAP Lens that populates original ETAP `.rpt` templates
from `.SA1S` and `.SA2S` result databases. Crystal Reports handles the layout,
formulas, grouping, highlighting, subreports, pagination, and PDF export.
The same rendering services also power the website's outbound Windows worker.
See [website reporting setup](../docs/REPORTING.md) for that workflow.

## Start

On the development machine, run `Launch.cmd` in this folder. It starts the
32-bit build, matching the installed SAP Crystal Reports runtime. If necessary,
the launcher first runs the build script below.

1. Browse to an ETAP short-circuit result file, or paste its path and click **Inspect**.
2. Select an applicable `.rpt` template. Use **Template settings** to select an
   existing ETAP Formats folder, or **Browse .rpt** to select an individual file.
3. Choose the output directory and optional filename timestamp.
4. **Preview Report** opens the native Crystal viewer with paging, zoom, search,
   print, and refresh. **Export PDF** in that window exports its current report,
   including parameter values entered through the viewer.
5. **Generate PDF** exports directly. Existing PDFs are retained: repeated names
   receive `_2`, `_3`, etc.
6. **Batch Reports** accepts multiple files and checked templates. Each pairing
   is attempted independently; failed pairings show their reason and do not stop
   the remaining reports. Cancellation takes effect after the current report.
   Double-click a result row to open its PDF or read the full error.

The app scans template subdirectories recursively. Template filenames are not
used to infer the study type or to guarantee compatibility. A missing schema
field stops that pairing before a PDF is published.

## Choose the right template family

The tested ETAP 24 studies use the following families from `Formats2400`:

| Internal StudyType | Detected study | Template family |
| --- | --- | --- |
| 1 | Device Duty | `ANSI 3-Phase SC` (momentary/interrupting duty summaries and complete report) |
| 3 | ANSI Half-Cycle / Momentary | `ANSI Unbalanced SC` (fault-current summary, complete, LG/LL/LLG reports) |
| 4 | ANSI 1.5-4 Cycle | `ANSI Unbalanced SC` |
| 5 | ANSI 30-Cycle / Minimum-Fault | `ANSI Unbalanced SC` |

This mapping was established from the supplied databases and template schemas;
it is not a claim about every ETAP version. A momentary *fault-current* `.SA2S`
file does not contain the `SCDSumMom` *device-duty* table. Using a momentary duty
template with that file correctly reports an incompatible schema.

`Templates/` is an external folder beside the executable. The local development
copy contains the supplied template families for testing. Proprietary `.rpt`
files and their local configuration files are excluded from Git; a fresh clone
must use templates from the user's own ETAP installation or authorized archive.
The full Formats folder can be selected without copying it or changing its files.

## Build and prerequisites

- Windows with **.NET Framework 4.8**. Modern .NET alone is not sufficient.
- SAP **Crystal Reports runtime engine for .NET Framework**, matching application
  bitness. The tested machine has 32-bit runtime 13.0.26.3348 (managed SDK assembly
  version 13.0.4000.0). A newer compatible runtime can also be used, but should be
  checked with representative templates before production use.
- Your original ETAP Crystal `.rpt` files. No report designer is needed to run.

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\build.ps1 -Platform x86 -Test
```

This restores pinned, SHA-256-verified NuGet packages into `desktop/.packages/`,
compiles against the .NET Framework 4.8 reference assemblies, and runs the tests.
It uses the Windows Framework C# compiler, so a modern .NET SDK or Visual Studio
installation is optional. Network access to `api.nuget.org` is needed on the
first build. It does not download or install SAP software.

Output: `EtapCrystalReporter/bin/x86/Release/EtapCrystalReporter.exe`.
Deploy the executable **with** its `.exe.config`, `System.Data.SQLite.dll`,
`e_sqlite3.dll`, and `Templates` folder. The receiving machine also needs the SAP
runtime; copying SAP DLLs from another machine is not a runtime installation.

For a 64-bit SAP runtime, build with `-Platform x64` and run that executable
directly. `Launch.cmd` intentionally selects the tested x86 build.

Visual Studio users can run `build.ps1 -RestoreOnly`, then open
`EtapCrystalReporter.sln` with the .NET Framework 4.8 targeting pack installed.
Choose x86 or x64 explicitly; Any CPU is not supported.

Dependencies: [System.Data.SQLite 2.0.4](https://www.nuget.org/packages/System.Data.SQLite/2.0.4),
[SourceGear.sqlite3 3.53.4](https://www.nuget.org/packages/SourceGear.sqlite3/3.53.4),
and [Microsoft.NETFramework.ReferenceAssemblies.net48 1.0.3](https://www.nuget.org/packages/Microsoft.NETFramework.ReferenceAssemblies.net48/1.0.3).
SQLite 2.x needs a separate native `e_sqlite3.dll`; the build includes the matching
architecture from SourceGear's package.

## Data preservation and traceability

- The original study is opened only as a read-only file stream, with writers
  excluded while copying and hashing. SQLite opens only a private temporary copy,
  in read-only/query-only mode. The source is never attached to Crystal.
- The SQLite header and `quick_check` must pass. Nonempty WAL or rollback-journal
  sidecars are rejected: close ETAP and supply a completed/checkpointed result.
  The application does not checkpoint or repair the original.
- Schema enumeration and `ISCStudyCase.StudyType` determine the displayed study;
  missing, invalid, conflicting, or unknown metadata is reported without guessing
  from the filename. StudyType 5 keeps the combined 30-cycle/minimum-fault label.
- All rows, including ETAP's report-spacing markers, are preserved. The companion
  does **not** reuse the web importer's spacer removal or derived tables.
- All main-report and subreport tables are rebound. Required field types are
  checked, NULL values are retained, and lossy integer conversions are rejected.
  Additional database columns remain available because some supplied templates
  contain formulas referencing columns absent from their saved field lists.
- The original `.rpt` is opened using `OpenReportByTempCopy`, and never saved.
  Refresh in the viewer rebuilds the report from a new protected study snapshot,
  instead of reconnecting to the template's original database path.
- PDFs are rendered to a temporary file in the output folder, checked for a PDF
  header, and moved into place without overwriting existing reports. Failed
  exports remove their partial file.

Settings and newline-delimited JSON logs are under
`%LOCALAPPDATA%\ETAP Lens\CrystalReporter`. **Open logs** opens that directory.
Logs include source/template paths and SHA-256 hashes, detected study metadata,
available tables, bound aliases, row counts, runtime version, errors, and output
PDF hashes. Engineering row values are not logged. Preview toolbar printing is
handled by SAP; app-managed PDF exports are logged.

## Optional template configuration

An adjacent file named `My Report.rpt.json` may declare:

```json
{
  "StudyTypes": [3, 4, 5],
  "TableMappings": {
    "BusAlias": "IBus",
    "SubreportName.rpt/OtherAlias": "IBus"
  },
  "Parameters": {
    "Title": "Short-circuit results",
    "SubreportName.rpt/ShowDetails": true
  }
}
```

Use only actual aliases and parameter names from your template. The example is
illustrative, not required by the supplied templates. The ten initial duty
templates have no parameters. Empty `StudyTypes` means no explicit restriction;
the table/column check still applies. Without a mapping, the binder tries the
Crystal alias and then the final component of its table location, ignoring case.

Scalar string/number/boolean/date parameters are supported in sidecars. Use
ISO dates. Interactive templates can prompt through the native viewer; direct
or batch export of unresolved parameters fails with a diagnostic. SQL Commands,
stored procedures, range/multi-value parameter sidecars, password-protected
templates, and server-based report services are not automatically translated.
No report query is executed against the source file.

## Architecture and validation

`UI/` handles forms and worker dispatch; `Services/` owns SQLite inspection,
study detection, template discovery, Crystal binding, export, and batching;
`Models/` holds job/result contracts; `Utilities/` handles logging, settings,
file protection, and STA workers. Crystal calls use the installed SAP SDK through
a small late-bound adapter; there is no mock reporting engine in the application.
The viewer and its documents stay on the UI STA thread. Noninteractive exports
run on an STA worker, keeping the main window responsive. Large preview loads
can temporarily occupy the viewer thread.

`build.ps1 -Test` runs 17 independent checks covering study metadata, SQLite
validation, source hashes, read-only inputs, spacer preservation, live sidecars,
template discovery, schema mappings, type conversion, additional formula fields,
collision handling, partial-file cleanup, batch continuation, and cancellation.
The unit-test report writer is explicitly a test double; these tests alone do
not validate SAP rendering.

The diagnostic test executable also accepts these real-SDK checks:

```powershell
$test = '.\desktop\EtapCrystalReporter\bin\x86\Release\EtapCrystalReporter.Tests.exe'
& $test --inspect 'C:\Studies'
& $test --templates 'C:\Templates'
& $test --report 'C:\Studies\Study.SA2S' 'C:\Templates\Summary.rpt' 'C:\Reports'
```

The four supplied ETAP 24.0.1N studies have been inspected and exported with the
installed SAP runtime: **37 of 37 native exports succeeded**, and 36 displayed
current values were cross-checked against the source databases. A separate STA
batch test rejected one incompatible pairing and then exported the other three
successfully. Native preview and its refresh/rebinding path have also been
exercised. See [VALIDATION.md](VALIDATION.md) for the scope of these checks. Report rendering
retains the template's formatting, including intentional whitespace and page
breaks. Engineering sign-off still requires comparison with the corresponding
ETAP-generated report; such baseline PDFs were not supplied.

The initial release exports PDF. Optional Excel/Word UI export and additional
study-file extensions are not implemented. The supplied specification stopped
at the heading of section 14, so later requirements were not assumed.

SDK references: [SAP data-source binding](https://help.sap.com/docs/SAP_CRYSTAL_REPORTS%2C_DEVELOPER_VERSION_FOR_MICROSOFT_VISUAL_STUDIO/0d6684e153174710b8b2eb114bb7f843/45c46b3f6e041014910aba7db0e91070.html)
and [native viewer binding](https://help.sap.com/docs/SAP_CRYSTAL_REPORTS%2C_DEVELOPER_VERSION_FOR_MICROSOFT_VISUAL_STUDIO/0d6684e153174710b8b2eb114bb7f843/45b0db796e041014910aba7db0e91070.html).
