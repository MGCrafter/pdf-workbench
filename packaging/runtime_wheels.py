"""Exact runtime-extension ownership and wheel-embedded dependency notices.

Linux Pillow is built from source against Ubuntu libraries. Its private wheel
libraries are deliberately NOT an approved provider. Cryptography's official
wheels publish hash-bearing OpenSSL and Cargo SBOMs; retain those, verify every
crate before reading its original license files, and record its complete source.
The installed RECORD must authenticate every approved native extension.
"""
from __future__ import annotations

import base64
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
from importlib import metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tomllib

VERSIONS = {"Pillow": "12.3.0", "cryptography": "50.0.1", "cffi": "2.1.1", "pycparser": "3.0",
            "PyMuPDF": "1.28.2", "aiohttp": "3.14.5", "frozenlist": "1.8.0", "multidict": "7.1.0",
            "propcache": "0.5.4", "yarl": "1.25.1", "lxml": "6.1.3"}
NATIVE_MODULES = {
    "aiohttp": ("aiohttp/_http_parser", "aiohttp/_http_writer", "aiohttp/_websocket/mask", "aiohttp/_websocket/reader_c"),
    "frozenlist": ("frozenlist/_frozenlist",), "multidict": ("multidict/_multidict", "multidict/_testcapi"),
    "propcache": ("propcache/_helpers_c",), "yarl": ("yarl/_quoting_c",),
    "lxml": ("lxml/_elementpath", "lxml/builder", "lxml/etree", "lxml/objectify", "lxml/sax", "lxml/html/_difflib", "lxml/html/diff"),
    "PyMuPDF": ("pymupdf/_extra", "pymupdf/_mupdf"),
}
RUST_COMMIT = "88d9e12ae178fab0fb5cc050a94da85685d449ea"
RUST_VERSION = "1.98.0"
RUST_BACKTRACE_COMMIT = "d902726a1dcdc1e1c66f73d1162181b5423c645b"
# Runtime/build dependencies of the x86_64 Linux/MSVC standard libraries at the
# audited compiler commit. Excludes libtest/dev crates and WASI/SGX/UEFI/other
# target runtimes from library/Cargo.lock. rust-src carries the in-tree crates.
RUST_STANDARD_CRATES = frozenset(("addr2line", "adler2", "cc", "cfg-if", "foldhash", "gimli", "hashbrown",
                                "libc", "memchr", "miniz_oxide", "object", "rustc-demangle", "shlex"))
PIL_EXTENSIONS = frozenset(("_avif", "_imaging", "_imagingcms", "_imagingft", "_imagingmath",
                            "_imagingmorph", "_imagingtk", "_webp"))
CALCULATED_DIGEST = "Calculated from pinned upstream HTTPS archive during cloud source acquisition"
# Pillow 12.3.0 winbuild/build_prepare.py and .github/dependencies.json, matched
# with the observed official cp314-win_amd64 wheel features. AVIF is explicitly
# excluded by build.py; FreeType is not collected and has no source exemption.
WINDOWS_PILLOW_SOURCES = (
    ("libjpeg-turbo", "3.1.4.1", "libjpeg-turbo-3.1.4.1.tar.gz", "https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.1.4.1/libjpeg-turbo-3.1.4.1.tar.gz"),
    ("libtiff", "4.7.1", "tiff-4.7.1.tar.gz", "https://download.osgeo.org/libtiff/tiff-4.7.1.tar.gz"),
    ("xz", "5.8.3", "xz-5.8.3.tar.gz", "https://github.com/tukaani-project/xz/releases/download/v5.8.3/xz-5.8.3.tar.gz"),
    ("OpenJPEG", "2.5.4", "openjpeg-2.5.4.tar.gz", "https://github.com/uclouvain/openjpeg/archive/v2.5.4.tar.gz"),
    ("zlib-ng", "2.3.3", "zlib-ng-2.3.3.tar.gz", "https://github.com/zlib-ng/zlib-ng/archive/refs/tags/2.3.3.tar.gz"),
    ("libwebp", "1.6.0", "libwebp-1.6.0.tar.gz", "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz"),
    ("Little CMS 2", "2.19.1", "lcms2-2.19.1.tar.gz", "https://github.com/mm2/Little-CMS/archive/refs/tags/lcms2.19.1.tar.gz"),
)


class RuntimeAuditError(RuntimeError):
    pass


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _safe_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name):
        raise RuntimeAuditError("Unsafe runtime component identity")
    return name


def _hash(component):
    hashes = [value["content"] for value in component.get("hashes", []) if value.get("alg") == "SHA-256"]
    if len(hashes) != 1 or not re.fullmatch(r"[0-9a-f]{64}", hashes[0]):
        raise RuntimeAuditError("Runtime SBOM lacks an unambiguous SHA-256")
    return hashes[0]


def _put(output, relative, data):
    path = output / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _verified_file(dist, entry):
    relative = PurePosixPath(str(entry).replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeAuditError("Runtime RECORD contains an unsafe path")
    path = Path(dist.locate_file(entry))
    if path.is_symlink() or not path.is_file():
        raise RuntimeAuditError(f"Runtime RECORD file missing or linked: {relative}")
    expected = entry.hash
    if expected is None or expected.mode != "sha256":
        raise RuntimeAuditError(f"Runtime RECORD has no SHA-256: {relative}")
    data = path.read_bytes()
    actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
    if expected.value != actual or (entry.size is not None and entry.size != len(data)):
        raise RuntimeAuditError(f"Runtime RECORD digest/size mismatch: {relative}")
    return path, data


def native_identity(name, relative):
    """Whitelisted *extension* identities; no generic directory exclusions."""
    path = PurePosixPath(relative)
    filename = path.name
    if name == "Pillow" and path.parent == PurePosixPath("PIL"):
        return any(re.fullmatch(re.escape(extension) + r"(?:\.[A-Za-z0-9_-]+)*\.(?:so|pyd)", filename)
                   for extension in PIL_EXTENSIONS)
    if name == "cryptography" and path.parent == PurePosixPath("cryptography/hazmat/bindings"):
        return bool(re.fullmatch(r"_rust(?:\.[A-Za-z0-9_-]+)*\.(?:so|pyd)", filename))
    if name == "cffi" and len(path.parts) == 1:
        return bool(re.fullmatch(r"_cffi_backend(?:\.[A-Za-z0-9_-]+)*\.(?:so|pyd)", filename))
    if name == "PyMuPDF" and relative in ("pymupdf/libmupdf.so.28.2", "pymupdf/libmupdfcpp.so.28.2", "pymupdf/mupdfcpp64.dll"):
        return True
    if name in NATIVE_MODULES:
        return any(re.fullmatch(re.escape(stem)+r"(?:\.[A-Za-z0-9_-]+)*\.(?:so|pyd)", relative)
                   for stem in NATIVE_MODULES[name])
    return False


def v09_lock():
    path = Path(__file__).with_name("v09-notices-lock.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("pymupdf_version") != VERSIONS["PyMuPDF"] or data.get("mupdf_version") != "1.28.2":
        raise RuntimeAuditError("Unsupported PyMuPDF/MuPDF attribution lock")
    return data


def windows_lxml_receipt_identity():
    spec = importlib.util.spec_from_file_location("pdfwb_lxml_builder", Path(__file__).with_name("build_lxml_windows.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.receipt_identity(helper.lock())


def verify_windows_lxml_receipt(receipt, record):
    expected = windows_lxml_receipt_identity()
    if (not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items())
            or receipt.get("build_inputs_verified") is not True
            or receipt.get("native_files") != {item["relative_path"]: item["sha256"] for item in record["native_files"]}):
        raise RuntimeAuditError("Windows lxml lacks its exact controlled-source-build receipt")
    required = {stem+".cp314-win_amd64.pyd" for stem in NATIVE_MODULES["lxml"]}
    if set(receipt["native_files"]) != required:
        raise RuntimeAuditError("Windows lxml controlled build has an unexpected native file set")


def audited_v09_sources(name, dist, record, output):
    """Identify the real native build; never exempt a package directory."""
    if name == "PyMuPDF":
        lock = v09_lock()
        entries = [entry for entry in dist.files or () if str(entry).replace("\\", "/") == "pymupdf/_build.py"]
        if len(entries) != 1: raise RuntimeAuditError("PyMuPDF wheel has no exact build provenance")
        _, data = _verified_file(dist, entries[0])
        values = {}
        for node in ast.parse(data).body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                values[node.targets[0].id] = ast.literal_eval(node.value)
        if (values.get("pymupdf_version") != VERSIONS[name] or values.get("pymupdf_git_sha") != lock["pymupdf_commit"]
                or values.get("mupdf_location") != lock["mupdf_source"]["url"] or values.get("pymupdf_git_diff") != ""):
            raise RuntimeAuditError("PyMuPDF wheel build differs from its audited source recipe")
        if sys.platform == "win32":
            from PyQt6 import QtCore  # Load the portable MSVC runtime first.
        import pymupdf
        if pymupdf.VersionBind != VERSIONS[name] or pymupdf.VersionFitz != lock["mupdf_version"]:
            raise RuntimeAuditError("Loaded MuPDF version differs from its source audit")
        required = {"pymupdf/_extra.pyd", "pymupdf/_mupdf.pyd", "pymupdf/mupdfcpp64.dll"} if sys.platform == "win32" else {"pymupdf/_extra.so", "pymupdf/_mupdf.so", "pymupdf/libmupdf.so.28.2", "pymupdf/libmupdfcpp.so.28.2"}
        if {item["relative_path"] for item in record["native_files"]} != required:
            raise RuntimeAuditError("PyMuPDF wheel native component set differs from its source audit")
        _put(output, "runtime/PyMuPDF-1.28.2/_build.py", data)
        record["build_provenance"] = values
        return [dict(lock["mupdf_source"])]
    if name == "lxml":
        from lxml import etree
        record["libxml_compiled"] = list(etree.LIBXML_COMPILED_VERSION)
        record["libxslt_compiled"] = list(etree.LIBXSLT_COMPILED_VERSION)
        if sys.platform == "linux":
            linked = subprocess.run(["readelf", "-d", etree.__file__], check=True, capture_output=True, text=True).stdout
            if not all(re.search(r"\(NEEDED\).*\["+library+r"\.so\.[0-9.]+\]", linked) for library in ("libxml2", "libxslt")):
                raise RuntimeAuditError("Linux release lxml must link audited system libxml2/libxslt; use --no-binary=lxml")
            record["native_source_provider"] = "Source-built lxml; linked native libraries require exact Ubuntu dpkg sources"
            return []
        if sys.platform == "win32":
            lock = v09_lock()["windows_lxml"]
            if record["libxml_compiled"] != lock["libxml_compiled"] or record["libxslt_compiled"] != lock["libxslt_compiled"]:
                raise RuntimeAuditError("Windows lxml native library versions differ from its audited recipe")
            entries = [entry for entry in dist.files or () if str(entry).replace("\\", "/").endswith(".dist-info/pdf-workbench-native-build.json")]
            if len(entries) != 1:
                raise RuntimeAuditError("Windows lxml requires the controlled-source-build helper receipt")
            _, receipt_data = _verified_file(dist, entries[0])
            receipt = json.loads(receipt_data)
            verify_windows_lxml_receipt(receipt, record)
            record["native_build_receipt"] = receipt
            _put(output, "runtime/lxml-6.1.3/pdf-workbench-native-build.json", receipt_data)
            record["native_source_provider"] = lock
            return [dict(item) for item in lock["source_archives"]]
        raise RuntimeAuditError("This lxml native platform has not been audited")
    return []


def require_linux_cffi_system_library(path):
    linked = subprocess.run(["readelf", "-d", str(path)], check=True,
                            capture_output=True, text=True).stdout
    if not re.search(r"\(NEEDED\).*\[libffi\.so\.[0-9.]+\]", linked):
        raise RuntimeAuditError("Linux release CFFI must link audited system libffi; use --no-binary=cffi")


def _sdist(name, version, fetch):
    data = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
    matches = [item for item in data["urls"] if item["packagetype"] == "sdist"]
    if len(matches) != 1:
        raise RuntimeAuditError(f"No exact corresponding source for {name} {version}")
    item = matches[0]
    return {"component": name, "version": version, "filename": item["filename"],
            "url": item["url"], "sha256": item["digests"]["sha256"], "size": item["size"]}


def cargo_sources(sbom):
    """Only crates.io identities with SBOM hashes become download requests."""
    if sbom.get("bomFormat") != "CycloneDX":
        raise RuntimeAuditError("Unsupported cryptography Cargo SBOM")
    result = []
    for component in sbom.get("components", []):
        purl = component.get("purl", "")
        if "?download_url=file:" in purl:
            # Internal cryptography workspace crates are inside its own sdist.
            if not component.get("name", "").startswith("cryptography-"):
                raise RuntimeAuditError("Unknown local Cargo source in runtime SBOM")
            continue
        name = _safe_name(component["name"])
        version = _safe_name(component["version"])
        if purl != f"pkg:cargo/{name}@{version}" or not component.get("licenses"):
            raise RuntimeAuditError("Unaudited Cargo identity/license in runtime SBOM")
        filename = f"{name}-{version}.crate"
        result.append({"component": "Rust crate: " + name, "version": version,
                       "filename": filename, "url": f"https://static.crates.io/crates/{name}/{filename}",
                       "sha256": _hash(component), "size": None,
                       "license_expressions": component["licenses"]})
    if not result:
        raise RuntimeAuditError("Cryptography Cargo SBOM has no registry components")
    return result


def crate_notices(item, fetch):
    """Read only bounded original notice members, never extract arbitrary paths."""
    data = fetch(item["url"])
    if _digest(data) != item["sha256"]:
        raise RuntimeAuditError(f"Cargo source digest mismatch: {item['filename']}")
    name = item["component"].removeprefix("Rust crate: ")
    prefix = f"{name}-{item['version']}"
    notices = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 10000:
            raise RuntimeAuditError("Cargo source archive has too many files")
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix):
                raise RuntimeAuditError("Unsafe Cargo source archive path")
            if not member.isfile():
                continue
            lower = path.name.lower()
            if lower.startswith(("license", "licence", "copying", "notice", "copyright", "authors")):
                if not 0 < member.size <= 4 * 1024 * 1024:
                    raise RuntimeAuditError("Cargo original notice has unexpected size")
                stream = archive.extractfile(member)
                if stream is None:
                    raise RuntimeAuditError("Cargo original notice is unreadable")
                notices.append((path.as_posix(), stream.read()))
    if not notices:
        raise RuntimeAuditError(f"Cargo source lacks original notices: {item['filename']}")
    return {**item, "size": len(data)}, notices


def openssl_source(sbom):
    components = sbom.get("components", [])
    if sbom.get("bomFormat") != "CycloneDX" or len(components) != 1:
        raise RuntimeAuditError("Unsupported cryptography native SBOM")
    component = components[0]
    version = _safe_name(component["version"])
    url = f"https://github.com/openssl/openssl/releases/download/openssl-{version}/openssl-{version}.tar.gz"
    refs = [ref["url"] for ref in component.get("externalReferences", []) if ref.get("type") == "distribution"]
    if component.get("name") != "openssl" or refs != [url]:
        raise RuntimeAuditError("Unknown native component in cryptography wheel")
    return {"component": "cryptography bundled OpenSSL", "version": version,
            "filename": f"openssl-{version}.tar.gz", "url": url, "sha256": _hash(component), "size": None}


def cffi_vendored_notices(source, fetch):
    """The Windows CFFI binary embeds modified libffi with its own MIT notices."""
    data = fetch(source["url"])
    if _digest(data) != source["sha256"]:
        raise RuntimeAuditError("CFFI vendored-source digest mismatch")
    prefix = f"cffi-{source['version']}/src/c/libffi_x86_x64/"
    notices = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.name.startswith(prefix) or not member.isfile():
                continue
            path = PurePosixPath(member.name)
            if ".." in path.parts or path.is_absolute() or member.size > 1024 * 1024:
                raise RuntimeAuditError("Unsafe CFFI vendored-source member")
            if path.suffix in (".c", ".h", ".asm"):
                stream = archive.extractfile(member)
                if stream is not None:
                    notices.append((path.name, stream.read()))
    required = {"ffi.c", "ffi.h", "ffitarget.h", "prep_cif.c", "types.c", "win32.c", "win64.asm"}
    if not required <= {name for name, _ in notices}:
        raise RuntimeAuditError("CFFI modified libffi original source/notice corpus is incomplete")
    return notices


def rust_standard_sources(native_data, output, fetch):
    commits = {value.decode() for value in re.findall(rb"/rustc/([0-9a-f]{40})/", native_data)}
    if commits != {RUST_COMMIT}:
        raise RuntimeAuditError("Cryptography Rust standard-library compiler identity differs from its audit")
    channel_url = f"https://static.rust-lang.org/dist/channel-rust-{RUST_VERSION}.toml"
    channel_data = fetch(channel_url)
    channel = tomllib.loads(channel_data.decode())
    package = channel["pkg"]["rust-src"]
    if package.get("git_commit_hash") != RUST_COMMIT:
        raise RuntimeAuditError("Official Rust source component does not match the embedded compiler commit")
    archive = package["target"]["*"]
    url, digest = archive["xz_url"], archive["xz_hash"]
    if not url.startswith("https://static.rust-lang.org/dist/") or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeAuditError("Official Rust source component has invalid provenance")
    sources = [{"component": "Rust standard library", "version": RUST_VERSION,
                "filename": f"rust-src-{RUST_VERSION}.tar.xz", "url": url,
                "sha256": digest, "size": None, "git_commit": RUST_COMMIT,
                "digest_source": channel_url}]
    prefix = f"runtime/rust-stdlib-{RUST_VERSION}/"
    _put(output, prefix + "channel.toml", channel_data)
    for name in ("LICENSE-MIT", "LICENSE-APACHE", "COPYRIGHT", "REUSE.toml",
                 "library/compiler-builtins/LICENSE.txt", "library/compiler-builtins/libm/LICENSE.txt",
                 "library/stdarch/LICENSE-MIT", "library/stdarch/LICENSE-APACHE"):
        _put(output, prefix + name, fetch(f"https://raw.githubusercontent.com/rust-lang/rust/{RUST_COMMIT}/{name}"))
    # rust-src includes this submodule's source, but its attribution is authored
    # separately from Rust's root license texts.
    for name in ("LICENSE-MIT", "LICENSE-APACHE"):
        _put(output, prefix + "library/backtrace/" + name,
             fetch(f"https://raw.githubusercontent.com/rust-lang/backtrace-rs/{RUST_BACKTRACE_COMMIT}/{name}"))
    lock = fetch(f"https://raw.githubusercontent.com/rust-lang/rust/{RUST_COMMIT}/library/Cargo.lock")
    _put(output, prefix + "Cargo.lock", lock)
    for package in tomllib.loads(lock.decode())["package"]:
        if "source" not in package:
            continue  # In-tree standard-library code is in official rust-src.
        if package["source"] != "registry+https://github.com/rust-lang/crates.io-index":
            raise RuntimeAuditError("Unknown Rust standard-library external source provider")
        name, version = _safe_name(package["name"]), _safe_name(package["version"])
        if name not in RUST_STANDARD_CRATES:
            continue
        checksum = package.get("checksum", "")
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise RuntimeAuditError("Rust standard-library dependency lacks a published checksum")
        filename = f"{name}-{version}.crate"
        sources.append({"component": "Rust crate: " + name, "version": version,
                        "filename": filename, "url": f"https://static.crates.io/crates/{name}/{filename}",
                        "sha256": checksum, "size": None, "dependency_of": "Rust standard library " + RUST_VERSION})
    return sources


def merge_source_records(*collections):
    records = {}
    for collection in collections:
        for item in collection:
            filename = item.get("filename", "")
            _safe_name(filename)
            digest = item.get("sha256")
            calculated = digest is None and item.get("digest_source") == CALCULATED_DIGEST
            if not calculated and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise RuntimeAuditError("Runtime source record lacks an exact SHA-256")
            if not item.get("url", "").startswith("https://"):
                raise RuntimeAuditError("Runtime source record is not HTTPS")
            if filename in records:
                previous = records[filename]
                if previous.get("version") != item.get("version") or previous.get("url") != item.get("url"):
                    raise RuntimeAuditError("Conflicting platform runtime source identities")
                if previous["sha256"] is not None and digest is not None and previous["sha256"] != digest:
                    raise RuntimeAuditError("Conflicting platform runtime source records")
                if previous["sha256"] is not None:
                    continue
            records[filename] = item
    return [records[key] for key in sorted(records)]


def windows_pillow_sources(sbom):
    if sbom.get("metadata", {}).get("component", {}).get("version") != VERSIONS["Pillow"]:
        raise RuntimeAuditError("Pillow SBOM is not the audited version")
    components = {item["name"]: item.get("version") for item in sbom.get("components", [])}
    expected = {"libjpeg / libjpeg-turbo": "3.1.4.1", "libtiff": "4.7.1", "OpenJPEG": "2.5.4",
                "zlib": "2.3.3", "libwebp": "1.6.0", "Little CMS 2": "2.19.1"}
    if any(components.get(name) != version for name, version in expected.items()):
        raise RuntimeAuditError("Windows Pillow codec SBOM differs from the audited source recipe")
    return [{"component": "Windows Pillow bundled " + name, "version": version,
             "filename": filename, "url": url, "sha256": None, "size": None,
             "digest_source": CALCULATED_DIGEST,
             "build_recipe": "https://github.com/python-pillow/Pillow/blob/12.3.0/winbuild/build_prepare.py",
             "patches": "Matching Pillow sdist includes wheel build scripts and source patches"}
            for name, version, filename, url in WINDOWS_PILLOW_SOURCES]


def collect_runtime_notices(output: Path, fetch) -> dict:
    """Run on each native builder; no large native source archives are fetched."""
    distributions, sources = [], []
    for name, version in VERSIONS.items():
        dist = metadata.distribution(name)
        if dist.version != version:
            raise RuntimeAuditError(f"Unaudited runtime version: {name} {dist.version}; expected {version}")
        record = {"name": name, "version": version, "native_files": [], "sbom_files": []}
        sboms, rust_binary = {}, None
        for entry in dist.files or ():
            relative = str(entry).replace("\\", "/")
            native = re.search(r"\.(?:so(?:\.[0-9.]+)?|pyd|dll)$", relative, re.I)
            sbom = "/sboms/" in relative and relative.endswith(".json")
            if not native and not sbom:
                continue
            path, data = _verified_file(dist, entry)
            if native:
                if name in NATIVE_MODULES and not native_identity(name, relative):
                    raise RuntimeAuditError(f"Unknown native file in audited {name} wheel: {relative}")
                if sys.platform == "linux" and name == "Pillow" and not native_identity(name, relative):
                    raise RuntimeAuditError("Linux release Pillow must be source-built; private wheel libraries are unaudited")
                if sys.platform == "linux" and name == "cffi":
                    # PyPI manylinux CFFI wheels statically embed another libffi
                    # build. Source-build against audited Ubuntu libffi instead.
                    require_linux_cffi_system_library(path)
                record["native_files"].append({"path": str(path.resolve()), "relative_path": relative,
                                               "sha256": _digest(data)})
                if name == "cryptography" and native_identity(name, relative):
                    rust_binary = data
            if sbom:
                destination = f"runtime/{name}-{version}/{PurePosixPath(relative).name}"
                _put(output, destination, data)
                record["sbom_files"].append(destination)
                sboms[PurePosixPath(relative).name] = json.loads(data)
        if name == "cryptography":
            if not {"sbom.json", "cryptography-rust.cyclonedx.json"} <= sboms.keys():
                raise RuntimeAuditError("Cryptography wheel is missing its native/Cargo SBOMs")
            native = openssl_source(sboms["sbom.json"])
            license_url = f"https://raw.githubusercontent.com/openssl/openssl/openssl-{native['version']}/LICENSE.txt"
            _put(output, f"runtime/OpenSSL-{native['version']}/LICENSE.txt", fetch(license_url))
            sources.append(native)
            if rust_binary is None:
                raise RuntimeAuditError("Cryptography Rust extension was not found")
            standard = rust_standard_sources(rust_binary, output, fetch)
            sources.extend(item for item in standard if not item["component"].startswith("Rust crate: "))
            crates = merge_source_records(cargo_sources(sboms["cryptography-rust.cyclonedx.json"]),
                                          [item for item in standard if item["component"].startswith("Rust crate: ")])
            with ThreadPoolExecutor(max_workers=4) as pool:
                for item, notices in pool.map(lambda item: crate_notices(item, fetch), crates):
                    sources.append(item)
                    for relative, data in notices:
                        _put(output, "runtime/cargo/" + relative, data)
        if name == "Pillow" and sys.platform == "win32":
            pillow_sbom = sboms.get(f"pillow-{version}.cdx.json")
            if pillow_sbom is None:
                raise RuntimeAuditError("Windows Pillow wheel is missing its codec SBOM")
            sources.extend(windows_pillow_sources(pillow_sbom))
        own_source = _sdist(name, version, fetch)
        sources.append(own_source)
        sources.extend(audited_v09_sources(name, dist, record, output))
        if name == "cffi":
            for relative, data in cffi_vendored_notices(own_source, fetch):
                _put(output, f"runtime/cffi-{version}/libffi_x86_x64/" + relative, data)
        distributions.append(record)
    # Pillow's vendored LGPL shim and MIT raqm originals are in its matching
    # sdist. Keep the LGPL text and upstream provenance visible in both apps.
    version = VERSIONS["Pillow"]
    for path in ("src/thirdparty/fribidi-shim/fribidi.c", "src/thirdparty/fribidi-shim/fribidi.h", "src/thirdparty/raqm/COPYING", "src/thirdparty/pythoncapi_compat.h"):
        url = f"https://raw.githubusercontent.com/python-pillow/Pillow/{version}/{path}"
        _put(output, f"runtime/Pillow-{version}/" + path, fetch(url))
    _put(output, f"runtime/Pillow-{version}/LGPL-2.1.txt", fetch("https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt"))
    inventory = {"schema_version": 1, "platform": sys.platform,
                 "distributions": distributions, "source_archives": merge_source_records(sources)}
    _put(output, "runtime-wheels.json", (json.dumps(inventory, indent=2) + "\n").encode())
    return inventory


def native_provider(source: Path, inventory: dict) -> dict | None:
    """Map only hash-verified recorded extensions, never unknown sibling libs."""
    if inventory.get("schema_version") != 1:
        raise RuntimeAuditError("Unsupported runtime inventory")
    path = source.resolve()
    for distribution in inventory.get("distributions", []):
        name, version = distribution["name"], distribution["version"]
        if name not in VERSIONS or version != VERSIONS[name]:
            raise RuntimeAuditError("Runtime inventory contains an unaudited version")
        for item in distribution.get("native_files", []):
            if str(path) != item["path"]:
                continue
            if not native_identity(name, item["relative_path"]):
                raise RuntimeAuditError(f"Native wheel library has no audited provider: {source}")
            basename = PurePosixPath(item["relative_path"]).name.split(".")[0]
            if name == "Pillow" and (basename == "_avif" or (inventory.get("platform") == "win32" and basename == "_imagingft")):
                raise RuntimeAuditError("Pillow extension requires an additional embedded-codec source audit")
            if _digest(path.read_bytes()) != item["sha256"]:
                raise RuntimeAuditError(f"Runtime native input changed since audit: {source}")
            source_id = (name.casefold(), version)
            providers = {(value.get("component", "").casefold(), value.get("version"))
                         for value in inventory.get("source_archives", [])}
            if source_id not in providers:
                raise RuntimeAuditError("Runtime native input has no exact corresponding source record")
            if name == "PyMuPDF" and ("mupdf", "1.28.2") not in providers:
                raise RuntimeAuditError("MuPDF shared library has no complete matching source archive")
            if name == "lxml" and inventory.get("platform") == "win32":
                verify_windows_lxml_receipt(distribution.get("native_build_receipt"), distribution)
                required = {(value["component"].casefold(), value["version"]) for value in v09_lock()["windows_lxml"]["source_archives"]}
                if not required <= providers:
                    raise RuntimeAuditError("Windows lxml native input lacks its corresponding sources")
            return {"path": str(source), "reason": f"RECORD-verified {name} {version} extension; runtime-wheels.json source archives",
                    "distribution": name, "version": version, "sha256": item["sha256"]}
    return None


def verify_frozen_runtime(bundle: Path, inventory: dict | None) -> list[dict]:
    """Bind the actually shipped runtime extensions to their audited RECORDs."""
    verified = []
    for path in bundle.rglob("*"):
        if not path.is_file() or not re.search(r"\.(?:so(?:\.[0-9.]+)?|pyd|dll)$", path.name, re.I):
            continue
        parts = path.relative_to(bundle).parts
        if any(part.lower() in ("pillow.libs", "cffi.libs", "cryptography.libs") for part in parts):
            raise RuntimeAuditError("Private runtime-wheel native libraries need a new source audit")
        if "PIL" in parts:
            name, relative = "Pillow", "/".join(parts[parts.index("PIL"):])
        elif "cryptography" in parts:
            name, relative = "cryptography", "/".join(parts[parts.index("cryptography"):])
        elif path.name.startswith("_cffi_backend"):
            name, relative = "cffi", path.name
        elif "pymupdf" in parts:
            name, relative = "PyMuPDF", "/".join(parts[parts.index("pymupdf"):])
        elif path.name.startswith(("libmupdf", "mupdfcpp")):
            name, relative = "PyMuPDF", "pymupdf/"+path.name
        else:
            matches = [(package, part) for package in NATIVE_MODULES if package != "PyMuPDF"
                       for part in parts if part == package]
            if not matches: continue
            name, package = matches[0]; relative = "/".join(parts[parts.index(package):])
        basename = path.name.split(".")[0]
        if name == "Pillow" and basename == "_avif":
            raise RuntimeAuditError("Excluded AVIF extension was accidentally bundled")
        if inventory is None or inventory.get("schema_version") != 1:
            raise RuntimeAuditError("Bundled runtime extensions require their audited runtime-wheels.json")
        if not native_identity(name, relative):
            raise RuntimeAuditError(f"Unknown bundled {name} native extension: {relative}")
        if name == "Pillow" and inventory.get("platform") == "win32" and basename == "_imagingft":
            raise RuntimeAuditError("Windows Pillow font extension requires an additional embedded-codec source audit")
        candidates = [item for dist in inventory.get("distributions", [])
                      if dist.get("name") == name and dist.get("version") == VERSIONS[name]
                      for item in dist.get("native_files", []) if item["relative_path"] == relative]
        if len(candidates) != 1 or _digest(path.read_bytes()) != candidates[0]["sha256"]:
            raise RuntimeAuditError(f"Bundled runtime extension differs from its audited RECORD: {relative}")
        if name in NATIVE_MODULES:
            source_ids = {(item.get("component", "").casefold(), item.get("version")) for item in inventory.get("source_archives", ())}
            if (name.casefold(), VERSIONS[name]) not in source_ids:
                raise RuntimeAuditError("New bundled native extension lacks its exact corresponding source record")
        if name == "PyMuPDF":
            providers = {(item.get("component", "").casefold(), item.get("version")) for item in inventory.get("source_archives", ())}
            if not {("pymupdf", VERSIONS[name]), ("mupdf", "1.28.2")} <= providers:
                raise RuntimeAuditError("Bundled MuPDF requires both matching PyMuPDF and complete MuPDF source records")
        if name == "lxml" and inventory.get("platform") == "win32":
            distributions = [dist for dist in inventory.get("distributions", ()) if dist.get("name") == name and dist.get("version") == VERSIONS[name]]
            if len(distributions) != 1:
                raise RuntimeAuditError("Bundled Windows lxml has ambiguous build provenance")
            verify_windows_lxml_receipt(distributions[0].get("native_build_receipt"), distributions[0])
            required = {(item["component"], item["version"]) for item in v09_lock()["windows_lxml"]["source_archives"]}
            provided = {(item.get("component"), item.get("version")) for item in inventory.get("source_archives", ())}
            if not required <= provided:
                raise RuntimeAuditError("Bundled Windows lxml lacks its static-library corresponding sources")
        verified.append({"relative_path": relative, "distribution": name, "version": VERSIONS[name],
                         "sha256": candidates[0]["sha256"]})
    return verified
