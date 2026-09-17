"""Durable report records with optimistic concurrency on disk or in GCS."""
import json
import os
import sqlite3
from contextlib import contextmanager


class Conflict(Exception):
    pass


class RecordStore:
    def __init__(self, objects, directory):
        self.objects = objects
        self.path = os.path.join(directory, "report-jobs.sqlite")
        if objects.kind == "local":
            os.makedirs(directory, exist_ok=True)
            with self._connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT, version INTEGER)")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _key(key):
        return "reports/state/" + key + ".json"

    def read(self, key):
        if self.objects.kind == "local":
            with self._connect() as db:
                row = db.execute("SELECT value, version FROM records WHERE key=?", (key,)).fetchone()
            return (json.loads(row[0]), row[1]) if row else ({}, 0)
        from google.api_core.exceptions import NotFound, PreconditionFailed
        for _ in range(5):
            blob = self.objects._blob(self._key(key))
            try:
                blob.reload()
                version = int(blob.generation)
                return json.loads(blob.download_as_bytes(if_generation_match=version)), version
            except NotFound:
                return {}, 0
            except PreconditionFailed:
                continue
        raise Conflict()

    def write(self, key, value, version):
        encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
        if self.objects.kind == "local":
            with self._connect() as db:
                if version:
                    changed = db.execute("UPDATE records SET value=?, version=version+1 WHERE key=? AND version=?",
                                         (encoded, key, version)).rowcount
                else:
                    changed = db.execute("INSERT OR IGNORE INTO records VALUES (?, ?, 1)", (key, encoded)).rowcount
                if not changed:
                    raise Conflict()
            return
        from google.api_core.exceptions import PreconditionFailed
        try:
            self.objects._blob(self._key(key)).upload_from_string(
                encoded, content_type="application/json", if_generation_match=version)
        except PreconditionFailed:
            raise Conflict()

    def update(self, key, change):
        for _ in range(12):
            try:
                value, version = self.read(key)
                before = json.dumps(value, sort_keys=True)
                result = change(value)
                if json.dumps(value, sort_keys=True) == before:
                    return result
                self.write(key, value, version)
                return result
            except Conflict:
                continue
        raise RuntimeError("Report queue is busy. Please try again.")

    def keys(self, prefix):
        if self.objects.kind == "local":
            with self._connect() as db:
                return [r[0] for r in db.execute("SELECT key FROM records WHERE key LIKE ? ORDER BY key", (prefix + "%",))]
        base = "reports/state/"
        return [k[len(base):-5] for k in self.objects.list(base + prefix) if k.endswith(".json")]
