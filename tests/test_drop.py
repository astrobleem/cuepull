import tempfile
import unittest
from pathlib import Path
from cuepull.core import CuePullError
from cuepull.drop import select_drop, suggested_output


class DropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_unicode_spaces_and_matching_pair(self):
        image, cue = self.root / "Demo Disc \u03a9.bin", self.root / "Demo Disc \u03a9.cue"
        image.write_bytes(b"synthetic")
        cue.write_text('FILE "Demo Disc \u03a9.bin" BINARY', encoding="utf-8")
        self.assertEqual(select_drop([str(image)]), image)
        self.assertEqual(select_drop([str(image), str(cue)]), cue)
        self.assertEqual(select_drop([str(cue), str(image)]), cue)
        self.assertEqual(suggested_output(image), self.root / "Demo Disc \u03a9")

    def test_existing_outputs_receive_safe_suffix(self):
        image = self.root / "demo.bin"
        image.write_bytes(b"original")
        (self.root / "demo").mkdir()
        (self.root / "demo" / "keep.txt").write_text("keep")
        (self.root / "demo (2)").write_text("unrelated")
        self.assertEqual(suggested_output(image), self.root / "demo (3)")
        self.assertEqual((self.root / "demo" / "keep.txt").read_text(), "keep")
        self.assertEqual(image.read_bytes(), b"original")

    def test_ambiguous_and_unsupported_inputs(self):
        paths = []
        for name in ("one.bin", "two.bin", "other.cue", "notes.txt"):
            path = self.root / name
            path.write_bytes(b"test")
            paths.append(path)
        for selection in ([], paths[:2], [paths[0], paths[2]], [paths[3]], paths):
            with self.assertRaises(CuePullError):
                select_drop(selection)


if __name__ == "__main__":
    unittest.main()
