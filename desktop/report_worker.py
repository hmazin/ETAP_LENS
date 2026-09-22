"""Outbound-only Windows Crystal worker. Uses Python's standard library.

The API URL and shared secret come from environment variables, never command
line arguments. No web server or inbound firewall rule is needed on this PC.
"""
import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent

# Must match etap_reader/report_sources.py: FileValidator on the .NET side only
# whitelists these extensions, so the downloaded source needs a real one, not
# just any accepted placeholder.
SOURCE_EXTENSION_BY_STUDY_TYPE = {2: ".UL1S"}
DEFAULT_SOURCE_EXTENSION = ".SA1S"


def digest(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


class Worker:
    def __init__(self, args):
        self.api = os.environ.get("ETAP_LENS_REPORT_API", "").rstrip("/")
        self.token = os.environ.get("ETAP_LENS_REPORT_WORKER_TOKEN", "")
        parsed = urlparse(self.api)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
            raise ValueError("Set ETAP_LENS_REPORT_API to an HTTPS API URL (HTTP is allowed only on localhost).")
        if parsed.username or parsed.password or parsed.query or parsed.fragment or len(self.token) < 32:
            raise ValueError("Set a worker token of at least 32 characters and an API URL without credentials or query parameters.")
        self.exe = Path(args.engine).resolve()
        self.templates = Path(args.templates).resolve()
        self.logs = Path(args.logs).resolve()
        self.logs.mkdir(parents=True, exist_ok=True)
        self.timeout = args.timeout
        self.worker_id = hashlib.sha256(socket.gethostname().encode()).hexdigest()[:24]
        self.catalog = json.loads((ROOT.parent / "etap_reader" / "report_catalog.json").read_text(encoding="utf-8"))
        probe = subprocess.run([str(self.exe), "--probe"], check=True, timeout=45, capture_output=True)
        if json.loads(probe.stdout).get("header_version", 0) < 1:
            raise RuntimeError("Rebuild the native report worker to enable report header settings.")

    def inventory(self):
        available = {}
        for item in self.catalog:
            path = self.templates / item["path"]
            if path.is_file():
                sidecar = Path(str(path) + ".json")
                available[item["id"]] = {"sha256": digest(path),
                                         "options_sha256": digest(sidecar) if sidecar.is_file() else ""}
        if not available:
            raise RuntimeError("No approved templates were found under the configured template root.")
        return available

    def post(self, route, data):
        request = Request(self.api + "/api/report-worker/" + route,
                          data=json.dumps(data).encode(),
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        return urlopen(request, timeout=90)

    def json(self, route, data):
        with self.post(route, data) as response:
            return json.load(response)

    @staticmethod
    def storage_url(url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "storage.googleapis.com" or parsed.username or parsed.password:
            raise ValueError("Unexpected storage URL.")
        return url

    @staticmethod
    def save_response(response, path, expected):
        count = 0
        with open(path, "wb") as stream:
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > expected:
                    raise ValueError("Study download exceeds the expected size.")
                stream.write(chunk)
        if count != expected:
            raise ValueError("Incomplete study download.")

    def render(self, job):
        lease = {key: job[key] for key in ("session", "id", "lease")}
        stop = threading.Event()
        lost = threading.Event()

        def heartbeat():
            while not stop.wait(25):
                try:
                    self.json("heartbeat", lease)
                except Exception:
                    logging.exception("Report %s: lease renewal failed", job["id"])
                    lost.set()
                    return
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="etap-worker-") as directory:
                work = Path(directory)
                source = work / ("source" + SOURCE_EXTENSION_BY_STUDY_TYPE.get(job["study_type"], DEFAULT_SOURCE_EXTENSION))
                pdf = work / "report.pdf"
                with self.post("source", lease) as response:
                    if "application/json" in response.headers.get("Content-Type", ""):
                        url = self.storage_url(json.load(response)["url"])
                        with urlopen(url, timeout=180) as raw:
                            self.save_response(raw, source, job["source_bytes"])
                    else:
                        self.save_response(response, source, job["source_bytes"])
                if digest(source) != job["source_sha256"]:
                    raise ValueError("Downloaded source checksum does not match.")
                template = job["template"]
                # The API returns a catalog ID, never an arbitrary user template.
                known = next((t for t in self.catalog if t["id"] == template["id"]), None)
                if known != template:
                    raise ValueError("Worker and API template catalogs differ. Update the worker.")
                request = {"source_path": str(source), "source_sha256": job["source_sha256"],
                           "study_type": job["study_type"], "template_root": str(self.templates),
                           "template_path": template["path"], "template_name": template["name"],
                           "template_sha256": job["template_hashes"]["sha256"],
                           "options_sha256": job["template_hashes"].get("options_sha256", ""),
                           "headers": job.get("headers", {}),
                           "log_directory": str(self.logs), "work_directory": str(work), "output_path": str(pdf)}
                request_path = work / "request.json"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                result = subprocess.run([str(self.exe), str(request_path)], capture_output=True,
                                        timeout=self.timeout, text=True, encoding="utf-8", errors="replace")
                if result.returncode:
                    logging.error("Report %s: %s", job["id"], result.stderr[-20000:])
                    raise RuntimeError("Crystal rendering failed; see the worker log.")
                if lost.is_set():
                    raise RuntimeError("Report lease was lost. Output will not be published.")
                if not 5 < pdf.stat().st_size <= 100 * 1024 * 1024:
                    raise ValueError("Generated PDF exceeds the 100 MB limit or is empty.")
                grant = self.json("upload", lease)
                if grant.get("direct"):
                    headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/pdf",
                               "X-Report-Session": lease["session"], "X-Report-Id": lease["id"], "X-Report-Lease": lease["lease"]}
                    url = self.api + "/api/report-worker/upload-direct"
                else:
                    headers = grant["upload"]["headers"]
                    url = self.storage_url(grant["upload"]["url"])
                # A bounded PDF; no source or report bytes are logged.
                request = Request(url, data=pdf.read_bytes(), headers=headers, method="POST" if grant.get("direct") else "PUT")
                with urlopen(request, timeout=180) as response:
                    response.read()
                self.json("complete", {**lease, "pdf_sha256": digest(pdf)})
                logging.info("Report %s ready (%s bytes)", job["id"], pdf.stat().st_size)
        except Exception:
            logging.exception("Report %s failed", job["id"])
            try:
                self.json("fail", lease)
            except Exception:
                logging.warning("Report %s could not be marked failed; lease recovery will handle it", job["id"])
        finally:
            stop.set()
            thread.join(timeout=2)

    def run(self, once=False):
        while True:
            try:
                result = self.json("claim", {"worker_id": self.worker_id, "templates": self.inventory(), "header_version": 1})
                if result.get("job"):
                    self.render(result["job"])
                elif not once:
                    time.sleep(15)
            except Exception:
                logging.exception("Report service connection failed")
                if not once:
                    time.sleep(30)
            if once:
                return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", default=str(ROOT / "EtapCrystalReporter" / "Templates"))
    parser.add_argument("--engine", default=str(ROOT / "EtapCrystalReporter/bin/x86/Release/EtapCrystalReporter.Worker.exe"))
    parser.add_argument("--logs", default=str(ROOT.parent / "cache" / "report-worker-logs"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--once", action="store_true", help="Claim at most one report, then exit.")
    args = parser.parse_args()
    Path(args.logs).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(Path(args.logs) / "worker.log", maxBytes=5 * 1024 * 1024, backupCount=3), logging.StreamHandler()])
    Worker(args).run(args.once)


if __name__ == "__main__":
    main()
