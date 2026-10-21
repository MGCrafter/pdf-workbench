"""Publishing may simplify app assets only after exact public source proof."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_layout = load_script("publishing_layout", PROJECT / "packaging/release_layout.py")
with patch.dict(sys.modules, {"release_layout": release_layout}):
    publisher = load_script("release_publishing", PROJECT / "packaging/publish_release.py")


class FakeGithub:
    """Small stateful GitHub boundary, with real asset bytes and stable IDs."""

    def __init__(self, repo, tag, files):
        self.repo, self.tag = repo, tag
        self.events = []
        self.next_id = 100
        self.app = {"id": 10, "tag_name": tag, "draft": False, "prerelease": False,
                    "body": "App release notes"}
        self.sources = {"id": 20, "tag_name": "source-archives", "draft": True,
                        "prerelease": False, "body": publisher.SOURCE_MARKER + "\nSources"}
        self.inventories = {10: {}, 20: {}}
        self.payload = {}
        self.fail_source_upload = False
        self.fail_app_upload = False
        self.corrupt_source_digest = False
        self.fail_public_head = False
        self.mutate_after_proof = None
        for name, data in files.items():
            self.add_asset(10, name, data)
        self.original = copy.deepcopy(self.inventories[10])

    def add_asset(self, release_id, name, data):
        self.next_id += 1
        item = {"id": self.next_id, "name": name, "size": len(data), "state": "uploaded",
                "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "browser_download_url": f"https://github.com/{self.repo}/releases/download/{release_id}/{name}"}
        self.inventories[release_id][name] = item
        self.payload[item["id"]] = data
        return item

    def api(self, path, *, method="GET", payload=None, paged=False):
        self.events.append(("api", method, path, copy.deepcopy(payload)))
        base = "repos/" + self.repo
        if path == base + "/releases?per_page=100":
            return [[copy.deepcopy(self.app), copy.deepcopy(self.sources)]]
        if path == base + "/releases/tags/" + self.tag:
            return copy.deepcopy(self.app)
        for release in (self.app, self.sources):
            endpoint = base + "/releases/" + str(release["id"])
            if path == endpoint + "/assets?per_page=100":
                items = list(copy.deepcopy(self.inventories[release["id"]]).values())
                return [items[:2], items[2:]] if paged else items
            if path == endpoint:
                if method == "PATCH":
                    release.update(payload)
                return copy.deepcopy(release)
        prefix = base + "/releases/assets/"
        if path.startswith(prefix):
            asset_id = int(path[len(prefix):])
            for inventory in self.inventories.values():
                for name, item in list(inventory.items()):
                    if item["id"] == asset_id:
                        result = copy.deepcopy(item)
                        if method == "DELETE":
                            del inventory[name]
                        return result
            raise ValueError("Asset no longer exists")
        raise AssertionError(f"Unexpected GitHub API: {method} {path}")

    def gh(self, *arguments, **kwargs):
        args = tuple(map(str, arguments))
        self.events.append(("gh", *args))
        if args[:2] == ("release", "upload"):
            tag, path = args[2], Path(args[3])
            release_id = 20 if tag == "source-archives" else 10
            if release_id == 20 and self.fail_source_upload:
                raise RuntimeError("Cloud source upload failed")
            if release_id == 10 and self.fail_app_upload:
                raise RuntimeError("Runner stopped before checksum upload")
            if path.name in self.inventories[release_id] and "--clobber" not in args:
                raise RuntimeError("GitHub rejected an exclusive upload: asset name already exists")
            item = self.add_asset(release_id, path.name, path.read_bytes())
            if release_id == 20 and self.corrupt_source_digest:
                item["digest"] = "sha256:" + "f" * 64
            return ""
        if args[:2] == ("release", "create"):
            return "created"
        raise AssertionError("Unexpected GitHub command: " + repr(args))

    def download(self, item, destination):
        self.events.append(("download", item["name"]))
        destination.write_bytes(self.payload[item["id"]])

    def public_head(self, item):
        self.events.append(("public", item["name"]))
        if self.fail_public_head:
            raise ValueError("Public source download unavailable")
        if self.mutate_after_proof is not None:
            mutation, self.mutate_after_proof = self.mutate_after_proof, None
            mutation()

    def app_mutations(self):
        return [event for event in self.events if (
            event[0] == "gh" and event[1:3] in (("release", "upload"), ("release", "create"))
            and event[3] == self.tag) or (
            event[0] == "api" and event[1] in ("PATCH", "DELETE")
            and (event[2].endswith("/releases/10") or "/releases/assets/" in event[2]))]


class ReleasePublishingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo, self.tag, self.commit = "example/pdf-workbench", "v0.5.3", "a" * 40
        self.windows = f"PDFWorkbench-{self.tag}-windows-x86_64.zip"
        self.linux = f"PDFWorkbench-{self.tag}-linux-x86_64.tar.gz"
        source = "tiny_1.2.orig.tar.xz"
        self.files = {
            self.windows: b"tested Windows archive bytes",
            self.linux: b"tested Linux archive bytes",
            f"PDFWorkbench-{self.tag}-source.tar.gz": b"full app source bytes",
            source: b"full corresponding dependency source",
        }
        record = {"filename": source, "sha256": hashlib.sha256(self.files[source]).hexdigest(),
                  "size": len(self.files[source])}
        self.files["source-manifest.json"] = (json.dumps([record]) + "\n").encode()
        self.files["SHA256SUMS"] = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                                               for name, data in sorted(self.files.items())).encode()
        self.fake = FakeGithub(self.repo, self.tag, self.files)
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.contexts.enter_context(patch.object(publisher, "api", self.fake.api))
        self.contexts.enter_context(patch.object(publisher, "gh", self.fake.gh))
        self.contexts.enter_context(patch.object(publisher, "download", self.fake.download))
        self.contexts.enter_context(patch.object(publisher, "public_head", self.fake.public_head))
        self.contexts.enter_context(patch.object(publisher, "root_commit", return_value="b" * 40))
        self.contexts.enter_context(patch.object(publisher.subprocess, "check_output", return_value=self.commit + "\n"))
        self.contexts.enter_context(patch.object(publisher.time, "sleep"))

    def consolidate(self):
        return publisher.consolidate(self.repo, self.tag, self.root / "migration")

    def prepared(self):
        incoming = self.root / "input"
        incoming.mkdir()
        for name, data in self.files.items():
            (incoming / name).write_bytes(data)
        return release_layout.prepare_assets(incoming, self.root / "layout", self.tag, self.commit)

    def test_consolidation_preserves_native_ids_and_bytes_and_leaves_exact_app_allowlist(self):
        self.consolidate()
        after = self.fake.inventories[10]
        self.assertEqual(set(after), {self.windows, self.linux, "SHA256SUMS"})
        for name in (self.windows, self.linux):
            self.assertEqual(after[name], self.fake.original[name])
            self.assertEqual(self.fake.payload[after[name]["id"]], self.files[name])
        checksum_data = self.fake.payload[after["SHA256SUMS"]["id"]].decode()
        self.assertEqual(checksum_data.splitlines(), [
            self.fake.original[name]["digest"][7:] + "  " + name
            for name in sorted((self.windows, self.linux))])
        self.assertEqual(len(self.fake.inventories[20]), 2)
        self.assertFalse(self.fake.sources["draft"])
        self.assertEqual(self.fake.sources["make_latest"], "false")
        events = self.fake.events
        proof = [index for index, event in enumerate(events) if event[0] == "public"]
        app_mutation = [index for index, event in enumerate(events) if event in self.fake.app_mutations()]
        self.assertEqual(len(proof), 2)
        self.assertLess(max(proof), min(app_mutation))
        native_ids = {self.fake.original[name]["id"] for name in (self.windows, self.linux)}
        deleted_ids = {int(event[2].rsplit("/", 1)[1]) for event in events
                       if event[0] == "api" and event[1] == "DELETE"}
        self.assertFalse(native_ids & deleted_ids)
        self.assertIn(self.fake.original["SHA256SUMS"]["id"], deleted_ids)
        checksum_upload = next(event for event in events if event[:4] == ("gh", "release", "upload", self.tag))
        self.assertNotIn("--clobber", checksum_upload)

    def test_failed_sources_upload_has_no_app_mutation_or_deletion(self):
        self.fake.fail_source_upload = True
        with self.assertRaises(RuntimeError):
            self.consolidate()
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertEqual(self.fake.inventories[10], self.fake.original)

    def test_server_digest_mismatch_has_no_app_mutation_or_deletion(self):
        self.fake.corrupt_source_digest = True
        with self.assertRaisesRegex(ValueError, "Published bytes differ"):
            self.consolidate()
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertEqual(self.fake.inventories[10], self.fake.original)

    def test_private_or_unavailable_sources_do_not_authorize_checksum_replacement(self):
        self.fake.fail_public_head = True
        with self.assertRaisesRegex(ValueError, "Public source"):
            self.consolidate()
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertEqual(self.fake.inventories[10], self.fake.original)

    def test_new_foreign_asset_during_publication_aborts_cleanup(self):
        self.fake.mutate_after_proof = lambda: self.fake.add_asset(10, "foreign-added.zip", b"someone else's upload")
        with self.assertRaisesRegex(ValueError, "concurrently"):
            self.consolidate()
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertIn("foreign-added.zip", self.fake.inventories[10])

    def test_replaced_native_asset_during_publication_aborts_cleanup(self):
        self.fake.mutate_after_proof = lambda: self.fake.add_asset(10, self.windows, self.files[self.windows])
        with self.assertRaisesRegex(ValueError, "concurrently|identity changed"):
            self.consolidate()
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertNotEqual(self.fake.inventories[10][self.windows]["id"], self.fake.original[self.windows]["id"])

    def test_versioned_source_archive_is_never_clobbered(self):
        prepared = self.prepared()
        path = prepared["materials_archive"]
        self.fake.add_asset(20, path.name, b"different source archive using the same version")
        with self.assertRaisesRegex(ValueError, "Published bytes differ"):
            publisher.publish_materials(self.repo, prepared, "b" * 40)
        self.assertFalse(any(event[0] == "gh" for event in self.fake.events))
        self.assertEqual(self.fake.app_mutations(), [])

    def test_matching_versioned_sources_are_reverified_without_reupload(self):
        prepared = self.prepared()
        for path in (prepared["materials_archive"], prepared["materials_manifest"]):
            self.fake.add_asset(20, path.name, path.read_bytes())
        publisher.publish_materials(self.repo, prepared, "b" * 40)
        self.assertFalse(any(event[0] == "gh" for event in self.fake.events))
        self.assertEqual(len([event for event in self.fake.events if event[0] == "public"]), 2)

    def test_normal_publish_proves_sources_before_creating_app_with_only_native_and_checksums(self):
        self.fake.app["tag_name"] = "v0.5.2"
        incoming = self.root / "incoming"
        incoming.mkdir()
        for name, data in self.files.items():
            (incoming / name).write_bytes(data)
        notes = self.root / "notes.md"
        notes.write_text("Release feature notes\n")
        publisher.create(self.repo, self.tag, incoming, notes)
        create = [event for event in self.fake.events if event[:3] == ("gh", "release", "create")]
        self.assertEqual(len(create), 1)
        event = create[0]
        self.assertEqual(event[3], self.tag)
        positional_paths = event[4:event.index("--repo")]
        self.assertEqual({Path(path).name for path in positional_paths}, {self.windows, self.linux, "SHA256SUMS"})
        self.assertEqual(len(positional_paths), 3)
        self.assertIn("--latest", event)
        self.assertNotIn("--latest=false", event)
        proof = [index for index, item in enumerate(self.fake.events) if item[0] == "public"]
        self.assertEqual(len(proof), 2)
        self.assertLess(max(proof), self.fake.events.index(event))

    def test_recovery_of_older_version_does_not_replace_newer_latest_release(self):
        self.fake.app["tag_name"] = "v0.6.0"
        incoming = self.root / "incoming-older-version"
        incoming.mkdir()
        for name, data in self.files.items():
            (incoming / name).write_bytes(data)
        notes = self.root / "older-notes.md"
        notes.write_text("Recovered older release notes\n")
        publisher.create(self.repo, self.tag, incoming, notes)
        create = [event for event in self.fake.events if event[:3] == ("gh", "release", "create")]
        self.assertEqual(len(create), 1)
        self.assertIn("--latest=false", create[0])
        self.assertNotIn("--latest", create[0])

    def test_unmanaged_source_release_is_never_repurposed(self):
        self.fake.sources["body"] = "Existing manually managed release"
        with self.assertRaisesRegex(ValueError, "not managed"):
            publisher.publish_materials(self.repo, self.prepared(), "b" * 40)
        self.assertFalse(any(event[0] == "gh" for event in self.fake.events))

    def test_snapshot_guard_detects_same_name_size_digest_asset_replacement(self):
        before = copy.deepcopy(self.fake.original)
        self.fake.add_asset(10, self.windows, self.files[self.windows])
        with self.assertRaisesRegex(ValueError, "concurrently|identity changed"):
            publisher.check_snapshot(self.repo, self.fake.app, before, {self.windows, self.linux})

    def test_cleanup_resumes_from_public_receipt_without_reuploading_versioned_sources(self):
        original_api = self.fake.api
        deleted = []

        def interrupted_api(path, *, method="GET", **kwargs):
            result = original_api(path, method=method, **kwargs)
            if method == "DELETE" and not path.endswith("/" + str(self.fake.original["SHA256SUMS"]["id"])):
                deleted.append(path)
                raise RuntimeError("Runner stopped after its first successful deletion")
            return result

        with patch.object(publisher, "api", interrupted_api), self.assertRaisesRegex(RuntimeError, "Runner stopped"):
            self.consolidate()
        self.assertEqual(len(deleted), 1)
        self.assertGreater(len(self.fake.inventories[10]), 3)
        source_ids = {name: item["id"] for name, item in self.fake.inventories[20].items()}
        checksum_id = self.fake.inventories[10]["SHA256SUMS"]["id"]
        self.fake.events.clear()
        publisher.consolidate(self.repo, self.tag, self.root / "resumed-run")
        self.assertEqual(set(self.fake.inventories[10]), {self.windows, self.linux, "SHA256SUMS"})
        self.assertEqual(source_ids, {name: item["id"] for name, item in self.fake.inventories[20].items()})
        self.assertEqual(checksum_id, self.fake.inventories[10]["SHA256SUMS"]["id"])
        self.assertFalse(any(event[0] == "gh" and event[1:3] == ("release", "upload")
                             for event in self.fake.events))
        for name in (self.windows, self.linux):
            self.assertEqual(self.fake.inventories[10][name], self.fake.original[name])
        self.assertEqual(len([event for event in self.fake.events if event[0] == "public"]), 2)

    def test_receipt_bound_to_another_app_commit_cannot_resume_deletion(self):
        self.consolidate()
        self.fake.inventories[10] = copy.deepcopy(self.fake.original)
        manifest_name = f"PDFWorkbench-{self.tag}-sources-and-reports.json"
        item = self.fake.inventories[20][manifest_name]
        receipt = json.loads(self.fake.payload[item["id"]])
        receipt["app_source_commit"] = "c" * 40
        self.fake.add_asset(20, manifest_name, json.dumps(receipt).encode())
        self.fake.events.clear()
        with self.assertRaisesRegex(ValueError, "does not identify"):
            publisher.consolidate(self.repo, self.tag, self.root / "invalid-receipt-run")
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertEqual(self.fake.inventories[10], self.fake.original)

    def test_incomplete_public_receipt_cannot_resume_deletion(self):
        self.consolidate()
        self.fake.inventories[10] = copy.deepcopy(self.fake.original)
        manifest_name = f"PDFWorkbench-{self.tag}-sources-and-reports.json"
        item = self.fake.inventories[20][manifest_name]
        receipt = json.loads(self.fake.payload[item["id"]])
        receipt["members"].pop()
        self.fake.add_asset(20, manifest_name, json.dumps(receipt).encode())
        self.fake.events.clear()
        with self.assertRaisesRegex(ValueError, "does not preserve"):
            publisher.consolidate(self.repo, self.tag, self.root / "incomplete-receipt-run")
        self.assertEqual(self.fake.app_mutations(), [])
        self.assertEqual(self.fake.inventories[10], self.fake.original)

    def test_replaced_extra_is_checked_again_by_id_immediately_before_deletion(self):
        original_api = self.fake.api
        extra = f"PDFWorkbench-{self.tag}-source.tar.gz"
        endpoint = f"repos/{self.repo}/releases/assets/{self.fake.original[extra]['id']}"

        def mutate_before_delete(path, *, method="GET", **kwargs):
            if path == endpoint and method == "GET":
                self.fake.add_asset(10, extra, self.files[extra])
            return original_api(path, method=method, **kwargs)

        with patch.object(publisher, "api", mutate_before_delete), self.assertRaises(ValueError):
            self.consolidate()
        self.assertIn(extra, self.fake.inventories[10])
        self.assertNotEqual(self.fake.inventories[10][extra]["id"], self.fake.original[extra]["id"])
        deleted_ids = {int(event[2].rsplit("/", 1)[1]) for event in self.fake.events
                       if event[0] == "api" and event[1] == "DELETE"}
        self.assertEqual(deleted_ids, {self.fake.original["SHA256SUMS"]["id"]})

    def test_cleanup_resumes_after_checksum_deletion_and_failed_upload(self):
        self.fake.fail_app_upload = True
        with self.assertRaisesRegex(RuntimeError, "before checksum upload"):
            self.consolidate()
        self.assertNotIn("SHA256SUMS", self.fake.inventories[10])
        self.assertEqual(set(self.fake.inventories[10]), set(self.fake.original) - {"SHA256SUMS"})
        source_ids = {name: item["id"] for name, item in self.fake.inventories[20].items()}
        self.fake.fail_app_upload = False
        self.fake.events.clear()
        publisher.consolidate(self.repo, self.tag, self.root / "resume-missing-checksum")
        self.assertEqual(set(self.fake.inventories[10]), {self.windows, self.linux, "SHA256SUMS"})
        self.assertEqual(source_ids, {name: item["id"] for name, item in self.fake.inventories[20].items()})
        for name in (self.windows, self.linux):
            self.assertEqual(self.fake.inventories[10][name], self.fake.original[name])
        uploads = [event for event in self.fake.events if event[:3] == ("gh", "release", "upload")]
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0][3], self.tag)
        self.assertNotIn("--clobber", uploads[0])
        proofs = [index for index, event in enumerate(self.fake.events) if event[0] == "public"]
        self.assertEqual(len(proofs), 2)
        self.assertLess(max(proofs), self.fake.events.index(uploads[0]))

    def test_foreign_checksum_uploaded_in_replacement_gap_is_preserved(self):
        original_api = self.fake.api
        endpoint = f"repos/{self.repo}/releases/assets/{self.fake.original['SHA256SUMS']['id']}"
        foreign = b"Someone else's concurrently uploaded checksum"

        def add_foreign_in_gap(path, *, method="GET", **kwargs):
            result = original_api(path, method=method, **kwargs)
            if path == endpoint and method == "DELETE":
                self.fake.add_asset(10, "SHA256SUMS", foreign)
            return result

        with patch.object(publisher, "api", add_foreign_in_gap), self.assertRaisesRegex(RuntimeError, "exclusive upload"):
            self.consolidate()
        checksum = self.fake.inventories[10]["SHA256SUMS"]
        self.assertEqual(self.fake.payload[checksum["id"]], foreign)
        for name in (self.windows, self.linux):
            self.assertEqual(self.fake.inventories[10][name], self.fake.original[name])
        deleted_ids = {int(event[2].rsplit("/", 1)[1]) for event in self.fake.events
                       if event[0] == "api" and event[1] == "DELETE"}
        self.assertEqual(deleted_ids, {self.fake.original["SHA256SUMS"]["id"]})


if __name__ == "__main__":
    unittest.main()
