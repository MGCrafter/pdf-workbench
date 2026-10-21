"""Release cleanup must retain the exact published sources and audit bytes."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_layout", PROJECT / "packaging/release_layout.py")
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class ReleaseLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input = self.root / "original-release"
        self.input.mkdir()
        self.output = self.root / "prepared"
        self.tag = "v0.5.3"
        self.commit = "a" * 40
        self.windows = f"PDFWorkbench-{self.tag}-windows-x86_64.zip"
        self.linux = f"PDFWorkbench-{self.tag}-linux-x86_64.tar.gz"
        self.source = f"PDFWorkbench-{self.tag}-source.tar.gz"
        self.files = {
            self.windows: b"exact published windows native archive",
            self.linux: b"exact published linux native archive",
            self.source: b"exact app source archive",
            "qt-everywhere-src-6.11.2.tar.xz": b"compressed Qt source fixture",
            "tiny_1.2.orig.tar.xz": b"exact upstream source fixture",
            "native-sources.json": b'{"schema_version":1,"packages":[]}\n',
        }
        sources = [self.record(name, data) for name, data in self.files.items()
                   if name in ("qt-everywhere-src-6.11.2.tar.xz", "tiny_1.2.orig.tar.xz")]
        self.files["source-manifest.json"] = (json.dumps(sources, indent=2) + "\n").encode()
        for target in ("linux", "windows"):
            self.files[f"PDFWorkbench-{self.tag}-{target}-BUILD-INFO.json"] = (
                json.dumps({"source_commit": self.commit, "app_version": self.tag[1:]}) + "\n").encode()
            self.files[f"PDFWorkbench-{self.tag}-{target}-acceptance.json"] = b'{"result":"PASS"}\n'
        for name, data in self.files.items():
            (self.input / name).write_bytes(data)
        self.write_checksums()

    @staticmethod
    def record(name, data):
        return {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def write_checksums(self):
        checksums = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                            for name, data in sorted(self.files.items()))
        (self.input / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")

    def prepare(self, **kwargs):
        return layout.prepare_assets(self.input, self.output, self.tag, self.commit, **kwargs)

    def test_compact_downloads_preserve_all_original_materials_and_original_checksums(self):
        original = {path.name: path.read_bytes() for path in self.input.iterdir()}
        result = self.prepare()
        self.assertEqual({path.name for path in result["app_assets"]}, {self.windows, self.linux, "SHA256SUMS"})
        self.assertEqual(result["app_checksums"].parent, self.output / "app")
        self.assertEqual(result["app_checksums"].read_text().splitlines(), [
            hashlib.sha256(self.files[name]).hexdigest() + "  " + name
            for name in sorted((self.windows, self.linux))])
        manifest = json.loads(result["materials_manifest"].read_text())
        self.assertEqual(manifest["app_tag"], self.tag)
        self.assertEqual(manifest["app_source_commit"], self.commit)
        self.assertEqual(manifest["sources_release_tag"], "source-archives")
        archive = result["materials_archive"]
        self.assertEqual(manifest["archive"]["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        self.assertEqual(manifest["archive"]["size"], archive.stat().st_size)
        self.assertEqual(archive.suffix, ".tar")
        self.assertEqual(len(list((self.output / "sources").iterdir())), 2)
        with tarfile.open(archive, "r:") as stream:
            actual = {}
            for member in stream:
                self.assertTrue(member.isfile())
                self.assertEqual(member.mode, 0o644)
                self.assertEqual((member.uid, member.gid, member.mtime, member.uname, member.gname), (0, 0, 0, "", ""))
                self.assertTrue(member.name.startswith(manifest["archive"]["prefix"] + "/"))
                name = member.name.rsplit("/", 1)[1]
                actual[name] = stream.extractfile(member).read()
        self.assertEqual(actual, {name: data for name, data in original.items() if name not in (self.windows, self.linux)})
        self.assertEqual(manifest["members"], [self.record(name, data) for name, data in sorted(actual.items())])
        self.assertEqual(manifest["native_assets"], [self.record(name, self.files[name]) for name in (self.windows, self.linux)])
        self.assertEqual(original, {path.name: path.read_bytes() for path in self.input.iterdir()})

    def test_archive_and_manifest_are_deterministic_despite_mtime_and_permission_changes(self):
        first = self.prepare()
        archive_bytes = first["materials_archive"].read_bytes()
        manifest_bytes = first["materials_manifest"].read_bytes()
        for path in self.input.iterdir():
            os.utime(path, (100000, 100000))
            path.chmod(0o600)
        self.output = self.root / "second-layout"
        second = self.prepare()
        self.assertEqual(second["materials_archive"].read_bytes(), archive_bytes)
        self.assertEqual(second["materials_manifest"].read_bytes(), manifest_bytes)

    def test_remote_migration_can_avoid_redownloading_only_the_two_native_archives(self):
        metadata = {}
        for name in (self.windows, self.linux):
            record = self.record(name, self.files[name])
            metadata[name] = {"digest": "sha256:" + record["sha256"], "size": record["size"]}
            (self.input / name).unlink()
        result = self.prepare(asset_metadata=metadata)
        self.assertEqual(result["app_assets"], [result["app_checksums"]])
        self.assertEqual([item["local_path"] for item in result["native_assets"]], [None, None])
        manifest = json.loads(result["materials_manifest"].read_text())
        self.assertEqual({item["filename"] for item in manifest["native_assets"]}, {self.windows, self.linux})

    def test_metadata_cannot_replace_local_native_bytes_or_missing_materials(self):
        cases = {
            "native checksum": {self.windows: {"sha256": "b" * 64, "size": len(self.files[self.windows])}},
            "native size": {self.windows: {"sha256": self.record(self.windows, self.files[self.windows])["sha256"], "size": 1}},
            "foreign file": {self.source: self.record(self.source, self.files[self.source])},
            "missing digest": {self.windows: {"size": 1}},
            "zero size": {self.windows: {"sha256": "a" * 64, "size": 0}},
            "boolean size": {self.windows: {"sha256": "a" * 64, "size": True}},
        }
        for label, metadata in cases.items():
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.prepare(asset_metadata=metadata)
        (self.input / self.source).unlink()
        metadata = {name: self.record(name, self.files[name]) for name in (self.windows, self.linux)}
        with self.assertRaises(ValueError):
            self.prepare(asset_metadata=metadata)

    def test_wrong_remote_native_digest_cannot_authorize_missing_native_archive(self):
        (self.input / self.windows).unlink()
        with self.assertRaisesRegex(ValueError, "Remote native checksum"):
            self.prepare(asset_metadata={self.windows: {"sha256": "f" * 64, "size": 100}})

    def test_tampered_missing_and_foreign_files_fail_before_creating_output(self):
        target = self.input / "tiny_1.2.orig.tar.xz"
        data = target.read_bytes()
        target.write_bytes(b"tampered source")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.prepare()
        target.write_bytes(data)
        target.unlink()
        with self.assertRaises(ValueError):
            self.prepare()
        target.write_bytes(data)
        (self.input / "foreign-not-part-of-the-release.txt").write_text("foreign")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_checksum_inventory_must_be_unambiguous_and_complete(self):
        original = (self.input / "SHA256SUMS").read_text()
        malformed = (
            "", original + original.splitlines()[0] + "\n", "not-a-sha256  file\n",
            original + "a" * 64 + "  ../escape\n",
            original + "a" * 64 + "  /absolute\n",
            original + "a" * 64 + "  SHA256SUMS\n",
            original.replace(self.windows, "Windows-foreign.zip"),
        )
        for checksums in malformed:
            (self.input / "SHA256SUMS").write_text(checksums)
            with self.subTest(checksums=checksums[-100:]), self.assertRaises(ValueError):
                self.prepare()

    def test_source_manifest_must_validate_every_record_and_real_source_bytes(self):
        original = json.loads(self.files["source-manifest.json"])
        cases = [[], {}, original + [original[0]],
                 [dict(original[0], filename="../escape")],
                 [dict(original[0], filename=self.windows)],
                 [dict(original[0], filename="missing.tar.xz")],
                 [dict(original[0], sha256="b" * 64)],
                 [dict(original[0], size=1)],
                 [dict(original[0], size=True)],
                 [dict(original[0], size=0)]]
        for sources in cases:
            self.files["source-manifest.json"] = json.dumps(sources).encode()
            (self.input / "source-manifest.json").write_bytes(self.files["source-manifest.json"])
            self.write_checksums()
            with self.subTest(sources=sources), self.assertRaises(ValueError):
                self.prepare()

    def test_duplicate_json_keys_cannot_choose_a_convenient_source_hash(self):
        original = json.loads(self.files["source-manifest.json"])[0]
        payload = '[{"filename":' + json.dumps(original["filename"]) + ',"size":1,"size":' + str(original["size"]) + ',"sha256":' + json.dumps(original["sha256"]) + '}]'
        self.files["source-manifest.json"] = payload.encode()
        (self.input / "source-manifest.json").write_bytes(payload.encode())
        self.write_checksums()
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            self.prepare()

    def test_filesystem_links_folders_unsafe_names_and_case_aliases_are_rejected(self):
        for filename in ("../escape", "/absolute", "file\\path", "bad\nname", ".hidden", "double..dot"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                layout._plain_name(filename)
        foreign = self.input / "foreign"
        foreign.mkdir()
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.prepare()
        foreign.rmdir()
        try:
            foreign.symlink_to(self.input / self.source)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaisesRegex(ValueError, "regular files"):
                self.prepare()
            foreign.unlink()
        (self.input / "Source-Manifest.json").write_text("case alias")
        if len([path for path in self.input.iterdir() if path.name.casefold() == "source-manifest.json"]) == 2:
            with self.assertRaisesRegex(ValueError, "Case-colliding"):
                self.prepare()
        else:
            # A case-insensitive filesystem opens the existing file; its changed
            # content must still fail the exact-original-byte checksum gate.
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                self.prepare()

    def test_tag_commit_build_identity_and_existing_outputs_are_guarded(self):
        for tag in ("../v0.5.3", "source-archives", "v0.5"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                layout.prepare_assets(self.input, self.output, tag, self.commit)
        for commit in ("short", "G" * 40):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                layout.prepare_assets(self.input, self.output, self.tag, commit)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            layout.prepare_assets(self.input, self.output, self.tag, "b" * 40)
        self.output.mkdir()
        sentinel = self.output / "keep-me"
        sentinel.write_text("original")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(sentinel.read_text(), "original")

    def test_output_must_be_separate_from_inputs(self):
        for output in (self.input / "nested", self.root):
            with self.subTest(output=output), self.assertRaises(ValueError):
                layout.prepare_assets(self.input, output, self.tag, self.commit)

    def test_size_limit_prevents_publication_and_does_not_modify_input(self):
        before = (self.input / "SHA256SUMS").read_bytes()
        with patch.object(layout, "MAX_ARCHIVE_BYTES", 512), self.assertRaisesRegex(ValueError, "limit"):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.assertEqual((self.input / "SHA256SUMS").read_bytes(), before)

    def stat_record(self, **changes):
        info = (self.input / "source-manifest.json").lstat()
        fields = ("st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        result = {name: getattr(info, name) for name in fields}
        result.update(changes)
        return SimpleNamespace(**result)

    def test_windows_path_creation_time_and_descriptor_change_time_can_differ(self):
        # CPython #157671: both API calls identify the exact same NTFS file but
        # expose different meanings for st_ctime_ns. Each API stays consistent.
        by_path = self.stat_record(st_ctime_ns=1000)
        by_fd = self.stat_record(st_ctime_ns=2000)
        target = self.input / "source-manifest.json"
        with patch.object(Path, "lstat", side_effect=[by_path, by_path]), \
                patch.object(layout.os, "fstat", side_effect=[by_fd, by_fd]):
            with layout._regular_file(target) as (stream, opened):
                self.assertEqual(stream.read(), self.files[target.name])
                self.assertIs(opened, by_fd)

    def test_metadata_changes_within_either_stat_api_still_reject_input(self):
        target = self.input / "source-manifest.json"
        by_path = self.stat_record(st_ctime_ns=1000)
        by_fd = self.stat_record(st_ctime_ns=2000)
        changed_path = self.stat_record(st_ctime_ns=1001)
        changed_fd = self.stat_record(st_ctime_ns=2001)
        for paths, descriptors, message in (
            ([by_path, changed_path], [by_fd, by_fd], "replaced while reading"),
            ([by_path, by_path], [by_fd, changed_fd], "changed while reading"),
        ):
            with self.subTest(message=message), \
                    patch.object(Path, "lstat", side_effect=paths), \
                    patch.object(layout.os, "fstat", side_effect=descriptors), \
                    self.assertRaisesRegex(ValueError, message):
                with layout._regular_file(target) as (stream, _):
                    stream.read()

    def test_cross_api_file_id_size_and_modification_time_must_still_match(self):
        target = self.input / "source-manifest.json"
        by_path = self.stat_record(st_ctime_ns=1000)
        for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns"):
            by_fd = self.stat_record(st_ctime_ns=2000, **{field: getattr(by_path, field) + 1})
            with self.subTest(field=field), \
                    patch.object(Path, "lstat", return_value=by_path), \
                    patch.object(layout.os, "fstat", return_value=by_fd), \
                    self.assertRaisesRegex(ValueError, "changed while opening"):
                with layout._regular_file(target):
                    self.fail("A mismatched descriptor must not be exposed")

    def test_same_bytes_size_and_mtime_replacement_before_open_is_rejected(self):
        target = self.input / "source-manifest.json"
        original_bytes = target.read_bytes()
        original_stat = target.stat()
        original_open = layout.os.open
        saved = self.root / "preserved-original-manifest.json"

        def replace_before_open(path, flags, *args, **kwargs):
            # This happens before opening, so it works without Windows delete
            # sharing and preserves both original and foreign file contents.
            target.rename(saved)
            target.write_bytes(original_bytes)
            os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            return original_open(path, flags, *args, **kwargs)

        with patch.object(layout.os, "open", side_effect=replace_before_open), \
                self.assertRaisesRegex(ValueError, "changed while opening"):
            layout._hash_file(target)
        self.assertEqual(saved.read_bytes(), original_bytes)
        self.assertEqual(target.read_bytes(), original_bytes)

    def test_readback_rejects_duplicate_missing_links_and_changed_member_bytes(self):
        result = self.prepare()
        prefix = json.loads(result["materials_manifest"].read_text())["archive"]["prefix"]
        item = result["members"][0]
        cases = ("duplicate", "missing", "symlink", "tampered", "outside")
        for case in cases:
            archive = self.root / (case + ".tar")
            with tarfile.open(archive, "w:") as stream:
                if case != "missing":
                    info = tarfile.TarInfo(prefix + "/" + item["filename"])
                    info.mode = 0o644
                    if case == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = "../../outside"
                        stream.addfile(info)
                    else:
                        data = (self.input / item["filename"]).read_bytes()
                        if case == "tampered":
                            data = b"x" * len(data)
                        if case == "outside":
                            info.name = "../outside"
                        info.size = len(data)
                        stream.addfile(info, io.BytesIO(data))
                        if case == "duplicate":
                            stream.addfile(info, io.BytesIO(data))
            with self.subTest(case=case), self.assertRaises(ValueError):
                layout._verify_archive(archive, prefix, result["members"])


if __name__ == "__main__":
    unittest.main()
