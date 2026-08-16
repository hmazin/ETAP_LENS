"""
The upload routes, end to end through Flask's test client.

The point of these is the thing unit tests cannot show: that a .HA1S
uploaded with its plot files actually comes out the other side with curves
attached. That depends on the upload directory being named after the
filename stem - which a companion shares - and nothing in either module says
so out loud.
"""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import fixtures  # noqa: E402


class UploadFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A cache directory of our own, so a test run cannot disturb whatever
        # the developer has loaded in the real app.
        cls.cache = tempfile.mkdtemp()
        import app as app_module
        from etap_reader import project_cache
        project_cache.CACHE_DIR = cls.cache
        app_module.app.config["TESTING"] = True
        cls.app_module = app_module
        cls.client = app_module.app.test_client()

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _upload(self, path, filename):
        with open(path, "rb") as f:
            data = {"file": (io.BytesIO(f.read()), filename)}
        return self.client.post("/api/upload", data=data,
                                content_type="multipart/form-data")

    def _upload_companion(self, path, filename, primary):
        with open(path, "rb") as f:
            data = {"file": (io.BytesIO(f.read()), filename), "primary": primary}
        return self.client.post("/api/upload/companion", data=data,
                                content_type="multipart/form-data")

    def _await_job(self, job_id):
        import time
        for _ in range(100):
            r = self.client.get(f"/api/load/status/{job_id}")
            body = r.get_json()
            if body.get("done"):
                return body
            time.sleep(0.05)
        self.fail("import job never finished")

    def test_companion_then_study_yields_attached_curves(self):
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "FS_H01.HA1S"))
        fspdb = fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_4116", 6, False)], [("PCC1", 4116)])

        staged = self._upload_companion(fspdb, "FS_H01.fspdb", "FS_H01.HA1S")
        self.assertEqual(staged.status_code, 200, staged.get_data(as_text=True))

        started = self._upload(ha1s, "FS_H01.HA1S")
        self.assertEqual(started.status_code, 200, started.get_data(as_text=True))
        job = self._await_job(started.get_json()["job_id"])
        self.assertIsNone(job.get("error"))

        stats = job["manifest"]["stats"]
        self.assertEqual(stats["attached_plots"]["curves"], 1)
        self.assertEqual(stats["attached_plots"]["points"], 6)

    def test_study_alone_still_imports(self):
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "Solo.HA1S"))
        started = self._upload(ha1s, "Solo.HA1S")
        job = self._await_job(started.get_json()["job_id"])
        self.assertIsNone(job.get("error"))
        self.assertEqual(job["manifest"]["stats"]["attached_plots"], {})

    def test_companion_for_a_different_study_is_refused(self):
        fspdb = fixtures.make_plot_db(os.path.join(self.dir, "Other.fspdb"), [], [])
        r = self._upload_companion(fspdb, "Other.fspdb", "FS_H01.HA1S")
        self.assertEqual(r.status_code, 400)
        self.assertIn("does not belong", r.get_json()["error"])

    def test_non_sqlite_companion_is_refused(self):
        junk = os.path.join(self.dir, "FS_H01.fspdb")
        with open(junk, "wb") as f:
            f.write(b"not a database")
        r = self._upload_companion(junk, "FS_H01.fspdb", "FS_H01.HA1S")
        self.assertEqual(r.status_code, 400)

    def test_a_rejected_companion_does_not_discard_a_good_one(self):
        good = fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_4116", 3, False)], [("PCC1", 4116)])
        self.assertEqual(
            self._upload_companion(good, "FS_H01.fspdb", "FS_H01.HA1S").status_code, 200)

        junk = os.path.join(self.dir, "bad.hfpdb")
        with open(junk, "wb") as f:
            f.write(b"not a database")
        self.assertEqual(
            self._upload_companion(junk, "FS_H01.hfpdb", "FS_H01.HA1S").status_code, 400)

        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "FS_H01.HA1S"))
        job = self._await_job(self._upload(ha1s, "FS_H01.HA1S").get_json()["job_id"])
        self.assertIsNone(job.get("error"))
        self.assertEqual(job["manifest"]["stats"]["attached_plots"]["curves"], 1)

    def test_companion_cannot_be_loaded_on_its_own(self):
        fspdb = fixtures.make_plot_db(os.path.join(self.dir, "FS_H01.fspdb"), [], [])
        r = self._upload(fspdb, "FS_H01.fspdb")
        self.assertEqual(r.status_code, 400)

    def test_config_advertises_companion_extensions(self):
        cfg = self.client.get("/api/config").get_json()
        self.assertIn(".fspdb", cfg["companion_extensions"])
        self.assertNotIn(".fspdb", cfg["accepted_extensions"])


if __name__ == "__main__":
    unittest.main()
