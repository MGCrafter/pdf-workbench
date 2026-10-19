"""Collect version-locked notices; fail instead of producing incomplete releases.

Large corresponding-source archives are downloaded only with --sources-output.
This script uses the standard library and the installed release distributions.
"""
from __future__ import annotations

import argparse
import ast
from email.parser import Parser
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import ssl
import subprocess
import sys
import sysconfig
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
QT_VERSION = "6.11.2"
LOCK = ROOT / "packaging/notices-lock.json"
# This release does not include WebEngine, Multimedia, Bluetooth or Qt3D.
# New native Qt modules require an attribution/source audit before this list grows.
COVERED_QT_MODULES = frozenset("Core Gui Widgets Network DBus Pdf PdfWidgets Svg SvgWidgets OpenGL OpenGLWidgets PrintSupport Sql Xml Concurrent Qml QmlMeta QmlModels QmlWorkerScript QmlBuiltins Quick QuickControls2 QuickTemplates2 QuickWidgets ShaderTools VirtualKeyboard WaylandClient WaylandCompositor WaylandEglClientHwIntegration WaylandEglCompositorHwIntegration InputSupport XcbQpa EglFSDeviceIntegration".split())
# These are named internal QtBase modules in v6.11.2, not new third-party
# runtimes. CMake definitions and LGPL source headers are linked in
# docs/distribution.md. Their libdrm/GBM/Wayland dependencies still go through
# the separate exact-version Ubuntu native-source audit.
COVERED_QT_MODULES |= frozenset(("EglFsKmsGbmSupport", "EglFsKmsSupport", "WlShellIntegration"))


class IncompleteNotices(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in ("", ".", "..") for part in pure.parts) or "\\" in value or ":" in value:
        raise IncompleteNotices(f"Unsafe relative notice path: {value!r}")
    return Path(*pure.parts)


def fetch(url: str, limit: int = 16 * 1024 * 1024) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise IncompleteNotices(f"Source URL must use HTTPS: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "PDFWorkbench-release-notices/1"})
    with urllib.request.urlopen(request, timeout=90) as response:
        if urllib.parse.urlparse(response.url).scheme != "https":
            raise IncompleteNotices("Refusing insecure upstream redirect")
        data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise IncompleteNotices(f"Missing or unexpectedly large notice/metadata: {url}")
    return data


def put(output: Path, relative: str, data: bytes) -> None:
    path = output / safe_relative(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def copy_locked_notices(output: Path) -> dict:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if lock.get("schema_version") != 1 or lock.get("qt_version") != QT_VERSION:
        raise IncompleteNotices("Unsupported attribution lock/version")
    required = {"abseil", "freetype", "pdfium", "the-chromium-project", "fast-float", "icu", "libjpeg-turbo", "libpng", "zlib"}
    if set(lock.get("qtpdf_components", ())) != required:
        raise IncompleteNotices("QtPdf attribution component list is incomplete")
    if len(lock.get("files", [])) < 60:
        raise IncompleteNotices("Qt base/plugin attribution corpus is incomplete")
    paths = {item["path"] for item in lock["files"]}
    critical = {f"licenses/upstream/qt-6.11.2/qtpdf-3rdparty-{name}.txt" for name in required}
    critical |= {f"licenses/upstream/qt-6.11.2/{name}" for name in (
        "pdfium-LICENSE.txt", "pdfium-AUTHORS.txt", "chromium-LICENSE.txt",
        "pdfium-foxit-copyright.txt", "GPL-3.0-only.txt", "LGPL-3.0-only.txt")}
    critical |= {f"licenses/upstream/qt-6.11.2/pdfium-third-party/{name}" for name in (
        "agg23/copying.txt", "bigint/LICENSE.txt", "lcms/LICENSE.txt",
        "libopenjpeg/LICENSE.txt", "libtiff/LICENSE.md.txt",
        "dragonbox/LICENSE-Apache2-LLVM.txt", "dragonbox/LICENSE-Boost.txt")}
    if not critical <= paths:
        raise IncompleteNotices(f"Critical Qt/PDFium notices missing from lock: {sorted(critical-paths)}")
    for item in lock["files"]:
        path = ROOT / safe_relative(item["path"])
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise IncompleteNotices(f"Missing/modified locked upstream notice: {item['path']}")
        put(output, item["path"].removeprefix("licenses/"), path.read_bytes())
    foxit = output / "upstream/qt-6.11.2/pdfium-foxit-copyright.txt"
    if not foxit.is_file() or b"Original code copyright 2014 Foxit Software" not in foxit.read_bytes():
        raise IncompleteNotices("Missing PDFium/Foxit font copyright")
    put(output, "upstream-lock.json", LOCK.read_bytes())
    return lock


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def release_pins() -> dict[str, str]:
    result = {}
    for line in (ROOT / "packaging/requirements-release.txt").read_text().splitlines():
        match = re.fullmatch(r"\s*([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)\s*", line)
        if match:
            result[normalized_name(match[1])] = match[2]
    return result


def collect_distributions(output: Path) -> tuple[dict, list]:
    pins = release_pins()
    required = {"pyqt6", "pyqt6-qt6", "pyqt6-sip", "pypdf", "pyinstaller", "pyinstaller-hooks-contrib"}
    versions, inventory = {}, []
    for dist in sorted(metadata.distributions(), key=lambda d: normalized_name(d.metadata.get("Name", ""))):
        name = dist.metadata.get("Name")
        if not name:
            continue
        normalized = normalized_name(name)
        if normalized in required and dist.version != pins.get(normalized):
            raise IncompleteNotices(f"Release dependency mismatch: {name} {dist.version}; expected {pins.get(normalized)}")
        notices = []
        for item in dist.files or ():
            posix = str(item).replace("\\", "/")
            filename = PurePosixPath(posix).name.lower()
            license_directory = any(part.lower() in ("licenses", "licences") for part in PurePosixPath(posix).parts)
            if (license_directory or filename.startswith(("license", "licence", "copying", "notice", "copyright"))) and not posix.startswith("../"):
                path = Path(dist.locate_file(item))
                if path.is_file() and path.stat().st_size < 4 * 1024 * 1024:
                    relative = f"installed/{normalized}-{dist.version}/{posix}"
                    put(output, relative, path.read_bytes())
                    notices.append(relative)
        if normalized in required and not notices:
            raise IncompleteNotices(f"Installed package has no distributable license text: {name}")
        versions[name] = dist.version
        inventory.append({"name": name, "version": dist.version, "license_files": notices,
                          "project_urls": dist.metadata.get_all("Project-URL", [])})
    found = {normalized_name(name) for name in versions}
    if not required <= found:
        raise IncompleteNotices(f"Missing release distributions: {sorted(required-found)}")
    # Record predictable keys regardless of wheel metadata capitalization.
    versions = {name: metadata.version(name) for name in ("PyQt6", "PyQt6-Qt6", "PyQt6-sip", "pypdf", "PyInstaller", "pyinstaller-hooks-contrib")}
    from PyQt6.QtCore import qVersion, PYQT_VERSION_STR
    versions.update({"Qt": qVersion(), "Python": platform.python_version()})
    if versions["Qt"] != QT_VERSION or PYQT_VERSION_STR != versions["PyQt6"]:
        raise IncompleteNotices("Loaded Qt/PyQt do not match the installed release metadata")
    put(output, "installed-distributions.json", (json.dumps(inventory, indent=2) + "\n").encode())
    return versions, inventory


def collect_python(output: Path, version: str) -> None:
    for name in ("LICENSE", "Doc/license.rst"):
        url = f"https://raw.githubusercontent.com/python/cpython/v{version}/{name}"
        data = fetch(url)
        put(output, "python/" + name.replace("/", "-"), data)
    # CPython's Windows build links OpenSSL; keep the actual running SSL version.
    match = re.search(r"OpenSSL (\d+\.\d+\.\d+[a-z]*)", ssl.OPENSSL_VERSION)
    if not match:
        raise IncompleteNotices(f"Unrecognized Python OpenSSL version: {ssl.OPENSSL_VERSION}")
    openssl = match[1]
    url = f"https://raw.githubusercontent.com/openssl/openssl/openssl-{openssl}/LICENSE.txt"
    put(output, "python/OpenSSL-LICENSE.txt", fetch(url))
    runtime_licenses = collect_python_runtime_notices(output)
    put(output, "python/runtime.json", (json.dumps({"python": version, "openssl": ssl.OPENSSL_VERSION,
        "python_source": f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz",
        "openssl_license_source": url, "installed_runtime_licenses": runtime_licenses}, indent=2) + "\n").encode())


def collect_python_runtime_notices(output: Path) -> list[dict]:
    """Retain distributor-added runtime terms, notably Microsoft's Windows text."""
    locations = (("base-prefix", Path(sys.base_prefix)), ("prefix", Path(sys.prefix)),
                 ("stdlib", Path(sysconfig.get_path("stdlib"))))
    copied, seen = [], set()
    for label, directory in locations:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or not path.name.lower().startswith(("license", "licence", "copying", "notice")):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.stat().st_size > 4 * 1024 * 1024:
                raise IncompleteNotices("Installed Python runtime license is unexpectedly large")
            relative = f"python/installed-runtime/{label}-{path.name}"
            put(output, relative, path.read_bytes())
            copied.append({"file": relative, "source_location": f"{label}/{path.name}", "sha256": sha256(path)})
    return copied


def collect_system_notices(output: Path) -> None:
    if sys.platform != "linux":
        return
    # Ubuntu native libraries can be pulled in by Qt plugins/Python extension
    # hooks. Include the installed distro's complete copyright corpus, clearly
    # labelled as a superset rather than guessing a shorter runtime list.
    docs = Path("/usr/share/doc")
    if not shutil.which("dpkg-query"):
        raise IncompleteNotices("Public Linux releases require the audited Ubuntu/dpkg builder")
    data = subprocess.run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n"],
                          check=True, capture_output=True).stdout
    put(output, "system/installed-packages.tsv", data)
    count = 0
    for path in sorted(docs.glob("*/copyright")):
        if path.is_file():
            put(output, "system/" + path.parent.name + "/copyright", path.read_bytes())
            count += 1
    if count < 20:
        raise IncompleteNotices("Native system copyright corpus is unexpectedly incomplete")
    common = Path("/usr/share/common-licenses")
    for path in sorted(common.iterdir()):
        if path.is_file():
            put(output, "system/common-licenses/" + path.name, path.read_bytes())
    if not (output / "system/common-licenses/LGPL-2.1").is_file():
        raise IncompleteNotices("Missing full native-system LGPL license text")


def source_archives(versions: dict, lock: dict) -> list[dict]:
    result = [dict(lock["qt_source_archive"])]
    for package in ("PyQt6", "PyQt6-sip", "pypdf", "PyInstaller", "pyinstaller-hooks-contrib"):
        version = versions[package]
        data = json.loads(fetch(f"https://pypi.org/pypi/{package}/{version}/json"))
        sdists = [item for item in data["urls"] if item["packagetype"] == "sdist"]
        if len(sdists) != 1:
            raise IncompleteNotices(f"No unambiguous corresponding source distribution for {package} {version}")
        item = sdists[0]
        result.append({"component": package, "version": version, "filename": item["filename"],
                       "url": item["url"], "sha256": item["digests"]["sha256"], "size": item["size"]})
    # CPython release archives are an additional rebuild convenience; the PSF
    # license does not impose a source-offer requirement. Fetch hash from the
    # official release API rather than inventing a digest or trusting a filename.
    version = versions["Python"]
    # python.org publishes the canonical tarball. Its digest is calculated and
    # recorded by the source-download job; no invented upstream digest is used.
    result.append({"component": "CPython", "version": version, "filename": f"Python-{version}.tar.xz",
                   "url": f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz",
                   "sha256": None, "size": None})
    return result


def native_source_inventory(toc: Path) -> dict:
    """Map actually collected ELF files to exact Ubuntu source packages."""
    if sys.platform != "linux" or not shutil.which("dpkg-query"):
        raise IncompleteNotices("Native source inventory requires the Ubuntu release builder")
    sources = set()
    def walk(value):
        if isinstance(value, (list, tuple)):
            if len(value) == 3 and isinstance(value[1], str) and value[2] in ("BINARY", "EXTENSION"):
                candidate = Path(value[1])
                if candidate.is_absolute() and candidate.is_file():
                    with candidate.open("rb") as stream:
                        if stream.read(4) == b"\x7fELF":
                            sources.add(candidate)
            else:
                for item in value:
                    walk(item)
    walk(ast.literal_eval(toc.read_text(encoding="utf-8")))
    if not sources:
        raise IncompleteNotices("No ELF inputs found in the PyInstaller analysis")
    packages, exclusions = {}, []
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    for source in sorted(sources):
        path = source.resolve()
        parts = path.parts
        if "PyQt6" in parts or "PyInstaller" in parts:
            exclusions.append({"path": str(source), "reason": "Matching Qt/PyQt/PyInstaller source archives"})
            continue
        python_runtime_lib = path.is_relative_to(Path(sys.base_prefix).resolve()) and path.name.startswith(("libssl.", "libcrypto.", "libffi.", "liblzma.", "libbz2.", "libz."))
        if (path.is_relative_to(stdlib) and "site-packages" not in parts) or path.name.startswith("libpython") or python_runtime_lib:
            exclusions.append({"path": str(source), "reason": "CPython/standard-library runtime; corresponding notices collected"})
            continue
        owner = None
        for candidate in dict.fromkeys((source, path, Path(str(source).replace("/usr/lib/", "/lib/", 1)))):
            result = subprocess.run(["dpkg-query", "-S", str(candidate)], capture_output=True, text=True)
            if result.returncode == 0:
                owner = result.stdout.splitlines()[0].split(": ", 1)[0]
                break
        if not owner:
            raise IncompleteNotices(f"No audited source provider for native library: {source}")
        result = subprocess.run(["dpkg-query", "-W", "-f=${source:Package}\t${source:Version}", owner],
                                check=True, capture_output=True, text=True)
        package, version = result.stdout.strip().split("\t")
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", package) or not re.fullmatch(r"[A-Za-z0-9.+:~_-]+", version):
            raise IncompleteNotices("Invalid Ubuntu source package identity")
        item = packages.setdefault((package, version), {"source_package": package, "source_version": version,
                                                       "binary_packages": set(), "binary_paths": []})
        item["binary_packages"].add(owner)
        item["binary_paths"].append(str(source))
    for item in packages.values():
        item["binary_packages"] = sorted(item["binary_packages"])
    return {"schema_version": 1, "builder_platform": platform.platform(),
            "packages": [packages[key] for key in sorted(packages)], "exclusions": exclusions}


def ubuntu_source_archives(inventory: dict) -> list[dict]:
    """Resolve exact Ubuntu sources through the official Launchpad archive API.

    The small canonical .dsc binds every upstream/debian source tarball to an
    upstream-provided SHA-256. Superseded security updates remain queryable.
    """
    if inventory.get("schema_version") != 1:
        raise IncompleteNotices("Unsupported native source inventory")
    archives = {}
    for package in inventory["packages"]:
        name, version = package["source_package"], package["source_version"]
        query = urllib.parse.urlencode({"ws.op": "getPublishedSources", "source_name": name,
                                        "version": version, "exact_match": "true"})
        publications = json.loads(fetch("https://api.launchpad.net/1.0/ubuntu/+archive/primary?" + query))
        publication = next((item for item in publications.get("entries", []) if
                            item["source_package_name"] == name and item["source_package_version"] == version), None)
        if not publication:
            raise IncompleteNotices(f"Exact Ubuntu source publication unavailable: {name} {version}")
        urls = json.loads(fetch(publication["self_link"] + "?ws.op=sourceFileUrls"))
        by_name = {urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]): url for url in urls}
        dsc_names = [filename for filename in by_name if filename.endswith(".dsc")]
        if len(dsc_names) != 1:
            raise IncompleteNotices(f"No unique Ubuntu source descriptor for {name} {version}")
        dsc_name = dsc_names[0]
        data = fetch(by_name[dsc_name])
        text = data.decode("utf-8")
        if text.startswith("-----BEGIN PGP SIGNED MESSAGE-----"):
            text = text.split("\n\n", 1)[1].split("-----BEGIN PGP SIGNATURE-----", 1)[0]
        control = Parser().parsestr(text)
        if control.get("Source") != name or control.get("Version") != version:
            raise IncompleteNotices("Ubuntu descriptor does not match the collected native version")
        records = [{"component": "Ubuntu source: " + name, "version": version, "filename": dsc_name,
                    "url": by_name[dsc_name], "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                    "digest_source": "Calculated from canonical Ubuntu HTTPS descriptor"}]
        checksums = control.get("Checksums-Sha256", "").strip().splitlines()
        if not checksums:
            raise IncompleteNotices("Ubuntu descriptor lacks SHA-256 source checksums")
        for line in checksums:
            digest, size, filename = line.split()
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or filename not in by_name:
                raise IncompleteNotices("Ubuntu descriptor references unavailable/invalid source data")
            records.append({"component": "Ubuntu source: " + name, "version": version, "filename": filename,
                            "url": by_name[filename], "sha256": digest, "size": int(size),
                            "digest_source": by_name[dsc_name]})
        for item in records:
            previous = archives.get(item["filename"])
            if previous and previous["sha256"] != item["sha256"]:
                raise IncompleteNotices("Conflicting Ubuntu source archive filenames")
            archives[item["filename"]] = item
    return [archives[key] for key in sorted(archives)]


def download_sources(items: list[dict], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "source-manifest.json").unlink(missing_ok=True)
    for item in items:
        filename = item["filename"]
        if safe_relative(filename).name != filename:
            raise IncompleteNotices("Source archive must be a plain filename")
        path = destination / filename
        expected = item["sha256"]
        if path.exists() and expected and sha256(path) == expected:
            continue
        if urllib.parse.urlparse(item["url"]).scheme != "https":
            raise IncompleteNotices("Source archive URL must use HTTPS")
        request = urllib.request.Request(item["url"], headers={"User-Agent": "PDFWorkbench-source-archive/1"})
        temporary = destination / (filename + ".partial")
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as stream:
            if urllib.parse.urlparse(response.url).scheme != "https":
                raise IncompleteNotices("Refusing insecure source redirect")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024 * 1024 * 1024:
                    raise IncompleteNotices("Source archive exceeds the release asset limit")
                digest.update(chunk)
                stream.write(chunk)
        actual = digest.hexdigest()
        if expected and actual != expected:
            temporary.unlink(missing_ok=True)
            raise IncompleteNotices(f"Source digest mismatch: {filename}")
        if item.get("size") and temporary.stat().st_size != item["size"]:
            temporary.unlink(missing_ok=True)
            raise IncompleteNotices(f"Source size mismatch: {filename}")
        item.update(sha256=actual, size=temporary.stat().st_size)
        temporary.replace(path)
    (destination / "source-manifest.json").write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def verify_bundle(bundle: Path, notices: Path) -> dict:
    manifest = json.loads((notices / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
        raise IncompleteNotices("Notices completeness gate did not pass")
    if sys.platform == "linux" and manifest.get("native_source_audit_complete") is not True:
        raise IncompleteNotices("Ubuntu native source audit has not completed")
    for relative, expected in manifest["files"].items():
        path = notices / safe_relative(relative)
        if not path.resolve().is_relative_to(notices.resolve()) or path.is_symlink() or not path.is_file() or sha256(path) != expected:
            raise IncompleteNotices(f"Notice manifest mismatch: {relative}")
    modules = set()
    for path in bundle.rglob("*"):
        match = re.match(r"(?:lib)?Qt6([A-Za-z0-9]+)(?:\.so(?:\..*)?|\.dll)$", path.name)
        if match:
            modules.add(match[1])
    if not modules or not {"Core", "Gui", "Widgets", "Pdf"} <= modules:
        raise IncompleteNotices("Bundle is missing the expected native Qt libraries")
    unknown = modules - COVERED_QT_MODULES
    if unknown:
        raise IncompleteNotices(f"Native Qt modules need a new attribution/source audit: {sorted(unknown)}")
    return {"verified": True, "qt_modules": sorted(modules)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sources-output", type=Path)
    parser.add_argument("--verify-bundle", type=Path)
    parser.add_argument("--native-toc", type=Path, help="Emit native-sources.json from a completed Ubuntu Analysis-00.toc")
    parser.add_argument("--native-sources", type=Path, help="Include the Ubuntu build's native-sources.json in source acquisition")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.native_toc:
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
            raise IncompleteNotices("Native source audit requires complete existing notices")
        manifest_path.unlink()
        inventory = native_source_inventory(args.native_toc.resolve())
        archives = ubuntu_source_archives(inventory)
        put(output, "native-sources.json", (json.dumps(inventory, indent=2) + "\n").encode())
        manifest["files"]["native-sources.json"] = sha256(output / "native-sources.json")
        by_name = {item["filename"]: item for item in manifest["source_archives"]}
        for item in archives:
            previous = by_name.get(item["filename"])
            if previous and previous["sha256"] != item["sha256"]:
                raise IncompleteNotices("Conflicting native-source filenames")
            by_name[item["filename"]] = item
        manifest["source_archives"] = list(by_name.values())
        manifest["native_source_audit_complete"] = True
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Native source inventory: {output / 'native-sources.json'}")
        return 0
    if args.verify_bundle:
        print(json.dumps(verify_bundle(args.verify_bundle.resolve(), output)))
        return 0
    output.mkdir(parents=True, exist_ok=True)
    # Never leave a stale success marker after a failed re-collection.
    (output / "manifest.json").unlink(missing_ok=True)
    lock = copy_locked_notices(output)
    versions, _ = collect_distributions(output)
    collect_python(output, versions["Python"])
    collect_system_notices(output)
    sources = source_archives(versions, lock)
    if args.native_sources:
        inventory = json.loads(args.native_sources.read_text(encoding="utf-8"))
        sources.extend(ubuntu_source_archives(inventory))
        put(output, "native-sources.json", args.native_sources.read_bytes())
    if args.sources_output:
        sources_output = args.sources_output.resolve()
        if sources_output == output or output in sources_output.parents or sources_output in output.parents:
            raise IncompleteNotices("Large source archives must be separate from binary notice data")
        download_sources(sources, sources_output)
    put(output, "SOURCE_AVAILABILITY.md", ("# Corresponding source\n\n"
        "The matching PDF Workbench tag contains the application and build scripts.\n"
        "The same GitHub release also provides the upstream source archives listed\n"
        "in manifest.json. Keep those assets available with the binary downloads.\n"
        "Qt libraries are dynamically loaded and may be replaced by compatible\n"
        "modified builds; see the source repository's docs/distribution.md.\n\n"
        "Installed distribution and Ubuntu notice files are a deliberate superset\n"
        "of the shipped runtime. They do not imply every installed tool is bundled.\n").encode())
    files = {p.relative_to(output).as_posix(): sha256(p) for p in sorted(output.rglob("*")) if p.is_file() and p.name != "manifest.json"}
    manifest = {"schema_version": 1, "complete": True, "platform": platform.platform(),
                "versions": versions, "files": files, "source_archives": sources,
                "source_downloads_complete": args.sources_output is not None,
                "native_source_audit_complete": args.native_sources is not None,
                "qt_chromium_commit": lock["qt_chromium_commit"],
                "covered_qt_modules": sorted(COVERED_QT_MODULES)}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Complete release notices: {output} ({len(files)} files)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IncompleteNotices, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Release license gate failed: {error}", file=sys.stderr)
        raise SystemExit(1)
