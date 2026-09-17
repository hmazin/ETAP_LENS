# Crystal reporting in ETAP Lens

The Reports workspace generates native Crystal PDFs from the original ETAP
result file. It offers single reports, compatible batch combinations, durable
history, retries, PDF preview (pages, zoom, search), individual downloads and ZIP.
Table-level CSV/Excel exports remain separate.

## Components

- Vercel serves `web/`, including a self-hosted PDF.js viewer.
- Flask validates session ownership and creates durable report jobs.
- GCS stores original sources, PDFs and job state. Job updates use generation
  preconditions; local mode uses SQLite transactions instead.
- A Windows worker polls the API over HTTPS. No inbound port is required.
- Each report runs in a separate STA .NET Framework process with the installed
  Crystal runtime. A crash or timeout cannot poison the next report.

The first catalog covers the supplied ETAP 24 ANSI study types 1, 3, 4 and 5.
Type 1 uses ANSI 3-Phase SC templates; types 3/4/5 use ANSI Unbalanced SC.
The catalog has 15 templates. It does not claim support for all ETAP modules or
every template in Formats2400. Matching uses database metadata, never filenames.
Crystal validates every required table and field during binding.

## API deployment

Deploy `deploy/cloudbuild.yaml` using the existing Cloud Run project. The pipeline
preserves separately configured environment values and secrets on later deploys.

1. Store a random token of at least 32 characters in Secret Manager, for example
   `etap-lens-report-worker`. Grant the existing API runtime service account
   secret-accessor permission on this secret only.
2. Attach it as `ETAP_LENS_REPORT_WORKER_TOKEN` and set
   `ETAP_LENS_REPORTS_ENABLED=true` on Cloud Run.
3. Apply `deploy/bucket-lifecycle.json`. Source files and PDFs expire after seven
   days; inactive state objects expire after eight. Existing upload/cache rules
   are preserved. `deploy/bucket-cors.json` already allows GET for signed previews.
4. Deploy the frontend through the connected GitHub/Vercel branch.

The UI remains usable with reporting disabled or the worker offline. Previously
loaded studies without a retained original must be uploaded again. The browsing
SQLite cache is never used to generate a Crystal report.

## This Windows computer as a trial worker

Install Python 3.10+, .NET Framework 4.8, and the matching Crystal runtime. The
worker supervisor uses only Python's standard library. Build the engine:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1 -Platform x86 -Test
```

Keep the licensed report files outside Git. By default, the worker reads
`desktop/EtapCrystalReporter/Templates`, preserving paths in
`etap_reader/report_catalog.json`. Optional `.rpt.json` parameter/mapping sidecars
work as in the desktop companion. The worker advertises only installed templates.

Save the API connection (the script prompts privately for its token):

```powershell
.\desktop\Configure-ReportWorker.ps1 -ApiUrl https://YOUR-API.run.app
.\desktop\Start-ReportWorker.ps1
```

The token is encrypted using Windows DPAPI for the current user and machine,
under ignored `cache/`. It is passed to the child process through its environment,
never in command-line arguments. The worker starts hidden and logs to
`cache/report-worker-logs/worker.log`. It runs until stopped or the PC restarts;
this trial setup does not install a Windows service or an automatic startup task.
Restart it with the same Start script after reboot.

```powershell
.\desktop\Stop-ReportWorker.ps1
```

Use a dedicated Windows service account and always-on host for a production
worker. For the trial, the PC must be awake and connected. The website reports
offline status when heartbeats stop.

## Job behavior and data boundaries

- Jobs belong to the browser's existing anonymous bearer session. This is not a
  user account; clearing site data loses access. No client data or templates go
  into the public Git repository.
- Limits: 24 reports per batch/pending queue, 100 reports per session per seven
  days, 100 MB per PDF, 250 MB per ZIP. These complement existing upload limits.
- A lease lasts three minutes and renews every 25 seconds. One abandoned job is
  automatically retried once. Lease tokens fence late workers from publishing.
- The supervisor enforces a ten-minute rendering timeout per report.
- Each job records source, template and PDF SHA-256. Template sidecar hashes are
  also pinned. Modified source selections are rejected instead of silently
  producing a different report on retry.
- Duplicate submission IDs return the original batch. One failed report does
  not stop the others. Downloads and ZIPs check ownership server-side.
- GCS URLs are signed for 15 minutes. Original files are shared only with the
  authenticated worker; PDFs are shared only with their owning browser session.
- Native engine diagnostics stay on the worker, keyed by report ID. Public error
  messages do not expose server paths or credentials.

## Validation

```powershell
python -m unittest discover -s tests -v
node --check web/reports.js
node --check web/pdf-preview.mjs
```

`tests/test_reports.py` covers source preservation, session isolation,
compatibility, idempotency, concurrent claims, lease recovery, downloads, ZIPs,
failure isolation and quotas. Native Crystal exports require the installed SDK
and privately held study/template files; synthetic API tests do not replace them.
