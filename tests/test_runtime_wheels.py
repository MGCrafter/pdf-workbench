"""Runtime attribution gates reject invented providers and altered binaries."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_audit_tests", ROOT / "packaging/runtime_wheels.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
spec = importlib.util.spec_from_file_location("runtime_notices_tests", ROOT / "packaging/collect_notices.py")
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


def archive_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class RuntimeWheelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, **changes):
        return {"component": "Pillow", "version": "12.3.0", "filename": "pillow-12.3.0.tar.gz",
                "url": "https://example.invalid/pillow-12.3.0.tar.gz", "sha256": "a" * 64, "size": 100, **changes}

    def inventory(self, path, relative="PIL/_imaging.cp314-win_amd64.pyd", *, platform="win32"):
        return {"schema_version": 1, "platform": platform,
                "source_archives": [self.source()],
                "distributions": [{"name": "Pillow", "version": "12.3.0", "native_files": [
                    {"path": str(path.resolve()), "relative_path": relative,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]}]}

    def test_native_provider_requires_exact_recorded_file_digest_and_source(self):
        path = self.directory / "_imaging.cp314-win_amd64.pyd"
        path.write_bytes(b"audited native binary")
        inventory = self.inventory(path)
        self.assertEqual(runtime.native_provider(path, inventory)["distribution"], "Pillow")
        other = self.directory / "unrecorded.so"
        other.write_bytes(b"same directory is not a source provider")
        self.assertIsNone(runtime.native_provider(other, inventory))
        path.write_bytes(b"changed native binary")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "changed since audit"):
            runtime.native_provider(path, inventory)
        inventory = self.inventory(path)
        inventory["source_archives"] = []
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "source record"):
            runtime.native_provider(path, inventory)

    def test_no_directory_or_basename_exemption_for_private_or_unknown_native_libs(self):
        for relative in ("pillow.libs/libjpeg.so", "PIL/libsurprise.dll", "PIL/_unknown.so",
                         "cryptography/libinjected.so", "somewhere/_cffi_backend.so"):
            name = "cryptography" if relative.startswith("cryptography") else "Pillow"
            self.assertFalse(runtime.native_identity(name, relative))
        self.assertTrue(runtime.native_identity("cffi", "_cffi_backend.cpython-314-x86_64-linux-gnu.so"))
        self.assertTrue(runtime.native_identity("cryptography", "cryptography/hazmat/bindings/_rust.abi3.so"))

    def test_installed_native_record_hash_and_static_linux_ffi_are_not_trusted(self):
        path = self.directory / "_cffi_backend.so"
        data = b"native extension from audited RECORD"
        path.write_bytes(data)
        class Entry:
            size = len(data)
            hash = SimpleNamespace(mode="sha256", value=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("="))
            def __str__(self): return path.name
        dist = SimpleNamespace(locate_file=lambda _: path)
        self.assertEqual(runtime._verified_file(dist, Entry())[1], data)
        path.write_bytes(b"modified binary")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "RECORD digest/size"):
            runtime._verified_file(dist, Entry())
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(stdout="(NEEDED) Shared library: [libc.so.6]")):
            with self.assertRaisesRegex(runtime.RuntimeAuditError, "system libffi"):
                runtime.require_linux_cffi_system_library(path)
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(stdout="(NEEDED) Shared library: [libffi.so.8]")):
            runtime.require_linux_cffi_system_library(path)

    def test_rust_standard_source_matches_embedded_compiler_and_official_published_hash(self):
        channel = ('[pkg.rust-src]\ngit_commit_hash = "' + runtime.RUST_COMMIT + '"\n'
                   '[pkg.rust-src.target."*"]\nxz_url = "https://static.rust-lang.org/dist/rust-src-1.98.0.tar.xz"\n'
                   'xz_hash = "' + 'f' * 64 + '"\n').encode()
        lock = ('[[package]]\nname = "cfg-if"\nversion = "1.0"\n'
                'source = "registry+https://github.com/rust-lang/crates.io-index"\nchecksum = "' + 'e' * 64 + '"\n').encode()
        def fetch(url):
            if url.endswith(".toml"): return channel
            if url.endswith("Cargo.lock"): return lock
            return b"Original Rust copyright and permission text"
        native = ("/rustc/" + runtime.RUST_COMMIT + "/library/std/runtime.rs").encode()
        result = runtime.rust_standard_sources(native, self.directory, fetch)
        self.assertEqual(result[0]["sha256"], "f" * 64)
        self.assertEqual(result[0]["git_commit"], runtime.RUST_COMMIT)
        self.assertEqual(result[1]["component"], "Rust crate: cfg-if")
        self.assertEqual((self.directory / "runtime/rust-stdlib-1.98.0/COPYRIGHT").read_bytes(),
                         b"Original Rust copyright and permission text")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "compiler identity"):
            runtime.rust_standard_sources(b"unknown compiler", self.directory, fetch)

    def test_frozen_runtime_hashes_namespace_and_excluded_codecs_are_checked(self):
        bundle = self.directory / "bundle"
        path = bundle / "_internal/PIL/_imaging.cp314-win_amd64.pyd"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"matching runtime")
        inventory = self.inventory(path)
        self.assertEqual(len(runtime.verify_frozen_runtime(bundle, inventory)), 1)
        path.write_bytes(b"altered bundle")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "differs"):
            runtime.verify_frozen_runtime(bundle, inventory)
        path.unlink()
        avif = path.with_name("_avif.cp314-win_amd64.pyd")
        avif.write_bytes(b"excluded codec")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "Excluded AVIF"):
            runtime.verify_frozen_runtime(bundle, None)
        avif.unlink()
        font = path.with_name("_imagingft.cp314-win_amd64.pyd")
        font.write_bytes(b"new codec closure")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "font extension"):
            runtime.verify_frozen_runtime(bundle, self.inventory(font, "PIL/" + font.name))

    def test_frozen_unknown_private_library_is_not_assumed_covered(self):
        bundle = self.directory / "bundle"
        path = bundle / "_internal/pillow.libs/libunreviewed.so.2"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"new dependency")
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "new source audit"):
            runtime.verify_frozen_runtime(bundle, {})

    def test_cargo_sbom_sources_require_exact_registry_id_and_sha(self):
        component = {"name": "tiny", "version": "1.2", "purl": "pkg:cargo/tiny@1.2",
                     "hashes": [{"alg": "SHA-256", "content": "b" * 64}], "licenses": [{"expression": "MIT"}]}
        sbom = {"bomFormat": "CycloneDX", "components": [component]}
        item = runtime.cargo_sources(sbom)[0]
        self.assertEqual(item["url"], "https://static.crates.io/crates/tiny/tiny-1.2.crate")
        for changes in ({"purl": "pkg:github/unreviewed/project@1.2"}, {"hashes": []}, {"licenses": []}):
            with self.subTest(changes=changes), self.assertRaises(runtime.RuntimeAuditError):
                runtime.cargo_sources({"bomFormat": "CycloneDX", "components": [{**component, **changes}]})

    def test_crate_original_notices_are_retained_byte_for_byte_after_hash_check(self):
        original = b"Copyright Original Upstream\r\nMIT original permission text\r\n"
        data = archive_bytes({"tiny-1.2/LICENSE-MIT": original, "tiny-1.2/src/lib.rs": b"not a notice"})
        item = self.source(component="Rust crate: tiny", version="1.2", filename="tiny-1.2.crate",
                           sha256=hashlib.sha256(data).hexdigest())
        record, originals = runtime.crate_notices(item, lambda _: data)
        self.assertEqual(originals, [("tiny-1.2/LICENSE-MIT", original)])
        self.assertEqual(record["size"], len(data))
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "digest mismatch"):
            runtime.crate_notices(item, lambda _: b"substituted source")

    def test_crate_tar_paths_and_missing_original_notices_fail_closed(self):
        for files in ({"../escape/LICENSE": b"bad"}, {"tiny-1.2/src/lib.rs": b"no original license"}):
            data = archive_bytes(files)
            item = self.source(component="Rust crate: tiny", version="1.2", filename="tiny-1.2.crate",
                               sha256=hashlib.sha256(data).hexdigest())
            with self.subTest(files=files), self.assertRaises(runtime.RuntimeAuditError):
                runtime.crate_notices(item, lambda _: data)

    def test_cffi_static_windows_libffi_original_headers_are_required_and_retained(self):
        names = ("ffi.c", "ffi.h", "ffitarget.h", "prep_cif.c", "types.c", "win32.c", "win64.asm")
        files = {"cffi-2.1.1/src/c/libffi_x86_x64/" + name: b"Original libffi copyright " + name.encode() for name in names}
        data = archive_bytes(files)
        source = self.source(version="2.1.1", sha256=hashlib.sha256(data).hexdigest())
        result = dict(runtime.cffi_vendored_notices(source, lambda _: data))
        self.assertEqual(result["ffi.c"], files["cffi-2.1.1/src/c/libffi_x86_x64/ffi.c"])
        del files["cffi-2.1.1/src/c/libffi_x86_x64/ffi.h"]
        data = archive_bytes(files)
        source["sha256"] = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "incomplete"):
            runtime.cffi_vendored_notices(source, lambda _: data)

    def test_openssl_sbom_native_version_digest_and_canonical_upstream_are_bound(self):
        url = "https://github.com/openssl/openssl/releases/download/openssl-4.0.2/openssl-4.0.2.tar.gz"
        component = {"name": "openssl", "version": "4.0.2", "hashes": [{"alg": "SHA-256", "content": "c" * 64}],
                     "externalReferences": [{"type": "distribution", "url": url}]}
        self.assertEqual(runtime.openssl_source({"bomFormat": "CycloneDX", "components": [component]})["version"], "4.0.2")
        component["externalReferences"][0]["url"] = "https://example.invalid/replacement.tar.gz"
        with self.assertRaisesRegex(runtime.RuntimeAuditError, "Unknown native"):
            runtime.openssl_source({"bomFormat": "CycloneDX", "components": [component]})

    def test_both_platform_source_union_retains_differences_and_rejects_conflicts(self):
        linux = self.source()
        windows = self.source(component="Windows codec", version="2.0", filename="codec-2.tar.gz",
                              url="https://example.invalid/codec-2.tar.gz", sha256=None,
                              digest_source=runtime.CALCULATED_DIGEST)
        result = runtime.merge_source_records([linux], [linux, windows])
        self.assertEqual(len(result), 2)
        known = {**windows, "sha256": "e" * 64}
        self.assertEqual(runtime.merge_source_records([known], [windows])[0]["sha256"], "e" * 64)
        for conflicting in ({**linux, "sha256": "f" * 64}, {**linux, "version": "9.9"},
                            {**linux, "url": "https://example.invalid/changed-source"}):
            with self.subTest(conflicting=conflicting), self.assertRaises(runtime.RuntimeAuditError):
                runtime.merge_source_records([linux], [conflicting])

    def test_calculated_native_source_rejects_html_and_accepts_real_tar(self):
        item = self.source(sha256=None, size=None, digest_source=runtime.CALCULATED_DIGEST)
        with patch.object(notices, "_network_retry", return_value=(hashlib.sha256(b"<html>error</html>").hexdigest(), 18)):
            # Use the real transfer path below instead; this branch ensures a
            # missing temporary file cannot create a success manifest either.
            with self.assertRaises(OSError):
                notices.download_sources([dict(item)], self.directory / "missing")
        class Response(io.BytesIO):
            url = item["url"]
            headers = {}
        for data, success in ((b"<html>mirror error</html>", False),
                              (archive_bytes({"codec/source.c": b"real source"}), True)):
            output = self.directory / str(success)
            with patch.object(notices.urllib.request, "urlopen", return_value=Response(data)):
                if success:
                    notices.download_sources([dict(item)], output)
                    self.assertTrue((output / "source-manifest.json").is_file())
                else:
                    with self.assertRaisesRegex(notices.IncompleteNotices, "not a readable archive"):
                        notices.download_sources([dict(item)], output)
                    self.assertFalse((output / "source-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
