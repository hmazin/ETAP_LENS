# Verification record

Validated on 2026-09-16 using .NET Framework 4.8, the installed 32-bit SAP Crystal
Reports runtime 13.0.26.3348, and ETAP 24.0.1N study files and original templates
provided by the user. Client files, template binaries, diagnostic logs, images,
and generated PDFs are excluded from version control.

## Source inspection

| Sample | Internal StudyType | SQLite tables | Detected study |
| --- | --- | --- | --- |
| Device duty `.SA1S` | 1 | 45 | Device Duty |
| Half-cycle `.SA2S` | 3 | 55 | ANSI Half-Cycle / Momentary |
| Interrupting `.SA2S` | 4 | 55 | ANSI 1.5-4 Cycle |
| Minimum-fault `.SA2S` | 5 | 55 | ANSI 30-Cycle / Minimum-Fault |

SQLite integrity checks passed. SHA-256 comparisons confirmed all four original
study files and all 35 locally copied `.rpt` templates were unchanged after
testing. The supplied full Formats directory contains 1,399 `.rpt` files;
enumeration was verified, but rendering was tested only for the families below.

## Native SDK exports

| Family and source | Reports tested | Result |
| --- | --- | --- |
| ANSI 3-Phase SC / duty study | Complete; combined Summary; momentary and interrupting Complete, Exceeded, Marginal, MVA | 10/10 passed |
| ANSI Unbalanced SC / half-cycle study | Summary; four Complete variants; four fault-current result variants | 9/9 passed |
| ANSI Unbalanced SC / interrupting study | Same nine variants | 9/9 passed |
| ANSI Unbalanced SC / minimum-fault study | Same nine variants | 9/9 passed |

All 37 PDFs reopened successfully with `pypdf`, contained at least one page, and
had 9,369 pages in total. These are native Crystal exports, not manually recreated
reports. Representative rendered pages were visually inspected using Poppler,
including momentary duty, minimum-fault summary, an exceeded report, and a
line-to-ground detail page. This is a representative visual inspection, not a
page-by-page audit of every generated page.

For three selected buses in each of the four studies, three current values were
compared between the SQLite records and the PDF's corresponding text line: all
36 matched to the report's three-decimal precision. The supplied duty study has
zero exceeded flags; the Exceeded templates therefore correctly contain headers
without result rows. Highlight rendering for a positive exceeded/marginal case
has not been demonstrated using these samples.

The unbalanced Complete template initially failed a formula referencing
`Isummary.Inverter`, which exists in the data but not in its saved field list.
Preserving additional source columns fixed that binding failure without editing
the template. A regression check covers preservation of these extra fields.

## UI and batch

- The WinForms window and native Crystal viewer were instantiated and rendered.
- Preview loaded a real duty report; programmatic viewer refresh triggered a new
  protected database snapshot and rebound the report, rather than reconnecting
  to the template's saved connection.
- A real batch on the same STA worker failed an intentionally incompatible first
  pairing, then successfully exported the remaining three studies.
- Large template-directory scans run in the background.
- The build compiled with warnings treated as errors; all 17 automated checks
  passed. `git diff --check` passed.

## Limits

Corresponding ETAP-generated baseline PDFs were not provided. Exact page/content
parity with an ETAP-generated baseline remains an acceptance check for engineering
use. Other runtime versions, ETAP releases, template families, encrypted files,
SQL Command templates, and complex parameter combinations are not covered by this
sample validation. The application does not calculate or reinterpret fault results.

To reproduce with your own licensed inputs, see the diagnostic commands and
dependency requirements in [README.md](README.md). Local outputs from this run are
under the repository's ignored `output/pdf/` directory.
