"""Keep app downloads small while retaining every verified release material.

This module performs no network requests or GitHub mutations.  Its input is the
flat, already published (or publish-ready) release directory, including the
original complete SHA256SUMS.  Only the two native archives stay on the app
release; all other original bytes, including that checksum file, go into one
deterministic, uncompressed archive on the ``source-archives`` release.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from typing import Mapping


SOURCES_RELEASE_TAG = "source-archives"
# Deliberately below both the decimal 2 GB and GitHub's 2 GiB asset limit.
MAX_ARCHIVE_BYTES = 2_000_000_000
CHUNK_SIZE = 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*", re.ASCII)
_HASH = re.compile(r"[0-9a-f]{64}", re.ASCII)
_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?", re.ASCII)
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.ASCII)


def _plain_name(name: object) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or ".." in name:
        raise ValueError(f"Unsafe release filename: {name!r}")
    return name


def _identity(info: os.stat_result) -> tuple:
    # CPython on Windows can expose creation time through lstat().st_ctime_ns
    # and metadata-change time through fstat().st_ctime_ns for the same file:
    # https://github.com/python/cpython/issues/157671
    # Compare portable file/content identity across APIs, and retain ctime in
    # the separate before/after checks within each API below.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _snapshot(info: os.stat_result) -> tuple:
    return (*_identity(info), info.st_ctime_ns)


@contextmanager
def _regular_file(path: Path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Release input must be a regular file: {path.name}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
            raise ValueError(f"Release input changed while opening: {path.name}")
        yield stream, opened
        if _snapshot(os.fstat(stream.fileno())) != _snapshot(opened):
            raise ValueError(f"Release input changed while reading: {path.name}")
        try:
            current = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"Release input disappeared while reading: {path.name}") from exc
        if _snapshot(current) != _snapshot(before) or not stat.S_ISREG(current.st_mode):
            raise ValueError(f"Release input was replaced while reading: {path.name}")


def _hash_file(path: Path) -> dict:
    digest = hashlib.sha256()
    with _regular_file(path) as (stream, info):
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return {"filename": path.name, "sha256": digest.hexdigest(), "size": info.st_size}


def _load_json(path: Path):
    def distinct_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {path.name}: {key}")
            result[key] = value
        return result
    with _regular_file(path) as (stream, _):
        return json.load(stream, object_pairs_hook=distinct_pairs)


def _checksums(path: Path) -> dict[str, str]:
    with _regular_file(path) as (stream, _):
        text = stream.read().decode("utf-8")
    records = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if not match:
            raise ValueError("Malformed original SHA256SUMS")
        digest, name = match.groups()
        _plain_name(name)
        if name == "SHA256SUMS" or name in records:
            raise ValueError("Duplicate or self-referencing original SHA256SUMS entry")
        records[name] = digest
    if not records:
        raise ValueError("Empty original SHA256SUMS")
    return records


def _metadata_record(name: str, raw: Mapping) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError(f"Invalid remote native asset metadata: {name}")
    digest = raw.get("sha256", raw.get("digest"))
    if isinstance(digest, str) and digest.startswith("sha256:"):
        digest = digest[7:]
    size = raw.get("size")
    if not isinstance(digest, str) or not _HASH.fullmatch(digest):
        raise ValueError(f"Missing SHA256 in remote native asset metadata: {name}")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError(f"Invalid native asset size: {name}")
    return {"filename": name, "sha256": digest, "size": size}


class _DigestReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, count=-1):
        chunk = self.stream.read(count)
        self.digest.update(chunk)
        self.size += len(chunk)
        return chunk


class _LimitedWriter:
    def __init__(self, stream):
        self.stream = stream

    def tell(self):
        return self.stream.tell()

    def write(self, chunk):
        if self.tell() + len(chunk) >= MAX_ARCHIVE_BYTES:
            raise ValueError("Sources archive exceeds the release asset limit")
        return self.stream.write(chunk)

    def flush(self):
        return self.stream.flush()


def _verify_archive(path: Path, prefix: str, records: list[dict]) -> None:
    expected = {prefix + "/" + item["filename"]: item for item in records}
    seen = set()
    with tarfile.open(path, "r:") as archive:
        for member in archive:
            if (not member.isfile() or member.name not in expected or member.name in seen
                    or member.mode != 0o644 or member.uid != 0 or member.gid != 0
                    or member.mtime != 0 or member.uname or member.gname
                    or member.linkname):
                raise ValueError("Unexpected, duplicate or unsafe sources archive member")
            seen.add(member.name)
            record = expected[member.name]
            if member.size != record["size"]:
                raise ValueError(f"Sources archive member size mismatch: {member.name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Sources archive member is unreadable")
            with stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
            if digest.hexdigest() != record["sha256"]:
                raise ValueError(f"Sources archive member checksum mismatch: {member.name}")
    if seen != set(expected):
        raise ValueError("Sources archive is missing verified release materials")


def prepare_assets(
    input_dir: Path,
    output_dir: Path,
    tag: str,
    source_commit: str,
    asset_metadata: Mapping[str, Mapping] | None = None,
) -> dict:
    """Validate all old assets and prepare the two complementary release sets.

    ``output_dir`` must not exist. ``asset_metadata`` is used only when migrating
    an existing release: it maps the exact native filenames to fresh GitHub
    asset ``size`` and ``sha256`` (or ``digest: sha256:...``) records. No other
    missing file is permitted. Local native files, when available, are always
    hashed and compared; metadata never overrides their bytes.

    Returned ``app_assets`` contains paths to native archives present locally
    plus the new two-line checksum file. ``native_assets`` also describes native
    assets already present remotely, with ``local_path=None``. The sources pair
    is returned as ``materials_archive`` and ``materials_manifest``.
    """
    if not isinstance(tag, str) or not _TAG.fullmatch(tag):
        raise ValueError("Expected an exact version tag, for example v0.5.3")
    if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        raise ValueError("Expected the full app source commit")
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise ValueError("Release input must be a real directory")
    input_dir, output_dir = input_dir.resolve(), output_dir.absolute()
    if output_dir.resolve().is_relative_to(input_dir) or input_dir.is_relative_to(output_dir.resolve()):
        raise ValueError("Release input and output directories must be separate")
    if os.path.lexists(output_dir):
        raise FileExistsError(f"Release layout output already exists: {output_dir}")

    native_names = (
        f"PDFWorkbench-{tag}-windows-x86_64.zip",
        f"PDFWorkbench-{tag}-linux-x86_64.tar.gz",
    )
    metadata = dict(asset_metadata or {})
    if set(metadata) - set(native_names):
        raise ValueError("Remote metadata is permitted only for the two native app archives")
    remote = {name: _metadata_record(name, raw) for name, raw in metadata.items()}
    paths = {}
    for path in input_dir.iterdir():
        name = _plain_name(path.name)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"Release input must contain only regular files: {name}")
        # Case aliases are ambiguous on the native Windows platform.
        if name.casefold() in {item.casefold() for item in paths}:
            raise ValueError("Case-colliding release filenames")
        paths[name] = path
    if "SHA256SUMS" not in paths:
        raise ValueError("Original complete SHA256SUMS is required")
    checksums = _checksums(paths["SHA256SUMS"])
    present = set(paths) - {"SHA256SUMS"}
    missing = set(checksums) - present
    if present - set(checksums) or missing - (set(native_names) & set(remote)):
        raise ValueError("Original SHA256SUMS must cover every release asset exactly")
    required = {"source-manifest.json", f"PDFWorkbench-{tag}-source.tar.gz", *native_names}
    if required - set(checksums):
        raise ValueError("Release is missing app archives or the corresponding source manifest")

    records = {}
    for name in sorted(paths):
        record = _hash_file(paths[name])
        if name != "SHA256SUMS" and record["sha256"] != checksums[name]:
            raise ValueError(f"Release asset checksum mismatch: {name}")
        if name in remote and record != remote[name]:
            raise ValueError(f"Local native bytes differ from remote metadata: {name}")
        records[name] = record
    for name in missing:
        record = remote[name]
        if record["sha256"] != checksums[name]:
            raise ValueError(f"Remote native checksum differs from original SHA256SUMS: {name}")
        records[name] = record

    sources = _load_json(paths["source-manifest.json"])
    if not isinstance(sources, list) or not sources:
        raise ValueError("A complete, nonempty corresponding source manifest is required")
    source_names = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Malformed source manifest record")
        name = _plain_name(source.get("filename"))
        if name in source_names or name in native_names or name == "SHA256SUMS":
            raise ValueError("Duplicate or reserved source manifest filename")
        source_names.add(name)
        size, digest = source.get("size"), source.get("sha256")
        if (not isinstance(size, int) or isinstance(size, bool) or size <= 0
                or not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or name not in paths or records[name]["size"] != size
                or records[name]["sha256"] != digest):
            raise ValueError(f"Missing, incomplete or changed corresponding source: {name}")
    for platform_name in ("linux", "windows"):
        report = paths.get(f"PDFWorkbench-{tag}-{platform_name}-BUILD-INFO.json")
        if report is not None:
            data = _load_json(report)
            if not isinstance(data, dict) or data.get("source_commit") != source_commit or data.get("app_version") != tag[1:]:
                raise ValueError("Build information does not belong to this app tag and commit")

    # Recovery publishing reuses already built native archives. For 0.8 onward,
    # require the complete feature report and its exact builder linkage here as
    # well, before uploading any archive. This never executes downloaded code.
    if tuple(map(int, tag[1:].split('.')[:2])) >= (0, 8):
        from release_checks import required_core_checks, required_gui_checks, minimum_core_tests
        required_core = required_core_checks(tag)
        required_gui = required_gui_checks(tag)
        shared_source_hashes = None
        for platform_name in ('linux', 'windows'):
            info_path = paths.get(f'PDFWorkbench-{tag}-{platform_name}-BUILD-INFO.json')
            report_path = paths.get(f'PDFWorkbench-{tag}-{platform_name}-acceptance.json')
            if info_path is None or report_path is None:
                raise ValueError('Both native 0.8 build and acceptance reports are required')
            info, report = _load_json(info_path), _load_json(report_path)
            if not isinstance(info, dict) or not isinstance(report, dict):
                raise ValueError('Malformed native 0.8 build or acceptance report')
            core, gui = report.get('core', {}), report.get('gui', {})
            accepted = info.get('acceptance', {})
            provenance = report.get('provenance', {})
            if (not all(isinstance(item, dict) for item in (core, gui, accepted, provenance))
                    or not isinstance(core.get('checks'), list) or not all(isinstance(v, str) for v in core['checks'])
                    or not isinstance(gui.get('checks'), list) or not all(isinstance(v, str) for v in gui['checks'])):
                raise ValueError('Malformed native 0.8 acceptance fields')
            native_platforms = {'windows'} if platform_name == 'windows' else {'xcb', 'wayland'}
            if (report.get('result') != 'PASS' or report.get('frozen') is not True
                    or report.get('app_version') != tag[1:] or gui.get('app_version') != tag[1:]
                    or gui.get('result') != 'PASS' or gui.get('qt_platform') not in native_platforms
                    or type(core.get('tests')) is not int or core['tests'] < minimum_core_tests(tag)
                    or core.get('failures') != 0 or core.get('errors') != 0 or core.get('skipped') != []
                    or not required_core <= set(core.get('checks', []))
                    or not required_gui <= set(gui.get('checks', []))
                    or accepted.get('core') != core or accepted.get('gui_checks') != gui.get('checks')
                    or accepted.get('qt_platform') != gui.get('qt_platform')
                    or provenance.get('source_commit') != source_commit
                    or provenance.get('app_version') != tag[1:]
                    or provenance.get('source_sha256') != info.get('source_sha256')
                    or not isinstance(info.get('source_sha256'), dict) or not info['source_sha256']
                    or any(not isinstance(path, str) or not path or not isinstance(digest, str) or not _HASH.fullmatch(digest)
                           for path, digest in info['source_sha256'].items())
                    or not _HASH.fullmatch(str(info.get('executable_sha256', '')))
                    or report.get('executable_sha256') != info.get('executable_sha256')):
                raise ValueError('Complete native 0.8 acceptance must match its builder and source commit')
            if shared_source_hashes is None:
                shared_source_hashes = info['source_sha256']
            elif shared_source_hashes != info['source_sha256']:
                raise ValueError('Both native 0.8 builders must use identical source hashes')

    members = [records[name] for name in sorted(paths) if name not in native_names]
    # Avoid creating even a partial huge output when its minimum size cannot fit.
    minimum_size = sum(512 + ((item["size"] + 511) // 512) * 512 for item in members) + 1024
    if minimum_size >= MAX_ARCHIVE_BYTES:
        raise ValueError("Sources archive exceeds the release asset limit")
    prefix = f"PDFWorkbench-{tag}-sources-and-reports"
    archive_name = prefix + ".tar"
    output_dir.mkdir(parents=True, exist_ok=False)
    app_dir, materials_dir = output_dir / "app", output_dir / "sources"
    app_dir.mkdir()
    materials_dir.mkdir()
    archive_path = materials_dir / archive_name
    with archive_path.open("xb") as raw:
        with tarfile.open(fileobj=_LimitedWriter(raw), mode="w:", format=tarfile.PAX_FORMAT) as archive:
            for item in members:
                name = item["filename"]
                with _regular_file(paths[name]) as (stream, info):
                    if info.st_size != item["size"]:
                        raise ValueError(f"Release asset changed before archiving: {name}")
                    header = tarfile.TarInfo(prefix + "/" + name)
                    header.size = item["size"]
                    header.mode, header.uid, header.gid, header.mtime = 0o644, 0, 0, 0
                    reader = _DigestReader(stream)
                    archive.addfile(header, reader)
                    if reader.size != item["size"] or reader.digest.hexdigest() != item["sha256"]:
                        raise ValueError(f"Release asset changed while archiving: {name}")
    _verify_archive(archive_path, prefix, members)
    archive_record = _hash_file(archive_path)
    if archive_record["size"] >= MAX_ARCHIVE_BYTES:
        raise ValueError("Sources archive exceeds the release asset limit")
    native_assets = [dict(records[name], local_path=paths.get(name)) for name in native_names]
    manifest = {
        "schema_version": 1,
        "app_tag": tag,
        "app_source_commit": source_commit,
        "sources_release_tag": SOURCES_RELEASE_TAG,
        "archive": dict(archive_record, prefix=prefix),
        "native_assets": [records[name] for name in native_names],
        "members": members,
    }
    manifest_path = materials_dir / (prefix + ".json")
    with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    app_checksums = app_dir / "SHA256SUMS"
    with app_checksums.open("x", encoding="utf-8", newline="\n") as stream:
        for name in sorted(native_names):
            stream.write(records[name]["sha256"] + "  " + name + "\n")
    return {
        "app_assets": [paths[name] for name in native_names if name in paths] + [app_checksums],
        "app_checksums": app_checksums,
        "native_assets": native_assets,
        "materials_archive": archive_path,
        "materials_manifest": manifest_path,
        "members": members,
        "archive_sha256": archive_record["sha256"],
        "archive_size": archive_record["size"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--asset-metadata", type=Path, help="Fresh native GitHub asset metadata, keyed by filename")
    args = parser.parse_args()
    metadata = _load_json(args.asset_metadata) if args.asset_metadata else None
    layout = prepare_assets(args.input, args.output, args.tag, args.source_commit, metadata)
    print(json.dumps(layout, indent=2, default=str))


if __name__ == "__main__":
    main()
