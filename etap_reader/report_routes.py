"""Browser and authenticated outbound-worker endpoints for Crystal reports."""
import functools
import hmac
import os
import tempfile
import time
import zipfile
import re
import sqlite3
from pathlib import Path

from flask import jsonify, request, send_file
from werkzeug.utils import secure_filename

from . import appconfig, project_cache, report_sources, sessions, report_headers
from .report_service import CATALOG, HASH, LEASE_SECONDS, MAX_PDF_BYTES, ReportError, ReportService, public_job
from .report_store import RecordStore


def register(app, objects, current_session, scoped_session):
    service = ReportService(objects, RecordStore(objects, project_cache.CACHE_DIR))

    def enabled():
        return appconfig.REPORTS_ENABLED and len(appconfig.REPORT_WORKER_TOKEN) >= 32

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ReportError("A JSON object is required.")
        return value

    def browser(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            sid, error = current_session()
            if error:
                return error
            return view(sid, *args, **kwargs)
        return wrapped

    def worker(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if not enabled() or not hmac.compare_digest(token, appconfig.REPORT_WORKER_TOKEN):
                return jsonify(error="Worker authentication required."), 401
            return view(*args, **kwargs)
        return wrapped

    def lease_args():
        data = body()
        sid, jid, token = data.get("session"), data.get("id"), data.get("lease")
        if not isinstance(sid, str) or not (sessions.is_valid(sid) or sid == sessions.SHARED):
            raise ReportError("Invalid session.")
        if not isinstance(jid, str) or not isinstance(token, str):
            raise ReportError("Invalid report lease.")
        return sid, jid, token

    def downloaded(key):
        fd, path = tempfile.mkstemp(prefix="etap-report-", suffix=".pdf")
        os.close(fd)
        try:
            objects.download_to(key, path)
        except Exception:
            os.remove(path)
            raise
        return path

    def file_response(path, filename, mimetype, attachment=True):
        response = send_file(path, mimetype=mimetype, as_attachment=attachment, download_name=filename)
        response.direct_passthrough = False
        # Gunicorn streams this body with chunked transfer when no length is
        # set, avoiding Cloud Run's 32 MiB non-streaming response ceiling.
        response.headers.pop("Content-Length", None)
        response.automatically_set_content_length = False
        response.call_on_close(lambda: os.remove(path) if os.path.exists(path) else None)
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.errorhandler(ReportError)
    def report_error(error):
        return jsonify(error=str(error)), error.status

    @app.after_request
    def private_reports(response):
        if request.path.startswith("/api/reports") or request.path.startswith("/api/report-worker"):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/reports/catalog")
    @browser
    def catalog(sid):
        available = service.availability() if enabled() else {}
        return jsonify(enabled=enabled(), online=bool(available), retention_days=7,
                       header_fields=report_headers.public_fields(),
                       headers_available=bool(service.availability(require_headers=True)) if enabled() else False,
                       message="" if available else "The reporting service is offline. Your studies remain available to browse.",
                       templates=[{**{k: v for k, v in t.items() if k != "path"}, "available": t["id"] in available} for t in CATALOG],
                       studies=service.studies(sid, scoped_session()))

    @app.get("/api/reports/studies/<project_id>/headers")
    @browser
    def study_headers(sid, project_id):
        if not re.fullmatch(r"[a-f0-9]{16}", project_id):
            raise ReportError("Study not found.", 404)
        manifest = project_cache.get_manifest(project_id, scoped_session())
        if not manifest or not manifest.get("report_source"):
            raise ReportError("Study not found. Reload it and try again.", 404)
        source = manifest["report_source"]
        if not objects.exists(source["key"]):
            raise ReportError("The original study has expired. Reload it first.", 410)
        values = source.get("headers")
        if values is None:
            # Earlier uploads predate header metadata. Read their original copy.
            with tempfile.TemporaryDirectory(prefix="etap-header-") as directory:
                path = Path(directory) / "study.sqlite"
                objects.download_to(source["key"], str(path))
                conn = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
                try:
                    conn.execute("PRAGMA trusted_schema=OFF")
                    deadline = time.monotonic() + 15
                    conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
                    values = report_headers.read_values(conn)
                finally:
                    conn.close()
        return jsonify(values=values, source_sha256=source["sha256"])

    @app.route("/api/reports/jobs", methods=["GET", "POST"])
    @browser
    def jobs(sid):
        if request.method == "GET":
            return jsonify(jobs=service.jobs(sid))
        if not enabled():
            raise ReportError("Reporting is not enabled on this server yet.", 503)
        return jsonify(jobs=service.create(sid, scoped_session(), body())), 202

    @app.get("/api/reports/jobs/<job_id>/pdf")
    @browser
    def pdf(sid, job_id):
        job = service.get(sid, job_id)
        if job["status"] != "ready":
            raise ReportError("This PDF is not ready yet.", 409)
        if not objects.exists(job["_output"]):
            raise ReportError("This PDF has expired. Generate it again.", 410)
        return file_response(downloaded(job["_output"]), job["output_name"], "application/pdf",
                             attachment=request.args.get("download") == "1")

    @app.get("/api/reports/jobs/<job_id>/link")
    @browser
    def pdf_link(sid, job_id):
        job = service.get(sid, job_id)
        if job["status"] != "ready":
            raise ReportError("This PDF is not ready yet.", 409)
        if not objects.exists(job["_output"]):
            raise ReportError("This PDF has expired. Generate it again.", 410)
        if objects.kind == "local":
            return jsonify(direct=True)
        disposition = "attachment" if request.args.get("download") == "1" else "inline"
        return jsonify(url=objects.signed_download_url(job["_output"],
                       disposition=f'{disposition}; filename="{job["output_name"]}"'))

    @app.post("/api/reports/download-zip")
    @browser
    def download_zip(sid):
        ids = body().get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 24 or not all(isinstance(i, str) for i in ids):
            raise ReportError("Select between 1 and 24 completed reports.")
        selected = [service.get(sid, jid) for jid in dict.fromkeys(ids)]
        if any(j["status"] != "ready" for j in selected):
            raise ReportError("Only completed reports can be downloaded.", 409)
        if sum(j.get("pdf_bytes", 0) for j in selected) > 250 * 1024 * 1024:
            raise ReportError("This download exceeds 250 MB. Download fewer reports at a time.")
        fd, path = tempfile.mkstemp(prefix="etap-reports-", suffix=".zip")
        os.close(fd)
        try:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
                for index, job in enumerate(selected, 1):
                    if not objects.exists(job["_output"]):
                        raise ReportError("A selected PDF has expired. Generate it again.", 410)
                    pdf_path = downloaded(job["_output"])
                    try:
                        archive.write(pdf_path, f"{index:02d}_{job['output_name']}")
                    finally:
                        os.remove(pdf_path)
            return file_response(path, "ETAP_Reports.zip", "application/zip")
        except Exception:
            os.remove(path)
            raise

    @app.post("/api/report-worker/claim")
    @worker
    def claim():
        data = body()
        job = service.claim(data.get("worker_id"), data.get("templates"), data.get("header_version", 0))
        if not job:
            return jsonify(job=None)
        return jsonify(job={"id": job["id"], "session": job["session"], "lease": job["_lease"],
                            "source_sha256": job["source_sha256"], "study_type": job["study_type"],
                            "template": job["template"], "template_hashes": job["_template"],
                            "headers": job.get("headers", {}),
                            "source_bytes": job["_source"]["bytes"]})

    @app.post("/api/report-worker/source")
    @worker
    def source():
        job = service.lease(*lease_args())
        if not objects.exists(job["_source"]["key"]):
            raise ReportError("The original study has expired. Reload the study.", 410)
        if objects.kind == "gcs":
            return jsonify(url=objects.signed_download_url(job["_source"]["key"]))
        return file_response(downloaded(job["_source"]["key"]), "study.sqlite", "application/octet-stream")

    @app.post("/api/report-worker/heartbeat")
    @worker
    def heartbeat():
        service.heartbeat(*lease_args())
        return jsonify(ok=True)

    @app.post("/api/report-worker/upload")
    @worker
    def upload():
        job = service.lease(*lease_args())
        if objects.kind != "gcs":
            return jsonify(direct=True)
        return jsonify(upload=objects.signed_upload_url(job["_output"], content_type="application/pdf", max_bytes=MAX_PDF_BYTES))

    @app.post("/api/report-worker/upload-direct")
    @worker
    def upload_direct():
        if objects.kind != "local":
            raise ReportError("Use the signed storage upload.")
        sid, jid, token = request.headers.get("X-Report-Session"), request.headers.get("X-Report-Id"), request.headers.get("X-Report-Lease")
        if not sid or not (sessions.is_valid(sid) or sid == sessions.SHARED):
            raise ReportError("Invalid session.")
        job = service.lease(sid, jid, token)
        if request.content_length is None or request.content_length > MAX_PDF_BYTES:
            raise ReportError("PDF exceeds the 100 MB report limit.", 413)
        with tempfile.TemporaryDirectory(prefix="etap-output-") as directory:
            path = os.path.join(directory, "report.pdf")
            with open(path, "wb") as stream:
                # Bounded even if a client lies about Content-Length.
                count = 0
                while chunk := request.stream.read(1024 * 1024):
                    count += len(chunk)
                    if count > MAX_PDF_BYTES:
                        raise ReportError("PDF exceeds the report limit.", 413)
                    stream.write(chunk)
            objects.upload_from(path, job["_output"])
        return jsonify(ok=True)

    @app.post("/api/report-worker/complete")
    @worker
    def complete():
        args = lease_args()
        job = service.lease(*args)
        digest = body().get("pdf_sha256", "")
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ReportError("A PDF checksum is required.")
        if not objects.exists(job["_output"]) or not 5 < objects.size(job["_output"]) <= MAX_PDF_BYTES:
            raise ReportError("The uploaded PDF is missing or exceeds the report limit.")
        path = downloaded(job["_output"])
        try:
            with open(path, "rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ReportError("The report is not a PDF.")
            if report_sources.sha256(path) != digest:
                raise ReportError("The PDF checksum does not match.")
            size = os.path.getsize(path)
        finally:
            os.remove(path)
        stem = secure_filename(os.path.splitext(job["filename"])[0])[:90] or "Study"
        name = secure_filename(job["template_name"]) or "Report"
        timestamp = time.strftime("_%Y%m%d-%H%M%S", time.gmtime(job["created_at"])) if job["timestamp"] else ""
        finished = service.lease(*args, change=lambda j: j.update(status="ready", message="PDF ready.",
                                 finished_at=time.time(), pdf_sha256=digest, pdf_bytes=size,
                                 output_name=f"{stem}_{name}{timestamp}.pdf"))
        return jsonify(job=public_job(finished))

    @app.post("/api/report-worker/fail")
    @worker
    def fail():
        service.lease(*lease_args(), change=lambda j: j.update(status="failed", finished_at=time.time(),
                      message="The report could not be generated. Retry it, or ask the operator to check the worker log using this report ID."))
        return jsonify(ok=True)

    return service
