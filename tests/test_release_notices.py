"""Release gates reject missing texts, altered inputs and unsafe output paths."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_notices", ROOT / "packaging/collect_notices.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


class ReleaseNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_reviewed_locked_corpus_contains_complete_pdfium_foxit_texts(self):
        output = self.directory / "notices"
        lock = notices.copy_locked_notices(output)
        self.assertEqual(lock["qt_version"], "6.11.2")
        self.assertGreaterEqual(len(lock["files"]), 100)
        self.assertIn(b"THIS SOFTWARE IS PROVIDED", (output / "upstream/qt-6.11.2/pdfium-LICENSE.txt").read_bytes())
        self.assertIn(b"Foxit Software", (output / "upstream/qt-6.11.2/pdfium-foxit-copyright.txt").read_bytes())

    def test_missing_critical_attribution_cannot_pass_completeness(self):
        lock = json.loads(notices.LOCK.read_text())
        lock["files"] = [item for item in lock["files"] if not item["path"].endswith("qtpdf-3rdparty-icu.txt")]
        path = self.directory / "lock.json"
        path.write_text(json.dumps(lock))
        with patch.object(notices, "LOCK", path), self.assertRaisesRegex(notices.IncompleteNotices, "Critical"):
            notices.copy_locked_notices(self.directory / "out")

    def test_altered_upstream_text_fails_hash_gate(self):
        lock = json.loads(notices.LOCK.read_text())
        lock["files"][0]["sha256"] = "0" * 64
        path = self.directory / "lock.json"
        path.write_text(json.dumps(lock))
        with patch.object(notices, "LOCK", path), self.assertRaisesRegex(notices.IncompleteNotices, "Missing/modified"):
            notices.copy_locked_notices(self.directory / "out")

    def test_manifest_path_traversal_and_missing_texts_fail(self):
        output = self.directory / "notices"
        output.mkdir()
        manifest = {"schema_version": 1, "complete": True, "native_source_audit_complete": True, "files": {"../escape": "0"*64}}
        (output / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(notices.IncompleteNotices, "Unsafe"):
            notices.verify_bundle(self.directory / "bundle", output)
        manifest["files"] = {"missing.txt": "0"*64}
        (output / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(notices.IncompleteNotices, "mismatch"):
            notices.verify_bundle(self.directory / "bundle", output)

    def test_unknown_native_qt_module_requires_new_audit(self):
        output, bundle = self.directory / "notices", self.directory / "bundle"
        output.mkdir(); bundle.mkdir()
        (output / "manifest.json").write_text(json.dumps({"schema_version": 1, "complete": True, "native_source_audit_complete": True, "files": {}}))
        for module in ("Core", "Gui", "Widgets", "Pdf", "EglFsKmsGbmSupport", "EglFsKmsSupport", "WlShellIntegration"):
            (bundle / f"Qt6{module}.dll").write_bytes(b"fixture")
        result = notices.verify_bundle(bundle, output)
        self.assertTrue(result["verified"])
        self.assertTrue({"EglFsKmsGbmSupport", "EglFsKmsSupport", "WlShellIntegration"} <= set(result['qt_modules']))
        (bundle / "Qt6Multimedia.dll").write_bytes(b"unaudited")
        with self.assertRaisesRegex(notices.IncompleteNotices, "new attribution"):
            notices.verify_bundle(bundle, output)

    def test_bad_source_hash_removes_stale_success_and_rejects_download(self):
        output = self.directory / "sources"
        output.mkdir()
        (output / "source-manifest.json").write_text("stale success")
        response = io.BytesIO(b"incorrect upstream bytes")
        response.url = "https://example.invalid/source.tar.xz"
        item = {"filename": "source.tar.xz", "url": response.url, "sha256": "0"*64, "size": 24}
        with patch.object(notices.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(notices.IncompleteNotices, "digest mismatch"):
                notices.download_sources([item], output)
        self.assertFalse((output / "source-manifest.json").exists())
        self.assertFalse((output / "source.tar.xz").exists())
        self.assertFalse((output / "source.tar.xz.partial").exists())

    def test_source_success_records_final_hash_without_embedding_archives_in_notices(self):
        data = b"small archive fixture"
        response = io.BytesIO(data)
        response.url = "https://example.invalid/source.tar.xz"
        item = {"filename": "source.tar.xz", "url": response.url, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        output = self.directory / "sources"
        with patch.object(notices.urllib.request, "urlopen", return_value=response):
            notices.download_sources([item], output)
        manifest = json.loads((output / "source-manifest.json").read_text())
        self.assertEqual(manifest[0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((output / "source.tar.xz").read_bytes(), data)

    def native_api(self, descriptor=None, include_original=True):
        base = 'https://launchpad.net/ubuntu/source/'
        urls = [base + 'demo_1.2-0ubuntu1.dsc', base + 'demo_1.2-0ubuntu1.debian.tar.xz']
        if include_original:
            urls.append(base + 'demo_1.2.orig.tar.xz')
        dsc = descriptor or ('-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n'
            'Format: 3.0 (quilt)\nSource: demo\nVersion: 1.2-0ubuntu1\n'
            'Checksums-Sha256:\n ' + 'a'*64 + ' 123 demo_1.2.orig.tar.xz\n ' + 'b'*64 +
            ' 45 demo_1.2-0ubuntu1.debian.tar.xz\n\n-----BEGIN PGP SIGNATURE-----\nfixture\n')
        def response(url, **kwargs):
            if 'getPublishedSources' in url:
                return json.dumps({'entries': [{'source_package_name': 'demo', 'source_package_version': '1.2-0ubuntu1',
                    'self_link': 'https://api.launchpad.net/source-publication/42'}]}).encode()
            if 'sourceFileUrls' in url:
                return json.dumps(urls).encode()
            if url.endswith('.dsc'):
                return dsc.encode()
            raise AssertionError('Unexpected upstream request: ' + url)
        return response

    def test_native_source_descriptor_binds_complete_exact_version_source_set(self):
        inventory = {'schema_version': 1, 'packages': [{'source_package': 'demo', 'source_version': '1.2-0ubuntu1'}]}
        with patch.object(notices, '_ubuntu_pool_sources', return_value=None), patch.object(notices, 'fetch', side_effect=self.native_api()):
            result = notices.ubuntu_source_archives(inventory)
        self.assertEqual(len(result), 3)
        orig = next(item for item in result if item['filename'].endswith('.orig.tar.xz'))
        self.assertEqual((orig['sha256'], orig['size']), ('a'*64, 123))
        self.assertEqual({item['version'] for item in result}, {'1.2-0ubuntu1'})

    def test_native_source_descriptor_rejects_wrong_version_and_missing_archive(self):
        inventory = {'schema_version': 1, 'packages': [{'source_package': 'demo', 'source_version': '1.2-0ubuntu1'}]}
        for response in (self.native_api('Source: demo\nVersion: 1.3-newer\n'), self.native_api(include_original=False)):
            with patch.object(notices, '_ubuntu_pool_sources', return_value=None), patch.object(notices, 'fetch', side_effect=response), self.assertRaises(notices.IncompleteNotices):
                notices.ubuntu_source_archives(inventory)

    def test_parallel_source_metadata_remains_complete_and_rejects_one_bad_descriptor(self):
        packages = [{'source_package': name, 'source_version': '1.0'} for name in ('alpha', 'beta')]
        for wrong in (False, True):
            barrier = threading.Barrier(2)
            def response(url, **kwargs):
                parsed = notices.urllib.parse.urlparse(url)
                query = notices.urllib.parse.parse_qs(parsed.query)
                if 'getPublishedSources' in url:
                    name = query['source_name'][0]
                    barrier.wait(timeout=5)
                    return json.dumps({'entries': [{'source_package_name': name, 'source_package_version': '1.0',
                                                    'self_link': 'https://api.launchpad.net/' + name}]}).encode()
                if 'sourceFileUrls' in url:
                    name = parsed.path.rsplit('/', 1)[-1]
                    return json.dumps([f'https://launchpad.net/{name}_1.0.dsc', f'https://launchpad.net/{name}_1.0.orig.tar.xz']).encode()
                name = parsed.path.rsplit('/', 1)[-1].split('_', 1)[0]
                version = '2.0' if wrong and name == 'beta' else '1.0'
                return (f'Source: {name}\nVersion: {version}\nChecksums-Sha256:\n ' + 'a'*64 + f' 123 {name}_1.0.orig.tar.xz\n').encode()
            with patch.object(notices, '_ubuntu_pool_sources', return_value=None), patch.object(notices, 'fetch', side_effect=response):
                if wrong:
                    with self.assertRaises(notices.IncompleteNotices):
                        notices.ubuntu_source_archives({'schema_version': 1, 'packages': packages})
                else:
                    result = notices.ubuntu_source_archives({'schema_version': 1, 'packages': packages})
                    self.assertEqual(len(result), 4)
                    self.assertEqual({item['component'] for item in result}, {'Ubuntu source: alpha', 'Ubuntu source: beta'})

    def test_native_toc_uses_actual_elf_inputs_and_exact_dpkg_source_version(self):
        library = self.directory / 'libdemo.so.1'
        library.write_bytes(b'\x7fELF actual collected input')
        data = self.directory / 'not-a-native-file'
        data.write_bytes(b'plain text data')
        toc = self.directory / 'Analysis-00.toc'
        toc.write_text(repr((['ignored'], [('libdemo.so.1', str(library), 'BINARY'), ('data', str(data), 'BINARY')])))
        def query(command, **kwargs):
            if '-S' in command:
                return SimpleNamespace(returncode=0, stdout='libdemo1:amd64: '+str(library)+'\n')
            return SimpleNamespace(returncode=0, stdout='demo\t1:1.2-0ubuntu1')
        with patch.object(notices.sys, 'platform', 'linux'), patch.object(notices.shutil, 'which', return_value='/usr/bin/dpkg-query'), patch.object(notices.subprocess, 'run', side_effect=query), patch.object(notices.platform, 'platform', return_value='Linux-release-test-x86_64'):
            result = notices.native_source_inventory(toc)
        self.assertEqual(len(result['packages']), 1)
        self.assertEqual(result['builder_platform'], 'Linux-release-test-x86_64')
        self.assertEqual(result['packages'][0]['source_version'], '1:1.2-0ubuntu1')
        self.assertEqual(result['packages'][0]['binary_paths'], [str(library)])

    def test_unknown_native_provider_does_not_get_assumed_system_source_coverage(self):
        library = self.directory / 'libunknown.so.1'
        library.write_bytes(b'\x7fELF')
        toc = self.directory / 'Analysis-00.toc'
        toc.write_text(repr([('libunknown.so.1', str(library), 'BINARY')]))
        with patch.object(notices.sys, 'platform', 'linux'), patch.object(notices.shutil, 'which', return_value='/usr/bin/dpkg-query'), patch.object(notices.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')):
            with self.assertRaisesRegex(notices.IncompleteNotices, 'No audited source provider'):
                notices.native_source_inventory(toc)

    def test_unfinished_native_audit_blocks_linux_binary_verification(self):
        output = self.directory / 'notices'
        output.mkdir()
        (output / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'complete': True, 'files': {}}))
        with patch.object(notices.sys, 'platform', 'linux'), self.assertRaisesRegex(notices.IncompleteNotices, 'has not completed'):
            notices.verify_bundle(self.directory / 'bundle', output)

    def test_installed_python_runtime_license_preserves_distributor_terms_byte_for_byte(self):
        prefix, stdlib, output = self.directory / 'python-runtime', self.directory / 'python-runtime/Lib', self.directory / 'notices'
        stdlib.mkdir(parents=True)
        license_bytes = b'Python runtime license\r\nMicrosoft Distributable Code terms\r\n'
        (prefix / 'LICENSE.txt').write_bytes(license_bytes)
        (stdlib / 'NOTICE.vendor').write_bytes(b'Additional runtime distributor notice')
        with patch.object(notices.sys, 'base_prefix', str(prefix)), patch.object(notices.sys, 'prefix', str(prefix)), patch.object(notices.sysconfig, 'get_path', return_value=str(stdlib)):
            records = notices.collect_python_runtime_notices(output)
        self.assertEqual(len(records), 2)
        actual = output / 'python/installed-runtime/base-prefix-LICENSE.txt'
        self.assertEqual(actual.read_bytes(), license_bytes)
        self.assertEqual(next(r['sha256'] for r in records if r['file'].endswith('LICENSE.txt')), hashlib.sha256(license_bytes).hexdigest())


if __name__ == "__main__":
    unittest.main()
