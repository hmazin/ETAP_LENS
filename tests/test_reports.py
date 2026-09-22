import concurrent.futures
import hashlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

from flask import Flask, jsonify, request

from etap_reader import appconfig, project_cache, report_routes, report_sources, sessions
from etap_reader.report_service import CATALOG, ReportService
from etap_reader.report_store import RecordStore
from etap_reader.storage import LocalStorage

SID = "a" * 32
OTHER = "b" * 32
PID = "1" * 16
TOKEN = "test-only-worker-token-" + "c" * 32


class ReportFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.objects = LocalStorage(os.path.join(self.temp.name, "objects"))
        self.source = os.path.join(self.temp.name, "renamed.SA2S")
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE ISCStudyCase (StudyType INTEGER)")
            db.execute("INSERT INTO ISCStudyCase VALUES (1)")
            db.execute("CREATE TABLE IBus (Name TEXT, Fault REAL)")
            db.executemany("INSERT INTO IBus VALUES (?, ?)", [("Bus A", 1.234), (None, None)])
            db.execute("CREATE TABLE Headr (SN TEXT, Date TEXT, Project TEXT, PSRev TEXT)")
            db.execute("INSERT INTO Headr VALUES ('Original SN', 'Original date', 'Original project', '24.0')")
        db.close()
        self.retained = report_sources.preserve(self.source, SID, self.objects)
        self.manifest = {"project_id": PID, "session_id": SID, "display_name": "renamed.SA2S",
                         "db_name": "renamed", "category_set": "sc_duty", "report_source": self.retained}
        for target, value in [("CACHE_DIR", self.temp.name)]:
            self.enterContext(patch.object(project_cache, target, value))
        self.enterContext(patch.object(appconfig, "REPORTS_ENABLED", True))
        self.enterContext(patch.object(appconfig, "REPORT_WORKER_TOKEN", TOKEN))
        self.enterContext(patch.object(project_cache, "get_manifest", side_effect=lambda pid, sid: self.manifest if pid == PID and sid == SID else None))
        self.enterContext(patch.object(project_cache, "list_projects", side_effect=lambda sid: [project_cache._public_manifest(self.manifest)] if sid == SID else []))
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True

        def current():
            sid, error = sessions.from_request(request, True)
            return sid, (jsonify(error=error), 400) if error else None
        self.service = report_routes.register(self.app, self.objects, current, lambda: request.headers.get("X-Session-Id"))
        self.client = self.app.test_client()
        self.headers = {"X-Session-Id": SID}
        self.worker_headers = {"Authorization": "Bearer " + TOKEN}
        self.inventory = {t["id"]: {"sha256": "d" * 64, "options_sha256": ""} for t in CATALOG}
        self.service.worker_seen("test-worker", self.inventory, header_version=1)

    def create(self, **changes):
        data = {"request_id": uuid.uuid4().hex, "jobs": [{"project_id": PID, "template_id": "duty-summary", "source_sha256": self.retained["sha256"]}]}
        data.update(changes)
        response = self.client.post("/api/reports/jobs", json=data, headers=self.headers)
        return response, data

    def claim(self):
        response = self.client.post("/api/report-worker/claim", headers=self.worker_headers,
                                    json={"worker_id": "test-worker", "templates": self.inventory, "header_version": 1})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["job"]

    @staticmethod
    def lease(job):
        return {k: job[k] for k in ("session", "id", "lease")}

    def finish(self, job):
        pdf = b"%PDF-1.7\nTest report bytes\n%%EOF"
        response = self.client.post("/api/report-worker/upload-direct", data=pdf,
                    headers={**self.worker_headers, "X-Report-Session": SID, "X-Report-Id": job["id"], "X-Report-Lease": job["lease"]}, content_type="application/pdf")
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/report-worker/complete", headers=self.worker_headers,
                                   json={**self.lease(job), "pdf_sha256": hashlib.sha256(pdf).hexdigest()})
        self.assertEqual(response.status_code, 200, response.json)
        return pdf

    def test_source_preserves_bytes_and_spacer_rows_and_uses_metadata(self):
        self.assertEqual(self.retained["study_type"], 1)  # Filename says SA2S; content says duty.
        self.assertEqual(report_sources.sha256(self.source), self.retained["sha256"])
        copy = self.objects._path(self.retained["key"])
        self.assertEqual(Path(copy).read_bytes(), Path(self.source).read_bytes())
        with sqlite3.connect(copy) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM IBus").fetchone()[0], 2)
        db.close()
        self.assertNotIn("report_source", project_cache._public_manifest(self.manifest))

    def test_invalid_and_active_databases_are_rejected(self):
        Path(self.source + "-wal").write_bytes(b"active")
        with self.assertRaisesRegex(ValueError, "Close the study"):
            report_sources.preserve(self.source, SID, self.objects)
        os.remove(self.source + "-wal")
        Path(self.source).write_bytes(b"not SQLite")
        with self.assertRaisesRegex(ValueError, "valid SQLite"):
            report_sources.preserve(self.source, SID, self.objects)

    def test_reloading_an_expired_original_restores_a_new_immutable_source(self):
        self.objects.delete(self.retained["key"])
        self.manifest["db_path"] = self.source
        with patch.object(project_cache, "_report_storage", self.objects):
            project_cache._preserve_report_source(self.manifest)
        restored = self.manifest["report_source"]
        self.assertNotEqual(restored["key"], self.retained["key"])
        self.assertTrue(self.objects.exists(restored["key"]))
        self.assertEqual(restored["sha256"], self.retained["sha256"])
    def test_catalog_scope_and_template_matching(self):
        data = self.client.get("/api/reports/catalog", headers=self.headers).json
        self.assertTrue(data["online"])
        self.assertEqual(data["studies"][0]["study_type"], 1)
        self.assertNotIn("path", data["templates"][0])
        self.assertEqual(self.client.get("/api/reports/catalog", headers={"X-Session-Id": OTHER}).json["studies"], [])
        response, _ = self.create(jobs=[{"project_id": PID, "template_id": "fault-summary"}])
        self.assertEqual(response.status_code, 400)

    def test_jobs_are_private_and_worker_requires_auth(self):
        response, data = self.create()
        self.assertEqual(response.status_code, 202)
        job = response.json["jobs"][0]
        self.assertFalse(any(k.startswith("_") for k in job))
        self.assertNotIn(SID, str(job))
        self.assertEqual(self.client.get("/api/reports/jobs").status_code, 400)
        self.assertEqual(self.client.get("/api/reports/jobs", headers={"X-Session-Id": OTHER}).json["jobs"], [])
        self.assertEqual(self.client.post("/api/reports/jobs", headers={"X-Session-Id": OTHER}, json=data).status_code, 404)
        self.assertEqual(self.client.post("/api/report-worker/claim", json={}).status_code, 401)

    def test_idempotency_and_changed_selection(self):
        first, data = self.create()
        again = self.client.post("/api/reports/jobs", json=data, headers=self.headers)
        self.assertEqual(first.json, again.json)
        data["timestamp"] = True
        self.assertEqual(self.client.post("/api/reports/jobs", json=data, headers=self.headers).status_code, 409)
        response, _ = self.create(jobs=[{"project_id": PID, "template_id": "duty-summary", "source_sha256": "f" * 64}])
        self.assertEqual(response.status_code, 409)

    def test_only_one_worker_claims_each_job_and_state_survives_restart(self):
        self.create()
        other = ReportService(self.objects, RecordStore(self.objects, self.temp.name))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda svc: svc.claim(uuid.uuid4().hex, self.inventory), [self.service, other]))
        self.assertEqual(sum(j is not None for j in claims), 1)
        self.assertEqual(other.jobs(SID)[0]["status"], "generating")

    def test_expired_lease_is_retried_and_stale_worker_is_fenced(self):
        self.create()
        first = self.claim()
        self.service.records.update("sessions/" + SID, lambda state: state["jobs"][0].update(_lease_until=0))
        second = self.claim()
        self.assertNotEqual(first["lease"], second["lease"])
        stale = self.client.post("/api/report-worker/heartbeat", headers=self.worker_headers, json=self.lease(first))
        self.assertEqual(stale.status_code, 409)
        self.service.records.update("sessions/" + SID, lambda state: state["jobs"][0].update(_lease_until=0))
        self.assertIsNone(self.claim())
        self.assertEqual(self.service.jobs(SID)[0]["status"], "failed")

    def test_source_pdf_and_zip_flow(self):
        response, _ = self.create()
        job = self.claim()
        source = self.client.post("/api/report-worker/source", json=self.lease(job), headers=self.worker_headers)
        self.assertEqual(source.data, Path(self.source).read_bytes())
        source.close()
        pdf = self.finish(job)
        jid = job["id"]
        result = self.client.get(f"/api/reports/jobs/{jid}/pdf", headers=self.headers)
        self.assertEqual(result.data, pdf)
        self.assertEqual(result.headers["Cache-Control"], "private, no-store")
        result.close()
        self.assertEqual(self.client.get(f"/api/reports/jobs/{jid}/pdf", headers={"X-Session-Id": OTHER}).status_code, 404)
        result = self.client.post("/api/reports/download-zip", json={"ids": [jid]}, headers=self.headers)
        with zipfile.ZipFile(io.BytesIO(result.data)) as archive:
            self.assertEqual(archive.read(archive.namelist()[0]), pdf)
        result.close()
        self.assertEqual(self.client.post("/api/reports/download-zip", json={"ids": [jid]}, headers={"X-Session-Id": OTHER}).status_code, 404)

    def test_pdf_must_match_uploaded_checksum(self):
        self.create()
        job = self.claim()
        key = self.service.get(SID, job["id"])["_output"]
        self.objects.upload_from(self.source, key)
        response = self.client.post("/api/report-worker/complete", headers=self.worker_headers,
                                   json={**self.lease(job), "pdf_sha256": self.retained["sha256"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.service.get(SID, job["id"])["status"], "generating")

    def test_failed_report_does_not_stop_the_next_job(self):
        self.create(jobs=[{"project_id": PID, "template_id": "duty-summary"}, {"project_id": PID, "template_id": "duty-complete"}])
        first = self.claim()
        self.client.post("/api/report-worker/fail", headers=self.worker_headers, json=self.lease(first))
        second = self.claim()
        self.finish(second)
        self.assertEqual({j["status"] for j in self.service.jobs(SID)}, {"failed", "ready"})

    def test_offline_disabled_and_quota_are_explicit(self):
        self.service.records.update("workers/test-worker", lambda state: state.update(seen=0))
        self.assertEqual(self.create()[0].status_code, 503)
        self.service.worker_seen("test-worker", self.inventory)
        with patch.object(appconfig, "REPORTS_ENABLED", False):
            self.assertFalse(self.client.get("/api/reports/catalog", headers=self.headers).json["enabled"])
            self.assertEqual(self.create()[0].status_code, 503)
        for _ in range(24):
            self.assertEqual(self.create()[0].status_code, 202)
        self.assertEqual(self.create()[0].status_code, 429)

    def test_invalid_requests_do_not_create_jobs(self):
        for invalid in (None, [], "x"):
            self.assertEqual(self.client.post("/api/reports/jobs", json=invalid, headers=self.headers).status_code, 400)
        for invalid in (None, [], [{}], [{"project_id": "../outside", "template_id": "duty-summary"}]):
            self.assertEqual(self.create(jobs=invalid)[0].status_code, 400)
        self.assertEqual(self.service.jobs(SID), [])

    def test_original_headers_are_private_and_older_uploads_are_supported(self):
        url = f"/api/reports/studies/{PID}/headers"
        for legacy in (False, True):
            if legacy:
                self.retained.pop("headers")
            response = self.client.get(url, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['values']['project'], 'Original project')
            self.assertEqual(response.json['values']['sn'], 'Original SN')
            self.assertEqual(response.json['source_sha256'], self.retained['sha256'])
        self.assertEqual(self.client.get(url, headers={'X-Session-Id': OTHER}).status_code, 404)
        self.assertEqual(self.client.get('/api/reports/studies/invalid/headers', headers=self.headers).status_code, 404)

    def test_header_changes_survive_jobs_and_claims_without_changing_original(self):
        headers = {'sn': None, 'date': '17 Sep 2026', 'revision': 'A', 'project': 'Test <Project> & Team', 'filename': ''}
        before = Path(self.source).read_bytes()
        response, data = self.create(headers=headers)
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(response.json['jobs'][0]['headers'], headers)
        self.assertEqual(self.client.post('/api/reports/jobs', json=data, headers=self.headers).json, response.json)
        data['headers']['revision'] = 'B'
        self.assertEqual(self.client.post('/api/reports/jobs', json=data, headers=self.headers).status_code, 409)
        claimed = self.claim()
        self.assertEqual(claimed['headers']['revision'], 'A')
        self.assertIsNone(claimed['headers']['sn'])
        self.assertEqual(Path(self.source).read_bytes(), before)
        self.assertEqual(self.retained['headers']['project'], 'Original project')

    def test_invalid_header_input_is_rejected_before_job_creation(self):
        for headers in (None, [], {'unknown': 'x'}, {'sn': True}, {'project': 10}, {'date': 'a\nb'},
                        {'revision': 'x' * 33}, {'project': '\x00'}, {'project': '\ud800'}):
            response, _ = self.create(headers=headers)
            self.assertEqual(response.status_code, 400, headers)
        self.assertEqual(self.service.jobs(SID), [])

    def test_old_workers_cannot_silently_ignore_custom_headers(self):
        self.service.worker_seen('test-worker', self.inventory, header_version=0)
        self.assertEqual(self.create(headers={'sn': None})[0].status_code, 503)
        self.service.worker_seen('new-worker', self.inventory, header_version=1)
        self.assertEqual(self.create(headers={'sn': None})[0].status_code, 202)
        self.assertIsNone(self.service.claim('old-worker', self.inventory))
        self.assertIsNotNone(self.service.claim('new-worker', self.inventory, header_version=1))


class UnbalancedLoadFlowSource(unittest.TestCase):
    """.UL1S has no ISCStudyCase, so its study_type is detected from the
    presence/non-emptiness of LFSumTotalLF3PH instead. See report_sources.py."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.objects = LocalStorage(os.path.join(self.temp.name, "objects"))
        self.source = os.path.join(self.temp.name, "study.UL1S")

    def _write(self, with_table=True, with_rows=True):
        with sqlite3.connect(self.source) as db:
            if with_table:
                db.execute("CREATE TABLE LFSumTotalLF3PH (BusID TEXT, MW REAL)")
                if with_rows:
                    db.execute("INSERT INTO LFSumTotalLF3PH VALUES ('Bus A', 1.5)")
            db.execute("CREATE TABLE Headr (SN TEXT, Date TEXT, Project TEXT, PSRev TEXT)")
            db.execute("INSERT INTO Headr VALUES ('SN', 'Date', 'Project', '24.0')")
        db.close()

    def test_ul1s_source_is_detected_as_unbalanced_load_flow(self):
        self._write()
        retained = report_sources.preserve(self.source, SID, self.objects)
        self.assertEqual(retained["study_type"], 2)
        self.assertEqual(retained["study_name"], "Unbalanced Load Flow")
        self.assertEqual(report_sources.sha256(self.source), retained["sha256"])

    def test_ul1s_without_result_table_is_rejected(self):
        self._write(with_table=False)
        with self.assertRaisesRegex(ValueError, "missing its load flow result table"):
            report_sources.preserve(self.source, SID, self.objects)

    def test_ul1s_with_empty_result_table_is_rejected(self):
        self._write(with_rows=False)
        with self.assertRaisesRegex(ValueError, "no load flow results"):
            report_sources.preserve(self.source, SID, self.objects)


if __name__ == "__main__":
    unittest.main()
