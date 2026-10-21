# Public source and binary releases

The public release is rebuilt on native Ubuntu 22.04 and Windows builders with
Python 3.14.8 and exact versions in `packaging/requirements-release.txt`.
Local CachyOS bundles and their historical license inventory are development
artifacts, not the portable public binaries.

## Version 0.6 runtime audit

Pillow, cryptography and CFFI are runtime dependencies with exact version pins.
Each builder records its installed native extensions and their authenticated
wheel RECORD hashes in `runtime-wheels.json`. The source job merges both
platform inventories; it does not assume Windows uses the Linux codec versions.
Original Rust/CFFI/OpenSSL notices and complete matching sources supplement
the original wheel notice corpus. The Windows Pillow codec audit is described
in [runtime-v06-audit.md](runtime-v06-audit.md).

Linux builds Pillow and CFFI from their pinned source distributions against
Ubuntu development libraries. The collected system ELFs retain the existing
exact dpkg source-package audit. Only explicitly identified, hash-matching
native extensions receive a corresponding Python-package source provider;
unknown sibling libraries still fail. The unused AVIF plugin/native extension
is excluded from the bundle. Source archives are acquired and checked in the
cloud, with verified downloads cached for retries.

The 0.6 package gate requires named password/compression/form PDF tests and
GUI workflows, including reduced motion, from the actual frozen executable.
A local development ZIP or source-only test is not a public release acceptance.

## Release materials

The app release contains only the two native onedir archives and their
SHA256SUMS. GitHub additionally supplies its automatic source-code downloads.
A versioned sources-and-reports TAR and its JSON inventory are published in the
separate `source-archives` release. The app release links directly to that TAR.
It retains the matching application source, dependency source archives,
build/test records and the original complete checksum inventory. Keep this
archive available while its corresponding binaries are available.
The matching application version tag includes build scripts, dependency pins
and this document. The `source-archives` tag is only an archive-hosting anchor;
use the versioned TAR for the corresponding app sources.

The LGPL library source and rebuild information are part of the distribution,
not replaced by a Qt homepage link. This release supplies complete, unmodified
upstream Qt 6.11.2 sources inside the sources archive, including PDFium/Chromium and
third-party sources. The official archive size is 1,019,661,552 bytes, below
GitHub's per-asset limit. Its official SHA-256 is
`6dcfbca271d76a6502741a2c0dc6fc98ef7dd0b7b4cfd0abcebb285a86a26f33`.
The source job also collects exact-version PyQt6, SIP-runtime, pypdf,
PyInstaller, hooks, Pillow, cryptography and CFFI source distributions, plus
the matching bundled native/Rust dependencies. CPython sources are an additional
rebuild convenience; the PSF license does not require a source offer.

The manifest distinguishes upstream-provided digests (Qt/PyPI) from CPython's
digest calculated during its HTTPS download. The latter is filled into the
downloaded source manifest, never guessed. CI downloads and bundles the large archives;
the app and the development host do not.

## Notice and source gates

Run the collector in the clean native build environment after tests and removal
of the test-only ReportLab dependency (Pillow remains part of the runtime):

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
sizes. `source-manifest.json` is written only after success. The publisher validates every downloaded source against its manifest, preserves
the already-compressed archives in an uncompressed TAR, and reads back every
member. The TAR must remain below 2,000,000,000 bytes; a future oversized set
fails instead of publishing incomplete sources. Each app version has its own
TAR and JSON inventory. Publishing depends on both successful native builds/tests
and successful source acquisition. The source pair is uploaded, its server
SHA-256 and size verified and public availability checked before app publication. The notice manifest's `complete` field describes notice collection;
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
patch archives inside the versioned source bundle. This also covers LGPL native
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

## Dateinamen der Quellarchive

GitHub verändert bestimmte Sonderzeichen in Downloadnamen.
`source-manifest.json` nennt den tatsächlichen Downloadnamen unter `filename`
und bei einer Umbenennung den ursprünglichen Namen unter `upstream_filename`.
Inhalt und SHA-256 bleiben unverändert. Vor `dpkg-source -x` die betreffenden
Ubuntu-Quellarchive wieder unter ihrem ursprünglichen Namen ablegen, damit
die Dateiverweise in der unveränderten `.dsc` passen.

## Compact publication and migration

`packaging/release_layout.py` prepares the two complementary layouts.
`packaging/publish_release.py` is shared by the release, recovery and one-off
consolidation workflows. Recovery loads the current publisher in a separate
checkout while continuing to verify the original app tag and successful native
build run. The source archive release points to the repository's oldest commit
and is explicitly excluded from Latest; app tags are never changed.
If the Actions token cannot create that older hosting tag, a repository owner
can create it once and initialize the managed draft release before retrying the
workflow. Uploads and verification still run in CI before any app asset cleanup.

For existing app releases the cloud runner downloads and verifies only their
non-native materials. Original native download IDs and bytes stay unchanged.
All extras, including the old complete SHA256SUMS, are retained before cleanup.
The public JSON receipt records every original asset ID and digest. After the
source pair is publicly verified, the checksum asset is replaced by exact ID
and obsolete extras are removed by exact ID after another identity check.
Foreign replacements are preserved. A partial cleanup can resume from the
verified public receipt without downloading large sources again.
