"""
Upload pre-flight, with most of the weight on the companion name rule.

That rule is the whole containment story for companion uploads: a companion
is written into a session directory without creating a project, so the only
thing bounding what can be written there is that its name must be the
primary's stem plus a companion extension.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etap_reader import upload_guard  # noqa: E402
from tests import fixtures  # noqa: E402


class CompanionName(unittest.TestCase):
    def test_matching_stem_is_accepted(self):
        self.assertEqual(
            upload_guard.check_companion_name("FS_H01.fspdb", "FS_H01.HA1S"), ".fspdb")

    def test_casing_does_not_matter(self):
        """ETAP writes the study upper and the companion lower."""
        self.assertEqual(
            upload_guard.check_companion_name("fs_h01.FSPDB", "FS_H01.HA1S"), ".fspdb")

    def test_different_stem_is_refused(self):
        with self.assertRaises(upload_guard.RejectedUpload):
            upload_guard.check_companion_name("OtherRun.fspdb", "FS_H01.HA1S")

    def test_non_companion_extension_is_refused(self):
        with self.assertRaises(upload_guard.RejectedUpload):
            upload_guard.check_companion_name("FS_H01.tu1s", "FS_H01.HA1S")

    def test_a_study_cannot_be_smuggled_in_as_a_companion(self):
        with self.assertRaises(upload_guard.RejectedUpload):
            upload_guard.check_companion_name("FS_H01.HA1S", "FS_H01.HA1S")

    def test_traversal_in_the_name_is_refused(self):
        """secure_filename runs first in the route, but the rule must hold on
        its own - it is the last thing between a name and the filesystem."""
        for name in ("../FS_H01.fspdb", "..\\FS_H01.fspdb", "/etc/FS_H01.fspdb"):
            with self.subTest(name=name):
                with self.assertRaises(upload_guard.RejectedUpload):
                    upload_guard.check_companion_name(name, "FS_H01.HA1S")

    def test_extensionless_name_is_refused(self):
        with self.assertRaises(upload_guard.RejectedUpload):
            upload_guard.check_companion_name("FS_H01", "FS_H01.HA1S")


class CompanionContent(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_valid_companion_passes(self):
        p = fixtures.make_plot_db(
            os.path.join(self.dir, "FS_H01.fspdb"),
            [("Buses_PCC1_Z Magnitude_Hz_1", 2, False)], [("PCC1", 1)])
        names = upload_guard.validate_companion(p, "FS_H01.fspdb", "FS_H01.HA1S")
        self.assertIn("DeviceID_IID", names)

    def test_non_sqlite_content_is_refused(self):
        p = os.path.join(self.dir, "FS_H01.fspdb")
        with open(p, "wb") as f:
            f.write(b"MZ\x90\x00 this is an executable, not a plot database")
        with self.assertRaises(upload_guard.RejectedUpload):
            upload_guard.validate_companion(p, "FS_H01.fspdb", "FS_H01.HA1S")

    def test_name_is_checked_before_content(self):
        """A wrongly-named file must be refused without being opened."""
        p = os.path.join(self.dir, "nope.fspdb")
        with open(p, "wb") as f:
            f.write(b"not sqlite")
        with self.assertRaises(upload_guard.RejectedUpload) as ctx:
            upload_guard.validate_companion(p, "Elsewhere.fspdb", "FS_H01.HA1S")
        self.assertIn("does not belong", str(ctx.exception))


class StudyExtensions(unittest.TestCase):
    def test_new_types_are_accepted(self):
        for ext in (".ha1s", ".grds"):
            with self.subTest(ext=ext):
                self.assertEqual(upload_guard.check_extension("x" + ext), ext)

    def test_companions_are_not_accepted_as_studies(self):
        """A .fspdb on its own has nothing to show; it must not be loadable."""
        for ext in upload_guard.COMPANION_EXTENSIONS:
            with self.subTest(ext=ext):
                with self.assertRaises(upload_guard.RejectedUpload):
                    upload_guard.check_extension("x" + ext)


if __name__ == "__main__":
    unittest.main()
