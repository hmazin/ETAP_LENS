"""
Harmonic plot companions: curve-name parsing and attachment.

The parsing tests carry most of the weight here. Curve tables are named
"<DeviceType>_<DeviceID>_<Curve>[_<XAxis>]_<IID>", which looks like a job for
str.split('_') until a device is named "PCC_1" - and the one project this was
built against happens to contain no such device, so nothing but a test will
catch it.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etap_reader import ha_plots  # noqa: E402
from tests import fixtures  # noqa: E402


class ParseCurveName(unittest.TestCase):
    def test_impedance_curve(self):
        self.assertEqual(
            ha_plots._parse_curve_name("Buses_PCC1_Z Magnitude_Hz_4116", {4116: "PCC1"}),
            ("Buses", "PCC1", "Z Magnitude", "Frequency (Hz)"))

    def test_order_axis(self):
        self.assertEqual(
            ha_plots._parse_curve_name("Buses_PCC1_Z Angle_Order_4116", {4116: "PCC1"}),
            ("Buses", "PCC1", "Z Angle", "Harmonic Order"))

    def test_waveform_has_no_axis_segment(self):
        self.assertEqual(
            ha_plots._parse_curve_name("Cables_Cable25_Waveform_4705", {4705: "Cable25"}),
            ("Cables", "Cable25", "Waveform", "Time (ms)"))

    def test_device_name_containing_underscore(self):
        """The case a split('_') implementation gets wrong."""
        self.assertEqual(
            ha_plots._parse_curve_name("Buses_PCC_1_Spectrum_Hz_4116", {4116: "PCC_1"}),
            ("Buses", "PCC_1", "Spectrum", "Frequency (Hz)"))

    def test_device_name_ending_in_digits_is_not_mistaken_for_the_iid(self):
        self.assertEqual(
            ha_plots._parse_curve_name("Buses_Bus_4116_Waveform_9001", {9001: "Bus_4116"}),
            ("Buses", "Bus_4116", "Waveform", "Time (ms)"))

    def test_device_name_that_looks_like_an_axis_suffix(self):
        """A curve whose device is called "Order" must not eat the axis."""
        self.assertEqual(
            ha_plots._parse_curve_name("Buses_Order_Z Magnitude_Hz_7", {7: "Order"}),
            ("Buses", "Order", "Z Magnitude", "Frequency (Hz)"))

    def test_unknown_iid_is_rejected(self):
        self.assertIsNone(
            ha_plots._parse_curve_name("Buses_PCC1_Z Magnitude_Hz_4116", {999: "Other"}))

    def test_name_without_iid_suffix_is_rejected(self):
        self.assertIsNone(
            ha_plots._parse_curve_name("SystemFrequency", {4116: "PCC1"}))

    def test_device_not_present_in_the_body_is_rejected(self):
        self.assertIsNone(
            ha_plots._parse_curve_name("Buses_Somewhere Else_Waveform_4116", {4116: "PCC1"}))


class CompanionsFor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_finds_both_companions(self):
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "FS_H01.HA1S"))
        fixtures.make_plot_db(os.path.join(self.dir, "FS_H01.fspdb"), [], [])
        fixtures.make_plot_db(os.path.join(self.dir, "FS_H01.hfpdb"), [], [])
        found = {os.path.basename(p) for p, _ in ha_plots.companions_for(ha1s)}
        self.assertEqual(found, {"FS_H01.fspdb", "FS_H01.hfpdb"})

    def test_no_companions_is_empty_not_an_error(self):
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "Lonely.HA1S"))
        self.assertEqual(ha_plots.companions_for(ha1s), [])

    def test_casing_is_not_assumed(self):
        """ETAP writes .HA1S upper and .fspdb lower; neither is guaranteed."""
        ha1s = fixtures.make_ha1s(os.path.join(self.dir, "Case.ha1s"))
        fixtures.make_plot_db(os.path.join(self.dir, "Case.FSPDB"), [], [])
        found = [os.path.basename(p) for p, _ in ha_plots.companions_for(ha1s)]
        self.assertEqual(found, ["Case.FSPDB"])


class Attach(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ha1s = fixtures.make_ha1s(os.path.join(self.dir, "FS_H01.HA1S"))
        self.cache = os.path.join(self.dir, "cache.sqlite")

    def _cache_conn(self):
        # attach() works on an already-copied cache, so mimic that copy.
        src = sqlite3.connect(self.ha1s)
        dst = sqlite3.connect(self.cache)
        src.backup(dst)
        src.close()
        return dst

    def test_builds_index_and_long_table(self):
        fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_4116", 5, False),
             ("Buses_PCC1_Z Angle_Order_4116", 5, False)],
            [("PCC1", 4116), ("PCC1", 4116)])
        conn = self._cache_conn()
        summary = ha_plots.attach(conn, self.ha1s)

        self.assertEqual(summary["curves"], 2)
        self.assertEqual(summary["points"], 10)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM HAPlotIndex").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM HAPlotCurves").fetchone()[0], 10)
        # The original tables survive under ETAP's own names.
        self.assertEqual(
            conn.execute('SELECT COUNT(*) FROM "Buses_PCC1_Z Magnitude_Hz_4116"').fetchone()[0], 5)
        conn.close()

    def test_waveform_angle_column_is_carried_through(self):
        fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.hfpdb"),
            [("Buses_PCC1_Waveform_4116", 4, True)], [("PCC1", 4116)])
        conn = self._cache_conn()
        ha_plots.attach(conn, self.ha1s)
        angles = conn.execute("SELECT Angle FROM HAPlotCurves ORDER BY X").fetchall()
        self.assertEqual([a[0] for a in angles], [0.0, 3.0, 6.0, 9.0])
        conn.close()

    def test_curves_without_angle_store_null(self):
        fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_4116", 2, False)], [("PCC1", 4116)])
        conn = self._cache_conn()
        ha_plots.attach(conn, self.ha1s)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM HAPlotCurves WHERE Angle IS NOT NULL").fetchone()[0], 0)
        conn.close()

    def test_empty_companion_yields_nothing(self):
        """A real project contained an .hfpdb holding only metadata tables,
        left over from an earlier run. Zero curves is not an error."""
        fixtures.make_plot_db(os.path.join(self.dir, "FS_H01.hfpdb"), [], [("PCC1", 4116)])
        conn = self._cache_conn()
        self.assertEqual(ha_plots.attach(conn, self.ha1s), {})
        conn.close()

    def test_no_companions_yields_nothing(self):
        conn = self._cache_conn()
        self.assertEqual(ha_plots.attach(conn, self.ha1s), {})
        conn.close()

    def test_system_frequency_is_recorded_per_curve(self):
        fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_4116", 2, False)], [("PCC1", 4116)],
            system_freq=50.0)
        conn = self._cache_conn()
        ha_plots.attach(conn, self.ha1s)
        self.assertEqual(conn.execute("SELECT SystemFreq FROM HAPlotIndex").fetchone()[0], 50.0)
        conn.close()

    def test_unreadable_companion_does_not_fail_the_import(self):
        """The results are already in hand; a corrupt plot file must not
        cost the engineer the study."""
        with open(os.path.join(self.dir, "FS_H01.fspdb"), "wb") as f:
            f.write(b"not a database at all")
        conn = self._cache_conn()
        self.assertEqual(ha_plots.attach(conn, self.ha1s), {})
        conn.close()


if __name__ == "__main__":
    unittest.main()
