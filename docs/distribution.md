# Public source and binary releases

The public release is rebuilt on native Ubuntu 22.04 and Windows builders with
Python 3.14.8 and exact versions in `packaging/requirements-release.txt`.
Local CachyOS bundles and their historical license inventory are development
artifacts, not the portable public binaries.

## Release materials

Publish the matching application Git tag and source archive, both native onedir
archives, checksums, build/test records, and dependency source archives in the
same GitHub release. Keep sources available while their binaries are available.
The source tag includes build scripts, dependency pins and this document.

The LGPL library source and rebuild information are part of the distribution,
not replaced by a Qt homepage link. This release supplies complete, unmodified
upstream Qt 6.11.2 sources as a separate asset, including PDFium/Chromium and
third-party sources. The official archive size is 1,019,661,552 bytes, below
GitHub's per-asset limit. Its official SHA-256 is
`6dcfbca271d76a6502741a2c0dc6fc98ef7dd0b7b4cfd0abcebb285a86a26f33`.
The source job also collects exact-version PyQt6, SIP-runtime, pypdf,
PyInstaller and hooks source distributions. CPython sources are an additional
rebuild convenience; the PSF license does not require a source offer.

The manifest distinguishes upstream-provided digests (Qt/PyPI) from CPython's
digest calculated during its HTTPS download. The latter is filled into the
downloaded source manifest, never guessed. CI downloads the large archives;
the app and the development host do not.

## Notice and source gates

Run the collector in the clean native build environment after tests and removal
of the test-only ReportLab/Pillow dependencies:

```sh
python packaging/collect_notices.py --output build/release-notices
```

It checks installed release versions, required distribution license files,
the reviewed Qt notice corpus and hashes, all nine QtPdf attribution components,
and the explicit PDFium/Foxit copyright. It collects CPython and OpenSSL notices
matching the running interpreter. Linux requires the audited dpkg builder and
includes native-package copyright texts and versions. Missing notices,
unsupported versions, altered locked files and unavailable required upstream
texts fail; no success manifest remains after failed collection.

The output contract is `manifest.json` with `schema_version: 1`,
`complete: true`, `versions`, `files` (relative path to SHA-256), and
`source_archives` (component/version/filename/URL/hash/size). Paths must remain
inside the notice directory. Packaging consumes only this small directory,
never the large dependency source archives.

Acquire sources separately on the Ubuntu CI builder:

```sh
python packaging/collect_notices.py --output build/source-notices --sources-output release-assets/dependency-sources
```

Downloads use temporary `.partial` names and verify known upstream digests and
sizes. `source-manifest.json` is written only after success. Upload individual
source archives, not one potentially oversized combined archive. Publishing
depends on both successful native builds/tests and successful source acquisition
and upload. The notice manifest's `complete` field describes notice collection;
it does not assert that release assets already exist.

After freezing, verify the included Qt module set:

```sh
python packaging/collect_notices.py --output build/release-notices --native-toc build/PDFWorkbench/Analysis-00.toc
python packaging/collect_notices.py --output build/release-notices --verify-bundle dist/PDFWorkbench
```

The first command is Linux-only. It maps the actual collected ELF inputs to
exact Ubuntu binary/source package versions, resolves their complete source
file set through Ubuntu's official Launchpad archive API, and checks the
canonical `.dsc` descriptor's package/version and SHA-256 source checksums.
It adds `native-sources.json` and those source archive records to the notice
manifest. Copy the updated notice directory into the frozen bundle before
testing and packaging it.

The source job receives only the small `native-sources.json` artifact, then
adds `--native-sources path/to/native-sources.json` to its source-acquisition
command. It downloads the exact Ubuntu `.dsc`, original source and Debian/Ubuntu
patch archives as separate release assets. This also covers LGPL native
dependencies such as GLib instead of treating every bundled `.so` as an
unbundled operating-system library. Unknown native providers fail the audit.
Superseded Ubuntu versions are resolved by exact version through Launchpad;
the job does not silently substitute the current apt version.

Unknown Qt modules fail the audit. Adding QtWebEngine, Multimedia, FFmpeg or
other unaudited modules requires reviewing their additional notices and source
obligations. Keep collected notices in the binary's `licenses` directory.

Three additional internal libraries are explicitly mapped to the supplied
QtBase 6.11.2 source: `EglFsKmsSupport` and `EglFsKmsGbmSupport` are the
EGLFS/KMS device-integration helpers, and `WlShellIntegration` is the Wayland
client shell helper. Their exact [KMS support CMake definition](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/eglfs/deviceintegration/eglfs_kms_support/CMakeLists.txt),
[GBM helper definition](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/eglfs/deviceintegration/eglfs_kms/CMakeLists.txt)
and [Wayland shell definition](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/wayland/plugins/shellintegration/wl-shell/CMakeLists.txt)
declare internal Qt modules. Their source headers offer LGPL-3.0/GPL terms
already included in the corpus. The shell helper's attribution points to
Wayland protocol 1.24.0, whose full MIT copyright/notice is already retained as
`qtwaylandcompositor-attribution-wayland-protocol.txt`. Its use by the platform
plugin is explicit in [QtBase's versioned attribution metadata](https://github.com/qt/qtbase/blob/v6.11.2/src/3rdparty/wayland/protocols/wayland/qt_attribution.json).
External libdrm, GBM and Wayland shared libraries remain subject to the native
package/source audit; this mapping does not exempt them.

The collector also retains actual installed Python runtime `LICENSE*`,
`COPYING*` and `NOTICE*` files from its installation prefix and standard-library
directory, in addition to upstream source notices. On Windows this preserves
the distributor-added Microsoft Distributable Code terms. Those terms apply
to the Microsoft runtime DLLs; they do not change the application source license.

## Rebuilding and substituting Qt

Application build instructions are the workflow and `packaging/build.py`;
dependencies are pinned. PyQt6's source distribution contains SIP interface files
and build configuration. Build modified bindings using compatible SIP and
PyQt-builder with the matching Qt installation's `qmake`, as described in
upstream PyQt instructions. Qt's source archive includes configure/CMake files
and build instructions. No local Qt/PyQt patches are applied: the application
freezer copies installed wheel libraries.

This is an extracted onedir application. Qt DLLs on Windows reside below
`_internal/PyQt6/Qt6/bin`; Linux shared objects reside below
`_internal/PyQt6/Qt6/lib`. Back up the original archive, close the app, and
replace libraries/plugins with interface-compatible modified builds, or rebuild
against them. Debugging and reverse engineering library modifications are
permitted. Qt is not encrypted, signature locked or statically hidden in the
application executable.

The collector is an evidence/completeness check for this pinned set. It does
not convert unrelated licenses into GPL or establish compliance for another
native runtime, added library or changed dependency version.

Primary sources: [QtPdf licensing](https://doc.qt.io/qt-6.11/qtpdf-licensing.html),
[Qt LGPL text](https://doc.qt.io/qt-6.11/lgpl.html),
[Qt source archive and metadata](https://download.qt.io/official_releases/qt/6.11/6.11.2/single/),
[PyQt-builder bundling documentation](https://pyqt-builder.readthedocs.io/en/stable/pyqtbundle.html),
[GPLv3 section 6](https://www.gnu.org/licenses/gpl-3.0.html#section6).
