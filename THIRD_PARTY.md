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
