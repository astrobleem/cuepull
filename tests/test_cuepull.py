import hashlib
import io
import json
import os
import stat
import sys
import time
import tempfile
import unittest
from unittest import mock
from pathlib import Path

# Ready source bundles include an independent, replaceable parser copy.
bundled_parser = Path(__file__).resolve().parents[1] / "dependencies"
if bundled_parser.is_dir():
    sys.path.insert(0, str(bundled_parser))

import pycdlib
from cuepull.core import (AmbiguousImageError, CuePullError, Disc, NameAllocator,
    SectorStream, SYNC, Track, descriptor, extract, frames, normalized_name,
    parse_cue, safe_output, verify)
from cuepull.core import is_link


def make_iso(payload=b"Hello CD image!\n", joliet=False, symlink=False):
    iso = pycdlib.PyCdlib()
    options = {"interchange_level": 3, "vol_ident": "SYNTHETIC"}
    if joliet:
        options["joliet"] = 3
    if symlink:
        options["rock_ridge"] = "1.09"
    iso.new(**options)
    kwargs = {"iso_path": "/HELLO.TXT;1"}
    if joliet:
        kwargs["joliet_path"] = "/Hello.txt"
    if symlink:
        kwargs["rr_name"] = "hello.txt"
    iso.add_fp(io.BytesIO(payload), len(payload), **kwargs)
    if symlink:
        iso.add_symlink(symlink_path="/LINK.;1", rr_symlink_name="link", rr_path="hello.txt")
    output = io.BytesIO()
    iso.write_fp(output)
    iso.close()
    return output.getvalue()


def wrap(data, mode):
    if mode == "MODE1/2048":
        return data
    result = bytearray()
    for at in range(0, len(data), 2048):
        payload = data[at:at + 2048].ljust(2048, b"\0")
        if mode == "MODE1/2352":
            result.extend(SYNC + b"\0\x02\0\x01" + payload + b"\0" * 288)
        elif mode == "MODE2/2352":
            result.extend(SYNC + b"\0\x02\0\x02" + b"\0" * 8 + payload + b"\0" * 280)
        elif mode == "MODE2/2336":
            result.extend(b"\0" * 8 + payload + b"\0" * 280)
        else:
            raise ValueError(mode)
    return bytes(result)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # macOS exposes its temporary tree through the /var system symlink.
        # Use the physical fixture path so link-rejection tests exercise only
        # the links intentionally created by the test.
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def image(self, data, mode="MODE1/2352", prefix=0):
        width = {"MODE1/2048": 2048, "MODE2/2336": 2336}.get(mode, 2352)
        path = self.root / "disc.bin"
        path.write_bytes(b"\0" * prefix * width + wrap(data, mode))
        cue = self.root / "disc.cue"
        m, remainder = divmod(prefix, 75 * 60)
        s, f = divmod(remainder, 75)
        cue.write_text(f'FILE "DISC.BIN" BINARY\n TRACK 01 {mode}\n INDEX 01 {m:02}:{s:02}:{f:02}\n')
        return path, cue


class SectorTests(Fixture):
    def test_all_supported_layouts_and_unaligned_cross_sector_reads(self):
        data = bytes(range(256)) * 24
        for mode in ("MODE1/2048", "MODE1/2352", "MODE2/2352", "MODE2/2336"):
            with self.subTest(mode=mode):
                image, cue = self.image(data, mode, 150)
                track = parse_cue(cue)[0]
                self.assertEqual(track.start, 150)
                with SectorStream(track) as stream:
                    stream.seek(2039)
                    self.assertEqual(stream.read(2070), data[2039:4109])
                    stream.seek(-10, 2)
                    self.assertEqual(stream.read(20), data[-10:])
                    self.assertEqual(stream.read(1), b"")

    def test_sync_and_mode_validation(self):
        for location in (0, 15):
            image, cue = self.image(b"a" * 2048)
            damaged = bytearray(image.read_bytes())
            damaged[location] = 7
            image.write_bytes(damaged)
            with SectorStream(parse_cue(cue)[0]) as stream:
                with self.assertRaises(CuePullError):
                    stream.read(1)

    def test_mode2_form2_and_subheader_mismatch(self):
        for where in (18, 20):
            image, cue = self.image(b"a" * 2048, "MODE2/2352")
            raw = bytearray(image.read_bytes())
            if where == 18:
                raw[18] = raw[22] = 0x20
            else:
                raw[20] = 1
            image.write_bytes(raw)
            with SectorStream(parse_cue(cue)[0]) as stream:
                with self.assertRaises(CuePullError):
                    stream.read(1)

    def test_truncated_read_and_memory_bound(self):
        image, cue = self.image(b"a" * 2048)
        track = parse_cue(cue)[0]
        track.end = 10000
        with SectorStream(track) as stream:
            with self.assertRaises(CuePullError):
                stream.read()
            stream.seek(2048)
            with self.assertRaises(CuePullError):
                stream.read(2048)

    def test_audio_refused(self):
        image, _ = self.image(b"a" * 2048)
        with self.assertRaises(CuePullError):
            SectorStream(Track(2, "AUDIO", image, {1: 0}, end=1))


class CueTests(Fixture):
    def cue(self, content):
        path = self.root / "disc.cue"
        path.write_text(content)
        return path

    def test_stored_index00_and_virtual_pregap(self):
        (self.root / "disc.bin").write_bytes(b"\0" * 40 * 2352)
        cue = self.cue('FILE "disc.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 00 00:00:20\nINDEX 01 00:00:22\nTRACK 03 AUDIO\nPREGAP 00:02:00\nINDEX 01 00:00:30')
        tracks = parse_cue(cue)
        self.assertEqual((tracks[0].end, tracks[1].start, tracks[1].end), (20, 22, 30))
        self.assertEqual((tracks[2].start, tracks[2].pregap, tracks[2].end), (30, 150, 40))

    def test_multiple_local_files_explicit_whitelist(self):
        first = self.root / "data.bin"
        second = self.root / "audio.bin"
        first.write_bytes(b"\0" * 3 * 2048)
        second.write_bytes(b"\0" * 4 * 2352)
        cue = self.cue('FILE "data.bin" BINARY\nTRACK 01 MODE1/2048\nINDEX 01 00:00:00\nFILE "audio.bin" BINARY\nTRACK 02 AUDIO\nINDEX 01 00:00:00')
        with self.assertRaises(CuePullError):
            parse_cue(cue)
        self.assertEqual([t.end for t in parse_cue(cue, [first, second])], [3, 4])

    def test_unsafe_external_references(self):
        for name in ('../disc.bin', 'C:\\disc.bin', '/disc.bin', '\\\\server\\disc.bin', 'NUL.bin', 'disc.bin:stream'):
            with self.subTest(name=name), self.assertRaises(CuePullError):
                parse_cue(self.cue(f'FILE "{name}" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00'))

    def test_malformed_cues(self):
        (self.root / "disc.bin").write_bytes(b"\0" * 20 * 2352)
        prefix = 'FILE "disc.bin" BINARY\n'
        for tail in ('TRACK 01 MODE1/2352',
                     'TRACK 01 MODE1/2352\nINDEX 01 00:00:75',
                     'TRACK 01 MODE1/2352\nINDEX 01 00:00:00\nINDEX 01 00:00:01',
                     'TRACK 01 MODE1/2352\nINDEX 01 00:00:10\nTRACK 02 AUDIO\nINDEX 01 00:00:05',
                     'TRACK 01 MODE1/2048\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 01 00:00:10',
                     'TRACK 01 CDG\nINDEX 01 00:00:00',
                     'REM SESSION 2',
                     'TRACK 01 AUDIO\nPREGAP 00:02:00\nINDEX 00 00:00:00\nINDEX 01 00:00:02'):
            with self.subTest(tail=tail), self.assertRaises(CuePullError):
                parse_cue(self.cue(prefix + tail))

    def test_partial_sector(self):
        (self.root / "disc.bin").write_bytes(b"\0" * 2353)
        with self.assertRaises(CuePullError):
            parse_cue(self.cue('FILE "disc.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00'))

    def test_frames(self):
        self.assertEqual(frames("40:43:04"), 183229)
        for value in ("00:60:00", "-1:00:00", "00:00:75", "00:0:00"):
            with self.assertRaises(CuePullError):
                frames(value)


class SafetyTests(Fixture):
    def test_windows_reparse_attribute_is_rejected(self):
        path = mock.Mock()
        path.lstat.return_value = mock.Mock(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        self.assertTrue(is_link(path))
        path.lstat.return_value = mock.Mock(st_mode=stat.S_IFREG, st_file_attributes=0)
        self.assertFalse(is_link(path))

    def test_unsafe_output_components(self):
        for name in ("..", ".", "", "../file", "a/b", "a\\b", "C:evil", "file:ads", "\0", "CON", "nul.txt", "COM1.ext", "LPT\u00b9"):
            with self.subTest(name=name), self.assertRaises(CuePullError):
                safe_output(self.root / "output", (name,))

    def test_normalization_and_case_collisions(self):
        self.assertEqual(normalized_name("FILE.TXT;1", True), "FILE.TXT")
        self.assertEqual(normalized_name('a?b*<>|". '), "a_b_____")
        allocator = NameAllocator(["cuepull-manifest.json"])
        self.assertEqual(allocator.allocate("ReadMe.txt"), "ReadMe.txt")
        self.assertEqual(allocator.allocate("README.TXT"), "README--2.TXT")
        self.assertEqual(allocator.allocate("cuepull-manifest.json"), "cuepull-manifest--2.json")
        self.assertLessEqual(len(normalized_name("a" * 300)), 100)

    def test_symlink_output_and_cue_rejected(self):
        outside = self.root / "elsewhere"
        outside.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit symlink creation")
        with self.assertRaises(CuePullError):
            safe_output(link, ("file.txt",))
        (_, cue) = self.image(make_iso())
        cue_link = self.root / "linked.cue"
        cue_link.symlink_to(cue)
        with self.assertRaises(CuePullError):
            parse_cue(cue_link)


class FilesystemTests(Fixture):
    def test_duplicate_directory_identifiers_refused(self):
        iso = pycdlib.PyCdlib()
        iso.new(interchange_level=3)
        for name in ("FIRST.TXT;1", "OTHER.TXT;1"):
            iso.add_fp(io.BytesIO(b"test"), 4, iso_path="/" + name)
        output = io.BytesIO()
        iso.write_fp(output)
        iso.close()
        data = bytearray(output.getvalue())
        root = int.from_bytes(data[16 * 2048 + 158:16 * 2048 + 162], "little")
        at = root * 2048
        at += data[at]
        at += data[at]
        next_record = at + data[at]
        data[next_record + 33:next_record + 44] = data[at + 33:at + 44]
        _, cue = self.image(data, "MODE1/2048")
        with self.assertRaises((CuePullError, pycdlib.pycdlibexception.PyCdlibException)):
            Disc(cue)

    def test_extract_and_source_extent_verification_all_layouts(self):
        payload = bytes(range(256)) * 1025 + b"last"
        for n, mode in enumerate(("MODE1/2048", "MODE1/2352", "MODE2/2352", "MODE2/2336")):
            image, cue = self.image(make_iso(payload), mode, 3)
            before = image.read_bytes()
            with Disc(cue) as disc:
                dest = self.root / f"out{n}"
                manifest = extract(disc, dest)
                self.assertEqual((dest / "HELLO.TXT").read_bytes(), payload)
                self.assertEqual(manifest["completed_bytes"], len(payload))
                self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertEqual(verify(disc, dest)["verified_files"], 1)
                with self.assertRaises(CuePullError):
                    extract(disc, dest)
            self.assertEqual(before, image.read_bytes())

    def test_verify_detects_content_and_length_damage(self):
        _, cue = self.image(make_iso(b"a" * 10000))
        with Disc(cue) as disc:
            dest = self.root / "out"
            extract(disc, dest)
            path = dest / "HELLO.TXT"
            path.write_bytes(b"b" + b"a" * 9999)
            with self.assertRaises(CuePullError):
                verify(disc, dest)
            path.write_bytes(b"a")
            with self.assertRaises(CuePullError):
                verify(disc, dest)

    def test_joliet(self):
        _, cue = self.image(make_iso(joliet=True))
        with Disc(cue) as disc:
            self.assertEqual(disc.namespace, "joliet")
            extract(disc, self.root / "out")
        self.assertTrue((self.root / "out" / "Hello.txt").exists())

    def test_rock_ridge_symlinks_refused(self):
        _, cue = self.image(make_iso(symlink=True))
        with self.assertRaises(CuePullError):
            Disc(cue)

    def test_corrupt_descriptors(self):
        data = make_iso()
        for location in (16 * 2048 + 1, 16 * 2048 + 84, 17 * 2048 + 1):
            corrupted = bytearray(data)
            corrupted[location] ^= 1
            _, cue = self.image(corrupted, "MODE1/2048")
            with self.subTest(location=location), self.assertRaises(CuePullError):
                Disc(cue)

    def test_out_of_volume_extent(self):
        data = bytearray(make_iso())
        root = int.from_bytes(data[16 * 2048 + 158:16 * 2048 + 162], "little")
        at = root * 2048
        at += data[at]  # dot
        at += data[at]  # dotdot
        huge = 1000000
        data[at + 2:at + 10] = huge.to_bytes(4, "little") + huge.to_bytes(4, "big")
        _, cue = self.image(data, "MODE1/2048")
        with self.assertRaises((CuePullError, pycdlib.pycdlibexception.PyCdlibException)):
            Disc(cue)


class RecoveryTests(Fixture):
    def test_missing_cue_all_layouts_and_nonzero_start(self):
        data = make_iso(b"Recovered without a CUE")
        for mode in ("MODE1/2048", "MODE1/2352", "MODE2/2352", "MODE2/2336"):
            for start in (0, 150):
                image, cue = self.image(data, mode, start)
                cue.unlink()
                with self.subTest(mode=mode, start=start), Disc(image) as disc:
                    self.assertEqual(disc.track.start, start)
                    self.assertEqual(disc.track.mode, mode)
                    self.assertTrue(disc.inferred)
                    self.assertEqual(disc.summary()["file_count"], 1)

    def test_ambiguous_volumes_require_override(self):
        data = make_iso()
        image, _ = self.image(data + data, "MODE1/2048")
        with self.assertRaises(AmbiguousImageError) as exc:
            Disc(image)
        self.assertEqual(len(exc.exception.candidates), 2)
        with Disc(image, layout="MODE1/2048", start_sector=len(data) // 2048) as disc:
            self.assertEqual(disc.track.start, len(data) // 2048)

    def test_false_positive_signature_and_garbage(self):
        data = bytearray(64 * 2048)
        data[16 * 2048:16 * 2048 + 7] = b"\x01CD001\x01"
        image, _ = self.image(data, "MODE1/2048")
        with self.assertRaises(CuePullError):
            Disc(image)
        image.write_bytes(b"\0" * 100000)
        with self.assertRaises(CuePullError):
            Disc(image)

    def test_valid_descriptors_but_invalid_root(self):
        data = bytearray(make_iso())
        root = int.from_bytes(data[16 * 2048 + 158:16 * 2048 + 162], "little")
        data[root * 2048:(root + 1) * 2048] = b"\0" * 2048
        image, _ = self.image(data, "MODE1/2048")
        with self.assertRaises(CuePullError):
            Disc(image)

    def test_scan_limit_and_override_validation(self):
        image, _ = self.image(make_iso(), "MODE1/2048", 600)
        with self.assertRaises(CuePullError):
            Disc(image, scan_mib=1)
        with Disc(image, layout="MODE1/2048", start_sector=600):
            pass
        for kwargs in ({"layout": "AUDIO"}, {"start_sector": 1}, {"scan_mib": 0},
                       {"layout": "MODE1/2048", "start_sector": -1}):
            with self.assertRaises(CuePullError):
                Disc(image, **kwargs)


class GuiTests(Fixture):
    def test_simple_gui_picker_extract_flow(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            window = tk.Tk()
            window.withdraw()
        except Exception as exc:
            self.skipTest(f"Tk/display unavailable: {exc}")
        from cuepull.gui import main
        image, cue = self.image(make_iso(b"GUI extraction"))
        found = {}

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def exercise():
            buttons = [w for w in descendants(window) if w.winfo_class() == "TButton"]
            browse = [b for b in buttons if b.cget("text").startswith("Browse")]
            action = [b for b in buttons if b.cget("text") == "Extract"][0]
            self.assertEqual(len(browse), 2)
            browse[0].invoke()
            browse[1].invoke()
            action.invoke()
            dest = self.root / "disc"
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                window.update()
                manifest = dest / "cuepull-manifest.json"
                if manifest.exists():
                    try:
                        if json.loads(manifest.read_text())["status"] == "complete":
                            found["complete"] = True
                            break
                    except json.JSONDecodeError:
                        pass
                time.sleep(0.01)
            self.assertTrue(found.get("complete"))
            self.assertEqual((dest / "HELLO.TXT").read_bytes(), b"GUI extraction")

        try:
            with mock.patch.object(tk, "Tk", return_value=window), \
                 mock.patch.object(filedialog, "askopenfilename", return_value=str(image)), \
                 mock.patch.object(filedialog, "askdirectory", return_value=str(self.root)), \
                 mock.patch.object(window, "mainloop", side_effect=exercise):
                self.assertEqual(main(), 0)
        finally:
            window.destroy()


@unittest.skipUnless(os.environ.get("CUEPULL_REAL_CUE"), "Optional local real-image check")
class RealImageTests(unittest.TestCase):
    def test_real_cue_and_missing_cue_inventory_match(self):
        cue = Path(os.environ["CUEPULL_REAL_CUE"])
        with Disc(cue) as original, Disc(parse_cue(cue)[0].image) as recovered:
            self.assertEqual([e.summary() for e in original.entries],
                             [e.summary() for e in recovered.entries])
            self.assertEqual(original.summary()["data_bytes"], recovered.summary()["data_bytes"])

    def test_real_extracted_source_extents(self):
        destination = os.environ.get("CUEPULL_REAL_OUTPUT")
        if not destination:
            self.skipTest("Set CUEPULL_REAL_OUTPUT after extraction")
        with Disc(Path(os.environ["CUEPULL_REAL_CUE"])) as disc:
            result = verify(disc, Path(destination))
            self.assertEqual(result["verified_files"], disc.summary()["file_count"])


if __name__ == "__main__":
    unittest.main()
