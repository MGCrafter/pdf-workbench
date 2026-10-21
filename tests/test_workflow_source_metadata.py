"""Execute the upload gates with old cached manifests and corrupt source data."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowSourceMetadataTests(unittest.TestCase):
    def gate(self, workflow, *, size=None, corrupt=False):
        source = (ROOT / '.github/workflows' / workflow).read_text()
        section = source.split('      - name: Write checksums\n', 1)[1]
        section = section.split('\n      - name:', 1)[0]
        script = section.split("python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'release-assets'
            root.mkdir()
            for suffix in ('windows-x86_64.zip', 'linux-x86_64.tar.gz', 'source.tar.gz'):
                (root / ('PDFWorkbench-v0.6.0-' + suffix)).write_bytes(b'app fixture')
            data = b'exact upstream source archive fixture'
            (root / 'source+1.tar.gz').write_bytes(data + (b'changed' if corrupt else b''))
            records = [{'filename': 'source+1.tar.gz', 'size': size,
                        'sha256': hashlib.sha256(data).hexdigest()}]
            manifest = root / 'source-manifest.json'
            manifest.write_text(json.dumps(records))
            result = subprocess.run([sys.executable, '-c', textwrap.dedent(script)], cwd=directory,
                                    env=dict(os.environ, RELEASE_TAG='v0.6.0', GITHUB_REF_NAME='v0.6.0'),
                                    capture_output=True, text=True, timeout=10)
            return result, json.loads(manifest.read_text()), len(data)

    def test_unknown_cached_size_is_filled_only_after_the_original_hash_matches(self):
        for workflow in ('release.yml', 'publish.yml'):
            with self.subTest(workflow=workflow):
                result, records, size = self.gate(workflow)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(records[0]['size'], size)
                self.assertEqual(records[0]['filename'], 'source.1.tar.gz')
                self.assertEqual(records[0]['upstream_filename'], 'source+1.tar.gz')

    def test_unknown_size_does_not_allow_corrupt_source_bytes(self):
        for workflow in ('release.yml', 'publish.yml'):
            with self.subTest(workflow=workflow):
                result, records, _ = self.gate(workflow, corrupt=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Source asset checksum mismatch', result.stderr)
                self.assertIsNone(records[0]['size'])

    def test_declared_size_mismatches_are_rejected_including_zero_and_boolean(self):
        for workflow in ('release.yml', 'publish.yml'):
            for size in (0, 9999, True):
                with self.subTest(workflow=workflow, size=size):
                    result, _, _ = self.gate(workflow, size=size)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Missing or incomplete corresponding source asset', result.stderr)


if __name__ == '__main__':
    unittest.main()
