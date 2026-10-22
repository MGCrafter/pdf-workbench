"""Build lxml with four exact, hash-verified Windows native inputs.

Upstream selects the latest native release at build time. This release helper
replaces only that selection with a fixed local input map. It preserves the
original source, deterministic patch, and a RECORD-bound build receipt. No
prebuilt lxml wheel is accepted as proof of the statically linked iconv version.
"""
from __future__ import annotations

import argparse
import base64
import csv
import difflib
import hashlib
from importlib import metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = "pdf-workbench-native-build.json"


class BuildError(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def lock():
    value = json.loads((ROOT / "packaging/v09-notices-lock.json").read_text(encoding="utf-8"))["windows_lxml"]
    if value["version"] != "6.1.3" or set(value["native_assets"]) != {"iconv", "libxml2", "libxslt", "zlib"}:
        raise BuildError("Unexpected lxml native input lock")
    return value


def download(item, cache):
    """Reuse authenticated bytes only; never replace an existing foreign file."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (item["sha256"] + "-" + item["filename"])
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise BuildError("Unsafe native download cache entry")
        data = target.read_bytes()
        if len(data) != item["size"] or digest(data) != item["sha256"]:
            raise BuildError("Cached native input digest/size mismatch")
        return target
    request = urllib.request.Request(item["url"], headers={"User-Agent": "PDF-Workbench-source-build/0.9"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(item["size"] + 1)
    if len(data) != item["size"] or digest(data) != item["sha256"]:
        raise BuildError("Downloaded native input digest/size mismatch")
    with target.open("xb") as stream:
        stream.write(data)
    return target


def safe_relative(name):
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in ("..", "") or ":" in part for part in path.parts):
        raise BuildError("Unsafe source/archive member")
    return path


def extract(archive_path, destination, zipped=False):
    """Extract regular files only, bounded, into a fresh destination."""
    destination.mkdir()
    roots = set()
    total = 0
    seen = set()
    with (zipfile.ZipFile(archive_path) if zipped else tarfile.open(archive_path, "r:gz")) as archive:
        members = archive.infolist() if zipped else archive.getmembers()
        if len(members) > 20000:
            raise BuildError("Native input contains too many members")
        for member in members:
            name = member.filename if zipped else member.name
            relative = safe_relative(name)
            identity = relative.as_posix().casefold()
            if identity in seen:
                raise BuildError("Duplicate source/archive member")
            seen.add(identity)
            roots.add(relative.parts[0])
            target = destination.joinpath(*relative.parts)
            is_directory = member.is_dir() if zipped else member.isdir()
            mode = (member.external_attr >> 16) if zipped else member.mode
            if is_directory:
                target.mkdir(parents=True, exist_ok=True)
                continue
            if (zipped and (mode & 0o170000) == 0o120000) or (not zipped and not member.isfile()):
                raise BuildError("Linked/special native source member")
            size = member.file_size if zipped else member.size
            total += size
            if size > 100 * 1024 * 1024 or total > 400 * 1024 * 1024:
                raise BuildError("Native source expands beyond its bound")
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.open(member) if zipped else archive.extractfile(member)
            with stream, target.open("xb") as output:
                output.write(stream.read())
            if target.stat().st_size != size:
                raise BuildError("Truncated native source member")
    if len(roots) != 1:
        raise BuildError("Native source must have one top-level directory")
    return destination / next(iter(roots))


REPLACEMENT = '''def download_and_extract_windows_binaries(destdir, arch=None):
    # PDF Workbench: exact local assets; no dynamic latest-release lookup.
    import hashlib
    import json
    from pathlib import Path
    if sys.maxsize <= 2**32 or arch not in (None, "win64"):
        raise RuntimeError("PDF Workbench native inputs require Windows x64")
    config = json.loads(Path(__file__).with_name("pdfwb-native-inputs.json").read_text(encoding="utf-8"))
    roots = {}
    for name, item in config["inputs"].items():
        root = Path(item["root"])
        for relative, expected in item["files"].items():
            path = root / relative
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeError("PDF Workbench native build input changed")
        roots[name] = str(root)
    Path(config["used_marker"]).write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    return roots


'''


def patched_script(original, expected_hash):
    if digest(original) != expected_hash:
        raise BuildError("lxml buildlibxml.py differs from its pinned original source")
    text = original.decode("utf-8")
    start = text.index("def download_and_extract_windows_binaries(")
    end = text.index("def find_top_dir_of_zipfile(", start)
    result = text[:start] + REPLACEMENT + text[end:]
    compile(result, "buildlibxml.py", "exec")
    patch = "".join(difflib.unified_diff(text.splitlines(keepends=True), result.splitlines(keepends=True),
                                         fromfile="a/buildlibxml.py", tofile="b/buildlibxml.py")).encode()
    return result.encode(), patch


def native_files(dist):
    result = {}
    for entry in dist.files or ():
        relative = str(entry).replace("\\", "/")
        if relative.startswith("lxml/") and relative.lower().endswith((".pyd", ".dll", ".so")):
            path = Path(dist.locate_file(entry))
            if path.is_symlink() or not path.is_file():
                raise BuildError("Missing/linked installed lxml native file")
            result[relative] = digest(path.read_bytes())
    stems = {re.sub(r"\.cp[0-9]+-win_amd64\.pyd$", "", name) for name in result}
    if stems != {"lxml/_elementpath", "lxml/builder", "lxml/etree", "lxml/objectify", "lxml/sax", "lxml/html/_difflib", "lxml/html/diff"}:
        raise BuildError("Unexpected installed lxml native file set")
    return result


def receipt_identity(value):
    """Stable proof fields shared by builder and the independent collector."""
    original = (ROOT / value["build_script"]["path"]).read_bytes()
    _, patch = patched_script(original, value["build_script"]["sha256"])
    return {"schema_version": 1, "distribution": "lxml", "version": value["version"], "platform": "win_amd64",
            "source": value["sdist"], "native_assets": value["native_assets"],
            "recipe_tag": value["build_recipe_tag"], "recipe_tree": value["recipe_tree"],
            "helper_sha256": digest(Path(__file__).read_bytes()), "patch_sha256": digest(patch)}


def attach_receipt(dist, receipt):
    entries = [entry for entry in dist.files or () if str(entry).replace("\\", "/").endswith(".dist-info/RECORD")]
    if len(entries) != 1:
        raise BuildError("lxml installation has no unique RECORD")
    record = Path(dist.locate_file(entries[0]))
    receipt_path = record.parent / RECEIPT
    data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    with receipt_path.open("xb") as stream:
        stream.write(data)
    rows = list(csv.reader(io.StringIO(record.read_text(encoding="utf-8"))))
    relative = str(PurePosixPath(str(entries[0]).replace("\\", "/")).parent / RECEIPT)
    if any(row and row[0] == relative for row in rows):
        raise BuildError("lxml build receipt already present in RECORD")
    rows.append([relative, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="), str(len(data))])
    with record.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    return receipt_path


def build(work_dir, cache):
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise BuildError("This controlled builder supports Windows x64 only")
    value = lock()
    work_dir.mkdir(parents=True, exist_ok=True)
    build_dir = Path(tempfile.mkdtemp(prefix="lxml-build-", dir=work_dir))
    source = extract(download(value["sdist"], cache), build_dir / "source")
    inputs = {}
    for name, item in value["native_assets"].items():
        root = extract(download(item, cache), build_dir / name, zipped=True)
        if not (root / "include").is_dir() or not (root / "lib").is_dir() or not list((root / "lib").glob("*.lib")):
            raise BuildError("Native asset lacks expected headers/static import libraries")
        inputs[name] = {"root": str(root), "asset": item,
                        "files": {path.relative_to(root).as_posix(): digest(path.read_bytes())
                                  for path in sorted(root.rglob("*")) if path.is_file()}}
    original = (source / "buildlibxml.py").read_bytes()
    modified, patch = patched_script(original, value["build_script"]["sha256"])
    (source / "buildlibxml.py").write_bytes(modified)
    (build_dir / "pdfwb-lxml-build.patch").write_bytes(patch)
    marker = build_dir / "native-inputs-used.json"
    config = {"inputs": inputs, "used_marker": str(marker)}
    (source / "pdfwb-native-inputs.json").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    environment = dict(os.environ)
    environment["STATIC_DEPS"] = "true"
    subprocess.run([sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(source)],
                   env=environment, check=True)
    if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != config:
        raise BuildError("Build did not acknowledge the exact pinned native inputs")
    # Recheck every actual extracted input after the compiler consumed it.
    for item in inputs.values():
        for relative, expected in item["files"].items():
            if digest((Path(item["root"]) / relative).read_bytes()) != expected:
                raise BuildError("Native input changed during compilation")
    dist = metadata.distribution("lxml")
    if dist.version != value["version"]:
        raise BuildError("Unexpected installed lxml version")
    from lxml import etree
    if list(etree.LIBXML_COMPILED_VERSION) != value["libxml_compiled"] or list(etree.LIBXSLT_COMPILED_VERSION) != value["libxslt_compiled"]:
        raise BuildError("Built lxml reports unexpected libxml2/libxslt versions")
    receipt = receipt_identity(value)
    receipt["native_files"] = native_files(dist)
    receipt["build_inputs_verified"] = True
    receipt_path = attach_receipt(dist, receipt)
    (build_dir / RECEIPT).write_bytes(receipt_path.read_bytes())
    print(json.dumps({"receipt": str(receipt_path), "build_evidence": str(build_dir)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    build(args.work_dir.resolve(), (args.cache or args.work_dir / "downloads").resolve())


if __name__ == "__main__":
    main()
