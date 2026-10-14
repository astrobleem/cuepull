"""Black-box tests of the compiled native CLI; stdlib-only fixtures.

Run: python test_native.py --exe PATH [--real-image CUE --real-output DIR]
The optional oracle test uses the openly licensed PyCdlib parser when present.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

parser = argparse.ArgumentParser()
parser.add_argument('--exe', required=True)
parser.add_argument('--real-image')
parser.add_argument('--real-output')
options, unittest_args = parser.parse_known_args()
EXE = str(Path(options.exe).resolve())
SYNC = b'\0' + b'\xff' * 10 + b'\0'
MODES = ('MODE1/2048', 'MODE1/2352', 'MODE2/2352', 'MODE2/2336')


def both32(value):
    return struct.pack('<I', value) + struct.pack('>I', value)


def both16(value):
    return struct.pack('<H', value) + struct.pack('>H', value)


def rec(name, extent, size, flags=0, extra=b''):
    if isinstance(name, str):
        name = name.encode('ascii')
    n = 33 + len(name) + (len(name) % 2 == 0) + len(extra)
    data = bytearray(n)
    data[0] = n
    data[2:10] = both32(extent)
    data[10:18] = both32(size)
    data[18:25] = bytes([126, 10, 14, 0, 0, 0, 0])
    data[25] = flags
    data[28:32] = both16(1)
    data[32] = len(name)
    data[33:33 + len(name)] = name
    if extra:
        data[-len(extra):] = extra
    return data


def iso(files=None, extra=b'', directory=False):
    """Independent fixed-layout fixture: PVD16, terminator17, root20."""
    files = files or [('HELLO.TXT;1', bytes(range(256)) * 259 + b'last')]
    ext = 23
    records = []
    chunks = []
    for name, content in files:
        records.append(rec(name, ext, len(content)))
        chunks.append((ext, content))
        ext += (len(content) + 2047) // 2048
    blocks = max(32, ext)
    data = bytearray(blocks * 2048)
    pvd = bytearray(2048)
    pvd[:7] = b'\x01CD001\x01'
    pvd[40:72] = b'NATIVE_TEST'.ljust(32, b' ')
    pvd[80:88] = both32(blocks)
    pvd[120:124] = both16(1)
    pvd[124:128] = both16(1)
    pvd[128:132] = both16(2048)
    root = rec(b'\0', 20, 2048, 2)
    pvd[156:156 + len(root)] = root
    data[16 * 2048:17 * 2048] = pvd
    data[17 * 2048:17 * 2048 + 7] = b'\xffCD001\x01'
    dot = rec(b'\0', 20, 2048, 2, extra)
    dotdot = rec(b'\1', 20, 2048, 2)
    root_records = dot + dotdot
    if directory:
        root_records += rec('SUBDIR', 21, 2048, 2)
        child = rec(b'\0', 21, 2048, 2) + rec(b'\1', 20, 2048, 2) + b''.join(records)
        data[21 * 2048:21 * 2048 + len(child)] = child
    else:
        root_records += b''.join(records)
    assert len(root_records) < 2048
    data[20 * 2048:20 * 2048 + len(root_records)] = root_records
    for extent, content in chunks:
        data[extent * 2048:extent * 2048 + len(content)] = content
    return data


def wrap(data, mode):
    if mode == 'MODE1/2048':
        return data
    out = bytearray()
    for i in range(0, len(data), 2048):
        payload = data[i:i + 2048].ljust(2048, b'\0')
        if mode == 'MODE1/2352':
            out += SYNC + b'\0\x02\0\x01' + payload + bytes(288)
        elif mode == 'MODE2/2352':
            out += SYNC + b'\0\x02\0\x02' + bytes(8) + payload + bytes(280)
        else:
            out += bytes(8) + payload + bytes(280)
    return out


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, success=True):
        result = subprocess.run([EXE, *map(str, args)], capture_output=True,
                                text=True, timeout=45)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def image(self, data=None, mode='MODE1/2352', prefix=0):
        data = iso() if data is None else data
        width = 2048 if mode == 'MODE1/2048' else 2336 if mode == 'MODE2/2336' else 2352
        image = self.root / 'disc.bin'
        image.write_bytes(bytes(prefix * width) + wrap(data, mode))
        m, rest = divmod(prefix, 60 * 75)
        s, f = divmod(rest, 75)
        cue = self.root / 'disc.cue'
        cue.write_text(f'FILE "disc.bin" BINARY\nTRACK 01 {mode}\nINDEX 01 {m:02}:{s:02}:{f:02}\n')
        return image, cue

    def test_modes_cue_and_missing_cue_offsets_content(self):
        payload = bytes(range(256)) * 1025 + b'last'
        for mode in MODES:
            for prefix in (0, 150):
                for use_cue in (False, True):
                    with self.subTest(mode=mode, prefix=prefix, cue=use_cue):
                        image, cue = self.image(iso([('HELLO.TXT;1', payload)], directory=True), mode, prefix)
                        source_before = hashlib.sha256(image.read_bytes()).digest()
                        dest = self.root / f'out-{MODES.index(mode)}-{prefix}-{use_cue}'
                        self.run_cli('extract', cue if use_cue else image, dest)
                        self.assertEqual((dest / 'SUBDIR' / 'HELLO.TXT').read_bytes(), payload)
                        self.assertEqual(hashlib.sha256(image.read_bytes()).digest(), source_before)
                        self.assertTrue((dest / 'cuepull32-manifest.tsv').read_text().endswith('# COMPLETE\n'))

    def test_index00_virtual_pregap_audio(self):
        image, cue = self.image()
        sectors = image.stat().st_size // 2352
        image.write_bytes(image.read_bytes() + bytes(100 * 2352))
        cue.write_text(f'FILE "disc.bin" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00\n'
                       f'TRACK 02 AUDIO\nINDEX 00 00:00:{sectors:02}\nINDEX 01 00:00:{sectors+2:02}\n'
                       'TRACK 03 AUDIO\nPREGAP 00:02:00\nINDEX 01 00:01:00\n')
        # Standard fixture is 56 sectors; still <75 for the stored gap.
        result = self.run_cli('inspect', cue)
        self.assertIn(f'end={sectors}', result.stdout)
        self.assertIn('Audio tracks skipped: 2', result.stdout)
        self.assertIn('virtual_pregap=150', result.stdout)

    def test_malformed_and_external_cue(self):
        image, cue = self.image()
        for name in ('../disc.bin', '/disc.bin', r'C:\disc.bin', r'\\server\disc.bin',
                     'NUL.bin', 'disc.bin:stream', 'other.bin'):
            with self.subTest(name=name):
                cue.write_text(f'FILE "{name}" BINARY\nTRACK 01 MODE1/2352\nINDEX 01 00:00:00')
                self.run_cli('inspect', cue, success=False)
        for tail in ('TRACK 01 MODE1/2352', 'TRACK 01 CDG\nINDEX 01 00:00:00',
                     'TRACK 01 MODE1/2352\nINDEX 01 00:00:75',
                     'TRACK 01 MODE1/2352\nINDEX 01 00:00:00\nINDEX 01 00:00:01',
                     'REM SESSION 2', 'TRACK 01 MODE1/2352\nINDEX 01 99999999999999999999:00:00',
                     'TRACK 01 MODE1/2048\nINDEX 01 00:00:00\nTRACK 02 AUDIO\nINDEX 01 00:00:10'):
            cue.write_text('FILE "disc.bin" BINARY\n' + tail)
            self.run_cli('inspect', cue, success=False)
        cue.write_bytes(b'FILE "disc.bin" BINARY\0\n')
        self.run_cli('inspect', cue, success=False)

    def test_bad_filesystem_boundaries_flags_and_endianness(self):
        data = iso()
        root_at = 20 * 2048
        file_at = root_at + data[root_at] + data[root_at + data[root_at]]
        for mutate in ('endian', 'extent', 'size', 'record', 'multi', 'interleave', 'cycle', 'terminator'):
            bad = bytearray(data)
            if mutate == 'endian': bad[16 * 2048 + 84] ^= 1
            if mutate == 'extent': bad[file_at + 2:file_at + 10] = both32(0xffffffff)
            if mutate == 'size': bad[file_at + 10:file_at + 18] = both32(0xffffffff)
            if mutate == 'record': bad[file_at] = 12
            if mutate == 'multi': bad[file_at + 25] |= 128
            if mutate == 'interleave': bad[file_at + 26] = 1
            if mutate == 'cycle': bad[file_at + 25] = 2; bad[file_at + 2:file_at + 10] = both32(20); bad[file_at + 10:file_at + 18] = both32(2048)
            if mutate == 'terminator': bad[17 * 2048 + 1] = 0
            image, cue = self.image(bad)
            dest = self.root / mutate
            self.run_cli('extract', cue, dest, success=False)
            self.assertFalse(dest.exists())

    def test_truncated_sector_and_volume(self):
        image, cue = self.image()
        original = image.read_bytes()
        for missing in (1, 2352, 30 * 2352):
            image.write_bytes(original[:-missing])
            self.run_cli('extract', cue, self.root / 'bad', success=False)
            self.assertFalse((self.root / 'bad').exists())

    def test_sync_mode_form2_subheaders(self):
        for mode, byte in (('MODE1/2352', 0), ('MODE1/2352', 15),
                           ('MODE2/2352', 18), ('MODE2/2352', 20), ('MODE2/2336', 2)):
            image, cue = self.image(mode=mode)
            width = 2336 if mode.endswith('/2336') else 2352
            bad = bytearray(image.read_bytes())
            at = 16 * width
            bad[at + byte] = 0x20 if byte in (18, 2) else 7
            if byte == 18: bad[at + 22] = 0x20
            if byte == 2: bad[at + 6] = 0x20
            image.write_bytes(bad)
            self.run_cli('inspect', cue, success=False)

    def test_path_traversal_devices_and_version_normalization(self):
        for name in ('..', '.', '../EVIL;1', 'C:EVIL;1', r'A\EVIL;1', 'NUL.TXT;1', 'COM1.;1', 'CON;1'):
            image, cue = self.image(iso([(name, b'bad')]))
            self.run_cli('extract', cue, self.root / 'unsafe', success=False)
            self.assertFalse((self.root / 'unsafe').exists())
        files = [('ReadMe.TXT;1', b'one'), ('README.TXT;1', b'two'),
                 ('A?B.TXT;1', b'three'), ('cuepull32-manifest.tsv;1', b'four')]
        image, cue = self.image(iso(files))
        dest = self.root / 'good'
        self.run_cli('extract', cue, dest)
        self.assertEqual((dest / 'ReadMe.TXT').read_bytes(), b'one')
        self.assertEqual((dest / 'README.TXT--2').read_bytes(), b'two')
        self.assertEqual((dest / 'A_B.TXT').read_bytes(), b'three')
        self.assertEqual((dest / 'cuepull32-manifest.tsv--2').read_bytes(), b'four')
        self.assertIn('413f422e5458543b31', (dest / 'cuepull32-manifest.tsv').read_text())

    def test_duplicate_identifier(self):
        image, cue = self.image(iso([('A.TXT;1', b'a'), ('A.TXT;1', b'b')]))
        self.run_cli('inspect', cue, success=False)

    def test_overwrite_automatic_suffix_and_verification_damage(self):
        image, cue = self.image()
        dest = self.root / 'disc'
        dest.mkdir()
        (dest / 'sentinel').write_text('keep')
        self.run_cli('extract', cue, dest, success=False)
        self.run_cli('extract', image)
        auto = self.root / 'disc (2)'
        self.assertTrue(auto.is_dir())
        self.assertEqual((dest / 'sentinel').read_text(), 'keep')
        self.run_cli('verify', cue, auto)
        f = auto / 'HELLO.TXT'
        d = bytearray(f.read_bytes()); d[0] ^= 1; f.write_bytes(d)
        self.run_cli('verify', cue, auto, success=False)
        f.write_bytes(b'')
        self.run_cli('verify', cue, auto, success=False)

    def test_ambiguous_volume_and_explicit_override_overflow(self):
        data = iso()
        image, cue = self.image(data + data, 'MODE1/2048')
        self.run_cli('inspect', image, success=False)
        self.run_cli('inspect', image, '--layout', 'MODE1/2048', '--start', '0')
        self.run_cli('inspect', image, '--layout', 'MODE1/2048', '--start', str(2**64), success=False)
        self.run_cli('inspect', image, '--layout', 'MODE1/2048', '--start', str(2**64 - 1), success=False)

    def test_susp_rejected_opaque_metadata_allowed(self):
        image, cue = self.image(iso(extra=b'SP\x07\x01\xbe\xef\x00'))
        self.run_cli('extract', cue, self.root / 'rr', success=False)
        self.assertFalse((self.root / 'rr').exists())
        image, cue = self.image(iso(extra=b'BA\x06TEXT' + b'hscd\x01\0\0'))
        self.run_cli('inspect', cue)

    def test_output_links_and_source_links(self):
        image, cue = self.image()
        outside = self.root / 'outside'; outside.mkdir()
        link = self.root / 'link'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
            if result.returncode: self.skipTest('Host cannot create a test junction')
        else:
            link.symlink_to(outside, target_is_directory=True)
        self.run_cli('extract', cue, link / 'out', success=False)
        self.assertFalse((outside / 'out').exists())
        # Source ancestor link is rejected as well.
        (outside / 'disc.bin').write_bytes(image.read_bytes())
        self.run_cli('inspect', link / 'disc.bin', success=False)

    def test_path_limit_preflight(self):
        image, cue = self.image(iso([('A' * 180 + ';1', b'x')], directory=True))
        dest = self.root / ('z' * 170)
        self.run_cli('extract', cue, dest, success=False)
        self.assertFalse(dest.exists())

    def test_sparse_image_offset_above_4gb(self):
        data = iso([('LARGE.TXT;1', b'64-bit image offset')])
        image = self.root / 'large.bin'
        start = 2200000
        with image.open('wb') as stream:
            if os.name == 'nt':
                import ctypes
                import msvcrt
                from ctypes import wintypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                    wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
                returned = wintypes.DWORD()
                if not kernel.DeviceIoControl(msvcrt.get_osfhandle(stream.fileno()),
                        0x900c4, None, 0, None, 0, ctypes.byref(returned), None):
                    self.skipTest('Filesystem lacks sparse test-file support')
            stream.seek(start * 2352)
            stream.write(wrap(data, 'MODE1/2352'))
        dest = self.root / 'large-out'
        self.run_cli('extract', image, dest, '--layout', 'MODE1/2352', '--start', start)
        self.assertEqual((dest / 'LARGE.TXT').read_bytes(), b'64-bit image offset')

    def test_pycdlib_oracle(self):
        try:
            import pycdlib
        except ImportError:
            self.skipTest('Install repository requirements for independent PyCdlib oracle')
        payload = b'Parser oracle\n' * 12345
        parser = pycdlib.PyCdlib()
        parser.new(interchange_level=3, vol_ident='PYCDLIB_ORACLE')
        parser.add_fp(io.BytesIO(payload), len(payload), iso_path='/ORACLE.TXT;1')
        output = io.BytesIO(); parser.write_fp(output); parser.close()
        image, cue = self.image(output.getvalue())
        dest = self.root / 'oracle'
        self.run_cli('extract', cue, dest)
        self.assertEqual((dest / 'ORACLE.TXT').read_bytes(), payload)

    @unittest.skipUnless(options.real_image and options.real_output, 'private real-disc integration is opt-in')
    def test_real_source_extent_verification(self):
        result = self.run_cli('verify', options.real_image, options.real_output)
        self.assertIn('Files: 2329', result.stdout)
        self.assertIn('File bytes: 330078121', result.stdout)


unittest.main(argv=[sys.argv[0], *unittest_args])
