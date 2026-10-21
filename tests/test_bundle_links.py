"""Native bundles normalize trusted library aliases without weakening updates."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
from build import materialize_bundle_links
from pdf_workbench.update_installer import write_bundle_manifest, validate_bundle


class BundleLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.bundle = self.root / 'bundle'; self.bundle.mkdir()

    def tearDown(self): self.temp.cleanup()

    def link(self, path, target):
        try: path.symlink_to(target)
        except OSError: self.skipTest('Creating symlinks is unavailable on this platform')

    def test_internal_library_alias_chain_is_materialized_and_manifest_covers_every_copy(self):
        directory = self.bundle / '_internal'; directory.mkdir()
        native = directory / 'actual.so'; native.write_bytes(b'Native library fixture'); native.chmod(0o755)
        alias = directory / 'alias.so'; self.link(alias, native.name)
        second = self.bundle / 'root-alias.so'; self.link(second, Path('_internal') / 'alias.so')
        materialize_bundle_links(self.bundle)
        for path in (alias, second):
            self.assertFalse(path.is_symlink()); self.assertEqual(path.read_bytes(), native.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, native.stat().st_mode & 0o777)
        manifest = write_bundle_manifest(self.bundle, app_version='0.8.0')
        self.assertEqual(len(manifest['files']), 3)
        validate_bundle(self.bundle, expected_version='0.8.0')

    def test_external_alias_is_rejected_before_any_copy_and_preserves_external_bytes(self):
        native = self.bundle / 'native.so'; native.write_bytes(b'Internal')
        good = self.bundle / 'a-good.so'; self.link(good, native.name)
        external = self.root / 'external.so'; external.write_bytes(b'Foreign document')
        bad = self.bundle / 'z-bad.so'; self.link(bad, external)
        with self.assertRaises(ValueError): materialize_bundle_links(self.bundle)
        self.assertTrue(good.is_symlink()); self.assertTrue(bad.is_symlink())
        self.assertEqual(external.read_bytes(), b'Foreign document')
        self.assertEqual(list(self.bundle.glob('.bundle-alias-*')), [])

    def test_directory_and_broken_aliases_remain_errors(self):
        directory = self.bundle / 'actual'; directory.mkdir()
        alias = self.bundle / 'directory-alias'; self.link(alias, 'actual')
        with self.assertRaises(ValueError): materialize_bundle_links(self.bundle)
        self.assertTrue(alias.is_symlink()); alias.unlink()
        self.link(alias, 'missing.so')
        with self.assertRaises(FileNotFoundError): materialize_bundle_links(self.bundle)
        self.assertTrue(alias.is_symlink())

    def test_cycles_are_rejected_before_mutating_valid_aliases(self):
        native=self.bundle/'native.so';native.write_bytes(b'Internal original')
        good=self.bundle/'a-good.so';self.link(good,native.name)
        first=self.bundle/'cycle-a.so';second=self.bundle/'cycle-b.so'
        self.link(first,second.name);self.link(second,first.name)
        for self_cycle in (False,True):
            with self.subTest(self_cycle=self_cycle):
                if self_cycle:
                    first.unlink();second.unlink();self.link(first,first.name)
                with self.assertRaises((OSError,RuntimeError)):materialize_bundle_links(self.bundle)
                self.assertTrue(good.is_symlink());self.assertTrue(first.is_symlink())
                self.assertEqual(native.read_bytes(),b'Internal original')
                self.assertEqual(list(self.bundle.glob('.bundle-alias-*')),[])

    def test_symlink_bundle_root_does_not_materialize_a_foreign_directory(self):
        foreign=self.root/'foreign';foreign.mkdir()
        native=foreign/'original.so';native.write_bytes(b'Foreign original')
        alias=foreign/'alias.so';self.link(alias,native.name)
        linked_root=self.root/'linked-bundle';self.link(linked_root,foreign)
        with self.assertRaises(ValueError):materialize_bundle_links(linked_root)
        self.assertTrue(alias.is_symlink());self.assertEqual(native.read_bytes(),b'Foreign original')
        self.assertEqual(list(foreign.glob('.bundle-alias-*')),[])


if __name__ == '__main__': unittest.main()
