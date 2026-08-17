"""
How a file type is routed: import behaviour, and the several places an
extension has to be registered for it to work end to end.

The registration tests exist because that knowledge is spread across four
modules by necessity - upload_guard cannot import study_result without a
cycle, and the board deliberately lists studies this tool cannot read. Each
place is right to be separate, which is exactly why nothing but a test
notices when one of them is forgotten.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etap_reader import categories, modules, study_result, upload_guard  # noqa: E402
from tests import fixtures  # noqa: E402


class ExtensionRegistration(unittest.TestCase):
    def test_upload_guard_matches_study_result(self):
        """upload_guard duplicates the list rather than importing it; if the
        two drift, a type loads locally and is refused on upload."""
        self.assertEqual(upload_guard.STUDY_EXTENSIONS, set(study_result.STUDY_EXTENSIONS))

    def test_every_study_extension_has_a_module_tile(self):
        for ext in study_result.STUDY_EXTENSIONS:
            with self.subTest(ext=ext):
                self.assertIsNotNone(
                    modules.module_for("x" + ext),
                    f"{ext} imports but no board tile would show it")

    def test_every_study_extension_has_a_category_set(self):
        for ext, (_, category_set) in study_result.STUDY_EXTENSIONS.items():
            with self.subTest(ext=ext):
                self.assertIn(category_set, categories.STUDY_CATEGORIES)

    def test_module_extensions_are_all_importable(self):
        """A tile that offers a file the importer will reject is a dead end."""
        for m in modules.MODULES:
            for ext in m["extensions"]:
                if ext in (".oti", ".mdf", ".bak"):
                    continue  # the model module, read via SQL Server instead
                with self.subTest(module=m["key"], ext=ext):
                    self.assertIn(ext, study_result.STUDY_EXTENSIONS)

    def test_harmonics_and_ground_grid_are_no_longer_unsupported(self):
        by_key = {m["key"]: m for m in modules.MODULES}
        self.assertTrue(by_key["harmonics"]["extensions"])
        self.assertTrue(by_key["ground_grid"]["extensions"])


class ImportRouting(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _import(self, source):
        out = os.path.join(self.dir, "cache.sqlite")
        return study_result.import_study_to_sqlite(source, out), out

    def test_ha1s_picks_up_its_companions(self):
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "FS_H01.HA1S"))
        fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_4116", 4, False)], [("PCC1", 4116)])
        stats, out = self._import(ha1s)
        self.assertEqual(stats["attached_plots"]["curves"], 1)
        conn = sqlite3.connect(out)
        indexed = {r[0] for r in conn.execute("SELECT table_name FROM _table_index")}
        conn.close()
        # Attached tables must reach _table_index or All Tables cannot see them.
        self.assertIn("HAPlotCurves", indexed)
        self.assertIn("Buses_PCC1_Z Magnitude_Hz_4116", indexed)

    def test_ha1s_without_companions_still_imports(self):
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "Lonely.HA1S"))
        stats, _ = self._import(ha1s)
        self.assertEqual(stats["attached_plots"], {})
        self.assertGreater(stats["rows_total"], 0)

    def test_grds_is_normalized_on_import(self):
        grds = fixtures.make_grds(os.path.join(self.dir, "g.GRDS"),
                                  [("GRD1", 27.1, 150.1, 1778879078.0, "AABB")])
        _, out = self._import(grds)
        conn = sqlite3.connect(out)
        cols = [r[1] for r in conn.execute('PRAGMA table_info("GroundGrid")')]
        conn.close()
        self.assertIn("GeometryBytes", cols)
        self.assertNotIn("data", cols)

    def test_other_study_types_are_untouched(self):
        """The .HA1S and .GRDS hooks are keyed off the extension; nothing
        else should acquire plot tables or lose a column."""
        lf1s = fixtures.make_lf1s(os.path.join(self.dir, "LF.LF1S"))
        # A stray companion sharing the stem must not be picked up by a
        # non-harmonic study.
        fixtures.make_plot_db(
            os.path.join(self.dir, "LF.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_1", 2, False)], [("PCC1", 1)])
        stats, out = self._import(lf1s)
        self.assertEqual(stats["attached_plots"], {})
        conn = sqlite3.connect(out)
        tables = {r[0] for r in conn.execute("SELECT table_name FROM _table_index")}
        conn.close()
        self.assertEqual(tables, {"LFR"})

    def test_both_ha1s_flavors_share_one_category_set(self):
        """A frequency scan and a harmonic load flow are the same extension
        and must resolve to the same curated categories."""
        scan = study_result.study_info(os.path.join(self.dir, "FS_H01.HA1S"))
        hlf = study_result.study_info(os.path.join(self.dir, "HLF_H01.HA1S"))
        self.assertEqual(scan["category_set"], hlf["category_set"])
        self.assertEqual(scan["category_set"], "harmonics")


class HarmonicCategories(unittest.TestCase):
    def test_covers_both_flavors(self):
        cats = categories.STUDY_CATEGORIES["harmonics"]
        tables = {t for c in cats.values() for t in c["tables"]}
        # Frequency scan and harmonic load flow evidence respectively.
        self.assertIn("HAFreqScan", tables)
        self.assertIn("HASystemInfo", tables)

    def test_attached_plot_tables_are_categorized(self):
        self.assertEqual(
            categories.category_for_table("HAPlotCurves", "harmonics"), "plot_curves")
        self.assertEqual(
            categories.category_for_table("HAPlotIndex", "harmonics"), "plot_curves")

    def test_alerts_category_is_discoverable_by_the_violations_report(self):
        """The report picks categories whose key contains alert/violation."""
        self.assertTrue(categories.alert_categories("harmonics"))


class ViolationsReportOffer(unittest.TestCase):
    """The overview offers the report; the report builds it. They must agree
    on which categories count, or the button produces an empty download."""

    def test_ground_grid_reports_no_violations(self):
        """A ground grid computes touch and step voltages and leaves the
        verdict to the engineer - there is nothing to put in the report, so
        the overview must not offer one."""
        self.assertEqual(categories.alert_categories("ground_grid"), [])

    def test_every_other_study_type_does_report_violations(self):
        for category_set in categories.STUDY_CATEGORIES:
            if category_set == "ground_grid":
                continue
            with self.subTest(category_set=category_set):
                self.assertTrue(
                    categories.alert_categories(category_set),
                    f"{category_set} would offer a violations report with no "
                    f"category to fill it")

    def test_named_categories_actually_exist_in_the_set(self):
        for category_set in categories.STUDY_CATEGORIES:
            defined = categories.categories_for_set(category_set)
            for key in categories.alert_categories(category_set):
                with self.subTest(category_set=category_set, key=key):
                    self.assertIn(key, defined)


if __name__ == "__main__":
    unittest.main()
