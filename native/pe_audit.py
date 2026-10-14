"""Small stdlib-only PE/import audit; structural evidence, not OS qualification."""
import argparse
import hashlib
import json
from pathlib import Path
import struct


def audit(path):
    data = Path(path).read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[:2] != b'MZ' or data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Not a PE executable')
    opt = pe + 24
    if struct.unpack_from('<H', data, opt)[0] != 0x10b:
        raise ValueError('Legacy candidate must be PE32')
    n = struct.unpack_from('<H', data, pe + 6)[0]
    size = struct.unpack_from('<H', data, pe + 20)[0]
    sections = []
    for i in range(n):
        at = opt + size + i * 40
        virtual_size, virtual, raw_size, raw = struct.unpack_from('<IIII', data, at + 8)
        sections.append((virtual, max(virtual_size, raw_size), raw))
    def offset(rva):
        for virtual, size, raw in sections:
            if virtual <= rva < virtual + size:
                return raw + rva - virtual
        raise ValueError('RVA outside sections')
    def string(rva):
        at = offset(rva)
        return data[at:data.index(b'\0', at)].decode('ascii')
    imports = {}
    at = offset(struct.unpack_from('<I', data, opt + 104)[0])
    while any(data[at:at + 20]):
        original, _, _, name, thunk = struct.unpack_from('<IIIII', data, at)
        at += 20
        dll = string(name)
        imports[dll] = []
        index = offset(original or thunk)
        while True:
            item = struct.unpack_from('<I', data, index)[0]; index += 4
            if not item: break
            imports[dll].append('ordinal:' + str(item & 0xffff) if item & 0x80000000 else string(item + 2))
    assert struct.unpack_from('<H', data, pe + 4)[0] == 0x14c
    assert struct.unpack_from('<HH', data, opt + 48) == (4, 0)
    assert set(imports) <= {'KERNEL32.DLL', 'USER32.DLL', 'GDI32.DLL', 'SHELL32.DLL', 'COMDLG32.DLL'}
    assert b'Open Watcom 1.9 C runtime' in data
    return {'file': Path(path).name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'machine': 'i386', 'subsystem_version': '4.0',
            'subsystem': struct.unpack_from('<H', data, opt + 68)[0], 'imports': imports,
            'xp_98_qualified': False,
            'note': 'Static compiler runtime imports some W entry points. Guest runtime testing is still required.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executables', nargs='+')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = [audit(path) for path in args.executables]
    output = json.dumps(report, indent=2) + '\n'
    if args.report: args.report.write_text(output)
    print(output)
