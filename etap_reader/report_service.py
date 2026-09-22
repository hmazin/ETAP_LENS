"""Session-owned, durable report jobs. Workers claim jobs with renewable leases."""
import copy
import hashlib
import json
import os
import re
import time
import uuid

from . import project_cache, report_sources, report_headers

with open(os.path.join(os.path.dirname(__file__), "report_catalog.json"), encoding="utf-8") as stream:
    CATALOG = json.load(stream)
TEMPLATES = {t["id"]: t for t in CATALOG}
HEX = re.compile(r"^[a-f0-9]{32}$")
HASH = re.compile(r"^[a-f0-9]{64}$")
LEASE_SECONDS = 180
RETENTION_SECONDS = 7 * 86400
MAX_PDF_BYTES = 100 * 1024 * 1024


class ReportError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def public_job(job):
    return {key: value for key, value in job.items() if not key.startswith("_")}


class ReportService:
    def __init__(self, objects, records):
        self.objects = objects
        self.records = records

    def availability(self, require_headers=False):
        templates = {}
        for key in self.records.keys("workers/"):
            worker, _ = self.records.read(key)
            if worker.get("seen", 0) > time.time() - 90 and (not require_headers or worker.get("header_version", 0) >= 1):
                templates.update(worker.get("templates", {}))
        return templates

    def studies(self, session, scope):
        result = []
        for public in project_cache.list_projects(scope):
            if public.get("category_set") not in ("sc_duty", "sc_fault", "load_flow_unbalanced"):
                continue
            manifest = project_cache.get_manifest(public["project_id"], scope)
            source = manifest.get("report_source") or {}
            supported = source.get("study_type") in report_sources.STUDIES
            available = bool(source and self.objects.exists(source["key"]))
            result.append({"project_id": public["project_id"],
                           "filename": public.get("display_name") or public["db_name"],
                           "study_type": source.get("study_type"),
                           "study_name": source.get("study_name", "Study type unavailable"),
                           "table_count": source.get("table_count"), "sha256": source.get("sha256"),
                           "ready": available and supported,
                           "reason": "" if available and supported else manifest.get("report_error") or
                           ("This study type is not supported yet." if available else
                            "Reload this study to retain its original file for reporting.")})
        return result

    @staticmethod
    def _expire(state):
        now = time.time()
        state["jobs"] = [j for j in state.get("jobs", []) if j["created_at"] > now - RETENTION_SECONDS]
        for job in state["jobs"]:
            if job["status"] == "generating" and job.get("_lease_until", 0) < now:
                job["status"] = "queued" if job["attempts"] < 2 else "failed"
                job["message"] = "Worker connection was lost; queued again." if job["status"] == "queued" else "The reporting service stopped responding. You can retry this report."
                job.pop("_lease", None)

    def jobs(self, session):
        def read(state):
            self._expire(state)
            return [public_job(j) for j in reversed(state["jobs"])]
        return self.records.update("sessions/" + session, read)

    def create(self, session, scope, body):
        request_id = body.get("request_id", "")
        selections = body.get("jobs")
        if not isinstance(request_id, str) or not HEX.fullmatch(request_id):
            raise ReportError("A valid request_id is required.")
        if not isinstance(selections, list) or not 1 <= len(selections) <= 24:
            raise ReportError("Select between 1 and 24 reports per batch.")
        if not isinstance(body.get("timestamp", False), bool):
            raise ReportError("timestamp must be true or false.")
        try:
            headers = report_headers.validate(body.get("headers", {}))
        except ValueError as error:
            raise ReportError(str(error)) from error
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        # An idempotent retry must succeed even if the worker has since gone offline.
        previous, _ = self.records.read("sessions/" + session)
        existing = [j for j in previous.get("jobs", []) if j.get("_request") == request_id]
        if existing:
            if existing[0]["_request_hash"] != fingerprint:
                raise ReportError("This request id has already been used for a different batch.", 409)
            return [public_job(j) for j in existing]
        available = self.availability(require_headers=bool(headers))
        if not available:
            if headers and self.availability():
                raise ReportError("The reporting worker needs an update before it can customize headers.", 503)
            raise ReportError("The reporting service is offline. Please try again when it reconnects.", 503)
        prepared = []
        seen = set()
        batch_id = uuid.uuid4().hex
        for selection in selections:
            if not isinstance(selection, dict):
                raise ReportError("Each report must identify a study and template.")
            pid, tid = selection.get("project_id"), selection.get("template_id")
            if not isinstance(pid, str) or not re.fullmatch(r"[a-f0-9]{16}", pid) or not isinstance(tid, str):
                raise ReportError("Invalid study or template.")
            if (pid, tid) in seen:
                continue
            seen.add((pid, tid))
            manifest = project_cache.get_manifest(pid, scope)
            if not manifest:
                raise ReportError("Study not found. Reload it and try again.", 404)
            source = manifest.get("report_source")
            template = TEMPLATES.get(tid)
            if not source or not self.objects.exists(source["key"]):
                raise ReportError("The original study has expired or is unavailable. Reload it first.", 409)
            if selection.get("source_sha256") and selection["source_sha256"] != source["sha256"]:
                raise ReportError("The study has changed since this report was selected. Select the current study again.", 409)
            if not template or source["study_type"] not in template["study_types"]:
                raise ReportError("The selected template does not support this study type.")
            if tid not in available:
                raise ReportError("This template is not available on the reporting service.", 503)
            prepared.append({"id": uuid.uuid4().hex, "batch_id": batch_id, "project_id": pid,
                             "filename": manifest.get("display_name") or manifest["db_name"],
                             "template_id": tid, "template_name": template["name"], "family": template["family"],
                             "study_type": source["study_type"], "study_name": source["study_name"],
                             "source_sha256": source["sha256"], "template_sha256": available[tid]["sha256"],
                             "created_at": time.time(), "status": "queued", "message": "Waiting for the reporting service.",
                             "attempts": 0, "timestamp": body.get("timestamp", False),
                             "headers": headers,
                             "_source": source, "_template": available[tid],
                             "_request": request_id, "_request_hash": fingerprint})

        def save(state):
            self._expire(state)
            duplicate = [j for j in state["jobs"] if j.get("_request") == request_id]
            if duplicate:
                if duplicate[0]["_request_hash"] != fingerprint:
                    raise ReportError("This request id has already been used.", 409)
                return [public_job(j) for j in duplicate]
            if len(state["jobs"]) + len(prepared) > 100 or sum(j["status"] in ("queued", "generating") for j in state["jobs"]) + len(prepared) > 24:
                raise ReportError("Report limit reached: 24 pending reports and 100 reports per seven days.", 429)
            state["jobs"].extend(prepared)
            return [public_job(j) for j in prepared]
        return self.records.update("sessions/" + session, save)

    def worker_seen(self, worker_id, inventory, header_version=0):
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", worker_id):
            raise ReportError("Invalid worker id.")
        if not isinstance(inventory, dict) or len(inventory) > len(CATALOG):
            raise ReportError("Invalid template inventory.")
        if type(header_version) is not int or header_version not in (0, 1):
            raise ReportError("Invalid worker header version.")
        for tid, item in inventory.items():
            if tid not in TEMPLATES or not isinstance(item, dict) or not HASH.fullmatch(str(item.get("sha256", ""))):
                raise ReportError("Invalid template inventory.")
            if item.get("options_sha256") and not HASH.fullmatch(str(item["options_sha256"])):
                raise ReportError("Invalid template options hash.")
        self.records.update("workers/" + worker_id, lambda state: state.update(seen=time.time(), templates=inventory, header_version=header_version))

    def claim(self, worker_id, inventory, header_version=0):
        self.worker_seen(worker_id, inventory, header_version)
        for key in self.records.keys("sessions/"):
            def take(state):
                self._expire(state)
                for job in state["jobs"]:
                    if job["status"] != "queued" or inventory.get(job["template_id"]) != job["_template"]:
                        continue
                    if job.get("headers") and header_version < 1:
                        continue
                    job.update(status="generating", message="Generating PDF from the original ETAP template.",
                               attempts=job["attempts"] + 1, _lease=uuid.uuid4().hex,
                               _lease_until=time.time() + LEASE_SECONDS, _worker=worker_id)
                    job["_output"] = f"reports/pdfs/{key.split('/')[1]}/{job['id']}/{job['_lease']}.pdf"
                    return copy.deepcopy(job)
            job = self.records.update(key, take)
            if job:
                job["session"] = key.split("/")[1]
                job["template"] = TEMPLATES[job["template_id"]]
                return job
        return None

    def lease(self, session, job_id, token, change=None):
        def update(state):
            for job in state.get("jobs", []):
                if job["id"] == job_id and job.get("_lease") == token and job["status"] == "generating" and job.get("_lease_until", 0) > time.time():
                    if change:
                        change(job)
                    return copy.deepcopy(job)
            raise ReportError("The report lease has expired or was replaced.", 409)
        return self.records.update("sessions/" + session, update)

    def heartbeat(self, session, job_id, token):
        job = self.lease(session, job_id, token, lambda j: j.update(_lease_until=time.time() + LEASE_SECONDS))
        self.records.update("workers/" + job["_worker"], lambda state: state.update(seen=time.time()))

    def get(self, session, job_id):
        state, _ = self.records.read("sessions/" + session)
        for job in state.get("jobs", []):
            if job["id"] == job_id and job["created_at"] > time.time() - RETENTION_SECONDS:
                return job
        raise ReportError("Report not found or expired.", 404)
