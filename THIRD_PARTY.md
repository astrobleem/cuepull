# Third-party attribution

CuePull's original code is MIT licensed. Its ISO filesystem parser is
[PyCdlib 1.14.0](https://pypi.org/project/pycdlib/1.14.0/), by Chris Lalancette
and contributors, under the GNU Lesser General Public License, version 2.1.
PyCdlib remains under its own license; MIT does not relicense it.

- Upstream source: https://github.com/clalancette/pycdlib
- Documentation: https://clalancette.github.io/pycdlib/
- Source release: https://pypi.org/project/pycdlib/1.14.0/#files
- The local ready-to-run bundle includes unmodified Python source in
  `dependencies/pycdlib` and the upstream license at
  `licenses/pycdlib-LGPL-2.1.txt`.
- PyCdlib can be replaced by installing the pinned dependency into a Python
  environment and running `python -m cuepull`; this bypasses the bundled copy.

No Windows disc files, images, proprietary software, or disc-derived file
listings are included in the source/release archive. Synthetic tests generate
their own tiny ISO files at runtime.

Windows releases bundle the CPython runtime (PSF license), Tcl/Tk (their
upstream license terms), and the PyInstaller bootloader (GPL with its
distribution exception). Complete notices are in the portable folder's
`licenses` directory. PyInstaller and its build dependencies are pinned in
`build-requirements.txt` and installed only for building. See
https://pyinstaller.org/en/stable/license.html for the bootloader exception.

The Windows EXEs exclude PyCdlib from their frozen archives and dynamically
import its unmodified source from `dependencies/pycdlib`. This source is included
in every portable Windows ZIP and can be replaced independently of the EXEs.
The complete CuePull source and build instructions are included in `source`.
Windows drag/drop uses the operating system's User32/Shell32 APIs and adds no
external runtime package. Public screenshots use only generated synthetic data.
