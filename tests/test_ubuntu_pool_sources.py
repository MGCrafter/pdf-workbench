"""Exact Ubuntu pool descriptors avoid unnecessary publication-API latency."""
from __future__ import annotations

import hashlib
import importlib.util
import io
from pathlib import Path
from unittest.mock import patch
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ubuntu_pool_notices", ROOT / "packaging/collect_notices.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


def descriptor(*, source="libdemo", version="2:1.2-0ubuntu1", rows=None):
    rows = rows if rows is not None else ["a" * 64 + " 123 libdemo_1.2.orig.tar.xz",
                                          "b" * 64 + " 45 libdemo_1.2-0ubuntu1.debian.tar.xz"]
    return (f"Source: {source}\nVersion: {version}\nChecksums-Sha256:\n " + "\n ".join(rows) + "\n").encode()


class UbuntuPoolSourceTests(unittest.TestCase):
    package = {"source_package": "libdemo", "source_version": "2:1.2-0ubuntu1"}

    def missing(self, url):
        return urllib.error.HTTPError(url, 404, "not found", {}, None)

    def test_pool_first_preserves_epoch_identity_and_published_hashes(self):
        data = descriptor()
        url = "https://archive.ubuntu.com/ubuntu/pool/main/libd/libdemo/libdemo_1.2-0ubuntu1.dsc"
        with patch.object(notices, "fetch", return_value=data) as fetch, patch.object(notices, "_launchpad_package_sources") as api:
            records = notices._ubuntu_package_sources(self.package)
        fetch.assert_called_once_with(url, limit=512 * 1024, timeout=12, attempts=1)
        api.assert_not_called()
        self.assertEqual({record["version"] for record in records}, {"2:1.2-0ubuntu1"})
        self.assertEqual(records[0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(records[1]["sha256"], "a" * 64)
        self.assertEqual(records[1]["size"], 123)
        self.assertEqual(records[1]["url"], url.rsplit("/", 1)[0] + "/libdemo_1.2.orig.tar.xz")
        self.assertEqual(records[1]["digest_source"], url)

    def test_archive_timeout_uses_security_without_waiting_for_default_retries(self):
        seen = []
        def fetch(url, **kwargs):
            seen.append((url, kwargs))
            if "archive.ubuntu.com" in url:
                raise TimeoutError("temporary archive timeout")
            return descriptor()
        with patch.object(notices, "fetch", side_effect=fetch), patch.object(notices, "_launchpad_package_sources") as api:
            records = notices._ubuntu_package_sources(self.package)
        self.assertEqual(len(seen), 2)
        self.assertTrue(records[0]["url"].startswith("https://security.ubuntu.com/"))
        self.assertTrue(all(kwargs["attempts"] == 1 and kwargs["timeout"] == 12 for _, kwargs in seen))
        api.assert_not_called()

    def test_unavailable_host_is_not_retried_for_every_other_component(self):
        seen = []
        def fetch(url, **kwargs):
            seen.append(url)
            if "archive.ubuntu.com" in url:
                raise TimeoutError("host unavailable")
            if "/main/" in url:
                raise self.missing(url)
            return descriptor()
        with patch.object(notices, "fetch", side_effect=fetch):
            records = notices._ubuntu_pool_sources(self.package)
        self.assertEqual(sum("archive.ubuntu.com" in url for url in seen), 1)
        self.assertIn("/pool/universe/", records[0]["url"])

    def test_historical_missing_pool_version_uses_exact_launchpad_fallback(self):
        def absent(url, **kwargs): raise self.missing(url)
        with patch.object(notices, "fetch", side_effect=absent) as fetch, patch.object(notices, "_launchpad_package_sources", return_value=[{"exact": True}]) as api:
            self.assertEqual(notices._ubuntu_package_sources(self.package), [{"exact": True}])
        self.assertEqual(fetch.call_count, 8)
        api.assert_called_once_with(self.package)
        self.assertTrue(all(call.kwargs["attempts"] == 1 for call in fetch.call_args_list))

    def test_bad_descriptor_never_falls_back_to_a_newer_or_other_provider(self):
        cases = (descriptor(version="2:1.3-newer"), descriptor(source="other"),
                 b"<html>successful mirror error page</html>", b"Source: libdemo\nVersion: 2:1.2-0ubuntu1\n")
        for data in cases:
            with self.subTest(data=data[:50]), patch.object(notices, "fetch", return_value=data) as fetch, patch.object(notices, "_launchpad_package_sources") as api:
                with self.assertRaises(notices.IncompleteNotices):
                    notices._ubuntu_package_sources(self.package)
                self.assertEqual(fetch.call_count, 1)
                api.assert_not_called()

    def test_invalid_checksum_filenames_sizes_and_duplicates_are_rejected(self):
        valid = "a" * 64 + " 123 source.tar.xz"
        cases = (["a" * 64 + " 123 ../escape.tar.xz"], ["a" * 64 + " 123 path/source.tar.xz"],
                 ["a" * 64 + " 0 source.tar.xz"], ["a" * 64 + " -1 source.tar.xz"],
                 ["not-a-sha256 123 source.tar.xz"], [valid, valid], ["malformed row"],
                 ["a" * 64 + " 999999999999999999999999 source.tar.xz"])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(notices.IncompleteNotices):
                notices._ubuntu_descriptor(self.package, "libdemo_1.2-0ubuntu1.dsc", descriptor(rows=rows),
                                           "https://archive.ubuntu.com/ubuntu/pool/main/libd/libdemo/libdemo_1.2-0ubuntu1.dsc")

    def test_ambiguous_headers_and_insecure_archive_urls_fail(self):
        for data in (descriptor() + b"Checksums-Sha256:\n " + b"c" * 64 + b" 1 other.tar.xz\n",
                     b"Source: libdemo\n" + descriptor(), b"Version: 2:1.2-0ubuntu1\n" + descriptor()):
            with self.subTest(data=data[:40]), self.assertRaises(notices.IncompleteNotices):
                notices._ubuntu_descriptor(self.package, "source.dsc", data, "https://archive.ubuntu.com/source.dsc")
        with self.assertRaisesRegex(notices.IncompleteNotices, "HTTPS"):
            notices._ubuntu_descriptor(self.package, "source.dsc", descriptor(), "http://archive.ubuntu.com/source.dsc")

    def test_certificate_validation_and_forbidden_responses_are_not_hidden(self):
        error = urllib.error.HTTPError("https://archive.ubuntu.com/x", 403, "forbidden", {}, io.BytesIO())
        with patch.object(notices, "fetch", side_effect=error), patch.object(notices, "_launchpad_package_sources") as api:
            with self.assertRaises(urllib.error.HTTPError):
                notices._ubuntu_package_sources(self.package)
            api.assert_not_called()
        error.close()

    def test_signed_crlf_descriptor_retains_original_descriptor_digest(self):
        data = (b"-----BEGIN PGP SIGNED MESSAGE-----\r\nHash: SHA256\r\n\r\n" +
                descriptor().replace(b"\n", b"\r\n") + b"\r\n-----BEGIN PGP SIGNATURE-----\r\nfixture\r\n")
        records = notices._ubuntu_descriptor(self.package, "source.dsc", data, "https://archive.ubuntu.com/source.dsc")
        self.assertEqual(records[0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(len(records), 3)


if __name__ == "__main__":
    unittest.main()
