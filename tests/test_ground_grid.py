"""Ground grid (.GRDS) column cleanup."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etap_reader import ground_grid  # noqa: E402
from tests import fixtures  # noqa: E402


class Normalize(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _normalized(self, rows):
        path = fixtures.make_grds(os.path.join(self.dir, "g.GRDS"), rows)
        conn = sqlite3.connect(path)
        ground_grid.normalize(conn)
        conn.row_factory = sqlite3.Row
        out = [dict(r) for r in conn.execute("SELECT * FROM GroundGrid")]
        conn.close()
        return out

    def test_binary_geometry_becomes_its_length(self):
        rows = self._normalized([("GRD1", 27.1, 150.1, 1778879078.0, "AABBCC")])
        self.assertNotIn("data", rows[0])
        self.assertEqual(rows[0]["GeometryBytes"], 6)

    def test_missing_geometry_is_zero_not_null(self):
        rows = self._normalized([("GRD1", 0.0, 0.0, 0.0, None)])
        self.assertEqual(rows[0]["GeometryBytes"], 0)

    def test_epoch_becomes_readable(self):
        rows = self._normalized([("GRD1", 27.1, 150.1, 1778879078.0, "AA")])
        # Local time, so assert the shape rather than a fixed instant.
        self.assertRegex(rows[0]["RunDate"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_never_run_grid_has_no_date(self):
        """RunDate 0 means the grid was never solved, not 1970."""
        rows = self._normalized([("GRD1", 0.0, 0.0, 0.0, None)])
        self.assertIsNone(rows[0]["RunDate"])

    def test_results_are_left_alone(self):
        rows = self._normalized([("GRD1", 27.09375, 150.137, 1778879078.0, "AA")])
        self.assertEqual(rows[0]["RG"], 27.09375)
        self.assertEqual(rows[0]["GPR"], 150.137)
        self.assertEqual(rows[0]["ID"], "GRD1")

    def test_every_row_survives(self):
        rows = self._normalized([
            ("GRD1", 0.0, 0.0, 0.0, None),
            ("GRD2", 27.1, 150.1, 1778879078.0, "AABB"),
            ("GRD3", 32.3, 117.4, 1778879128.0, "CC"),
        ])
        self.assertEqual([r["ID"] for r in rows], ["GRD1", "GRD2", "GRD3"])

    def test_file_without_the_table_is_reported_not_raised(self):
        path = os.path.join(self.dir, "empty.sqlite")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE Something (x INTEGER)")
        self.assertFalse(ground_grid.normalize(conn))
        conn.close()

    def test_garbage_date_does_not_raise(self):
        rows = self._normalized([("GRD1", 1.0, 2.0, float("inf"), "AA")])
        self.assertIsNone(rows[0]["RunDate"])


if __name__ == "__main__":
    unittest.main()
