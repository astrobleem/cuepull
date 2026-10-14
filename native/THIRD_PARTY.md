# Native candidate notices

The authored CuePull32 C source, frontend, tests and build scripts use the parent
project's MIT license. No third-party filesystem parser source is incorporated.
The reader implements a bounded primary ISO9660 subset using ECMA-119, and the
limitations are explicit in README.md.

The private Windows candidate is built with the already installed **Open Watcom
1.9** and statically includes its **unmodified C runtime and startup code**.
That code is governed by the **Sybase Open Watcom Public License 1.0**, not MIT.
The full installed license is reproduced unchanged at
`licenses/OpenWatcom-1.9.txt`. Runtime source is available under that license in
the official [Open Watcom 1.9 source archive](https://openwatcom.org/ftp/source/open_watcom_1.9.0-src.zip).
The binary includes this notice through CLI help and GUI About. We do not claim
a blanket MIT license for the linked Watcom runtime or a runtime exception.
Use of Open Watcom Covered Code constitutes acceptance of its license; do not
use that Covered Code if you do not accept all its terms. CuePull32 does not
modify the compiler/runtime sources or bundle the compiler itself.

Tests optionally generate a fixture with **PyCdlib 1.14.0**, LGPL-2.1, using the
parent repository's reproducible requirements lock. PyCdlib is an independent
test oracle, not linked into or required by CuePull32. No extracted media or
third-party disc programs are part of the application or its source archive.

The portable CLI can instead be built with GCC/Clang/MSVC; those compiler and
runtime licenses depend on the chosen build toolchain. A modern build does not
establish compatibility with XP/98. Keep that qualification separate.
