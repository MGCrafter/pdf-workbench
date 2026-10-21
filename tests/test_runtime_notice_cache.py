"""The original locked LGPL text remains available when its upstream is down."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_cached_notices", ROOT / "packaging/collect_notices.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)
URL = "https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt"
RELATIVE = "licenses/upstream/runtime/LGPL-2.1.txt"
DIGEST = "20e50fe7aae3e56378ebf0417d9de904f55a0e61e4df315333e632a4d3555d95"


class RuntimeNoticeCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "notices"
        self.lock = notices.copy_locked_notices(self.output)
        self.original = (ROOT / RELATIVE).read_bytes()
        self.copied = self.output / RELATIVE.removeprefix("licenses/")

    def tearDown(self):
        self.temp.cleanup()

    def test_canonical_original_and_lock_are_preserved_without_a_network_request(self):
        records = [item for item in self.lock["files"] if item["path"] == RELATIVE]
        self.assertEqual(records, [{"path": RELATIVE, "sha256": DIGEST, "source_url": URL}])
        self.assertEqual(hashlib.sha256(self.original).hexdigest(), DIGEST)
        self.assertIn(b"Version 2.1, February 1999", self.original)
        self.assertIn(b"Free Software Foundation, Inc.", self.original)
        with patch.object(notices, "fetch", side_effect=AssertionError("upstream is unavailable")) as fetch:
            result = notices.fetch_runtime_notice(URL, self.output, self.lock)
        self.assertEqual(result, self.original)
        self.assertEqual(result, self.copied.read_bytes())
        fetch.assert_not_called()

    def test_missing_or_altered_original_fails_without_remote_fallback(self):
        with patch.object(notices, "fetch", side_effect=AssertionError("must not hide an altered original")):
            self.copied.write_bytes(b"modified license")
            with self.assertRaisesRegex(notices.IncompleteNotices, "Missing/modified"):
                notices.fetch_runtime_notice(URL, self.output, self.lock)
            self.copied.unlink()
            with self.assertRaisesRegex(notices.IncompleteNotices, "Missing/modified"):
                notices.fetch_runtime_notice(URL, self.output, self.lock)

    def test_missing_duplicate_or_changed_provenance_fails_closed(self):
        for kind in ("missing", "duplicate", "changed-url"):
            lock = copy.deepcopy(self.lock)
            entry = next(item for item in lock["files"] if item["path"] == RELATIVE)
            if kind == "missing":
                lock["files"].remove(entry)
            elif kind == "duplicate":
                lock["files"].append(dict(entry))
            else:
                entry["source_url"] = "https://example.invalid/substituted"
            with self.subTest(kind=kind), patch.object(notices, "fetch") as fetch:
                with self.assertRaisesRegex(notices.IncompleteNotices, "canonical provenance"):
                    notices.fetch_runtime_notice(URL, self.output, lock)
                fetch.assert_not_called()

    def test_required_lock_entry_cannot_be_dropped_from_collection(self):
        lock = copy.deepcopy(self.lock)
        lock["files"] = [item for item in lock["files"] if item["path"] != RELATIVE]
        path = Path(self.temp.name) / "incomplete-lock.json"
        path.write_text(json.dumps(lock))
        with patch.object(notices, "LOCK", path), self.assertRaisesRegex(notices.IncompleteNotices, "missing from lock"):
            notices.copy_locked_notices(self.output)

    def test_other_runtime_requests_still_use_normal_verified_network_fetch(self):
        url = "https://pypi.org/pypi/cryptography/50.0.1/json"
        with patch.object(notices, "fetch", return_value=b"ordinary upstream metadata") as fetch:
            self.assertEqual(notices.fetch_runtime_notice(url, self.output, self.lock), b"ordinary upstream metadata")
        fetch.assert_called_once_with(url)

    def test_collector_passes_the_offline_verified_notice_to_runtime_audit(self):
        received = []
        def collect_runtime(output, fetch):
            received.append(fetch(URL))
            return {"schema_version": 1, "source_archives": []}
        runtime = SimpleNamespace(collect_runtime_notices=collect_runtime, merge_source_records=lambda records: records,
                                  RuntimeAuditError=RuntimeError)
        with (patch.object(notices.sys, "argv", ["collect_notices.py", "--output", str(self.output)]),
              patch.object(notices, "collect_distributions", return_value=({"Python": "3.14.8"}, [])),
              patch.object(notices, "collect_python"), patch.object(notices, "collect_system_notices"),
              patch.object(notices, "source_archives", return_value=[]),
              patch.object(notices, "runtime_wheels", return_value=runtime),
              patch.object(notices, "fetch", side_effect=AssertionError("no upstream needed"))):
            self.assertEqual(notices.main(), 0)
        self.assertEqual(received, [self.original])
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["files"]["upstream/runtime/LGPL-2.1.txt"], DIGEST)


if __name__ == "__main__":
    unittest.main()
