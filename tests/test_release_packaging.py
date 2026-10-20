"""A frozen test result must belong to the exact executable being released."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load_script("release_packaging_build", PROJECT / "packaging/build.py")
original_path = sys.path[:]
try:
    with patch.dict(sys.modules, {"build": build}):
        release = load_script("release_packaging_release", PROJECT / "packaging/release.py")
finally:
    sys.path[:] = original_path


class ReleaseAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "PDFWorkbench"
        (self.bundle / "_internal").mkdir(parents=True)
        self.results = self.root / "acceptance"
        self.report_path = self.results / "run-current/result.json"
        self.report_path.parent.mkdir(parents=True)
        self.version = "0.3.0"
        self.commit = "a" * 40
        self.source_hashes = {"src/pdf_workbench/__init__.py": hashlib.sha256(b"reviewed source").hexdigest()}
        self.executable_bytes = b"tested native executable fixture"
        self.provenance = {
            "schema_version": 1,
            "app_version": self.version,
            "source_commit": self.commit,
            "source_sha256": self.source_hashes,
        }

    def prepare(self, target="linux", qt_platform="xcb"):
        executable = self.bundle / ("PDFWorkbench.exe" if target == "win32" else "PDFWorkbench")
        executable.write_bytes(self.executable_bytes)
        self.provenance_path = self.bundle / "_internal/APP_PROVENANCE.json"
        self.provenance_path.write_text(json.dumps(self.provenance), encoding="utf-8")
        report = {
            "result": "PASS",
            "frozen": True,
            "app_version": self.version,
            "provenance": copy.deepcopy(self.provenance),
            "executable_sha256": hashlib.sha256(self.executable_bytes).hexdigest(),
            "core": {"tests": 13, "failures": 0, "errors": 0, "skipped": []},
            "gui": {"result": "PASS", "qt_platform": qt_platform, "app_version": self.version, "checks": ["native window", "PDF export/reopen"]},
        }
        self.write_report(report)
        return report, executable

    def write_report(self, report):
        self.report_path.write_text(json.dumps(report), encoding="utf-8")

    def verify(self, target="linux", **identity):
        with patch.object(release.sys, "platform", target):
            return release.verify_acceptance(
                self.bundle, self.results,
                identity.get("version", self.version),
                identity.get("commit", self.commit),
                identity.get("source_hashes", self.source_hashes),
            )

    def test_matching_frozen_results_are_accepted_on_both_native_platforms(self):
        for target, qt_platform in (("linux", "xcb"), ("linux", "wayland"), ("win32", "windows")):
            with self.subTest(target=target, qt_platform=qt_platform):
                report, _ = self.prepare(target, qt_platform)
                self.assertEqual(self.verify(target), report)

    def test_replacing_tested_executable_invalidates_its_previous_pass(self):
        _, executable = self.prepare()
        executable.write_bytes(b"different executable with the same filename")
        with self.assertRaises(ValueError):
            self.verify()

    def test_dark_mode_release_requires_all_native_appearance_checks(self):
        self.version = self.provenance['app_version'] = '0.4.0'
        required = ['appearance dark/light/system', 'appearance persisted',
                    'appearance preserves document and viewport', 'appearance document/export colors',
                    'appearance dialogs and icons']
        for target, platform_name in (('linux', 'xcb'), ('win32', 'windows')):
            report, _ = self.prepare(target, platform_name)
            with self.subTest(target=target, absent='all'), self.assertRaises(ValueError):
                self.verify(target)
            for missing in required:
                report['gui']['checks'] = [name for name in required if name != missing]
                self.write_report(report)
                with self.subTest(target=target, absent=missing), self.assertRaises(ValueError):
                    self.verify(target)
            report['gui']['checks'] = required
            self.write_report(report)
            self.assertEqual(self.verify(target), report)

    def test_previous_build_cannot_be_released_as_a_different_checkout(self):
        self.prepare()
        identities = {
            "different source": {"source_hashes": {"src/pdf_workbench/__init__.py": "b" * 64}},
            "different commit": {"commit": "b" * 40},
            "different version": {"version": "0.4.0"},
        }
        for label, identity in identities.items():
            with self.subTest(identity=label), self.assertRaises(ValueError):
                self.verify(**identity)

    def test_unrelated_report_or_altered_embedded_provenance_cannot_be_substituted(self):
        for field, value in (("source_sha256", {"src/other.py": "c" * 64}),
                             ("source_commit", "c" * 40), ("app_version", "0.2.0")):
            with self.subTest(location="report", field=field):
                report, _ = self.prepare()
                report["provenance"][field] = value
                self.write_report(report)
                with self.assertRaises(ValueError):
                    self.verify()
            with self.subTest(location="bundle", field=field):
                self.prepare()
                provenance = copy.deepcopy(self.provenance)
                provenance[field] = value
                self.provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.verify()

    def test_missing_or_ambiguous_reports_cannot_select_a_convenient_pass(self):
        report, _ = self.prepare()
        self.report_path.unlink()
        with self.subTest(reports=0), self.assertRaises(ValueError):
            self.verify()
        self.write_report(report)
        old_report = self.results / "old-run/result.json"
        old_report.parent.mkdir()
        old_report.write_text(json.dumps(report), encoding="utf-8")
        with self.subTest(reports=2), self.assertRaises(ValueError):
            self.verify()

    def test_skipped_failed_unfrozen_and_non_native_runs_cannot_authorize_release(self):
        cases = (
            ("source runtime only", ("frozen",), False),
            ("overall failure", ("result",), "FAIL"),
            ("stale report version", ("app_version",), "0.2.0"),
            ("incomplete PDF cases", ("core", "tests"), 12),
            ("PDF test failure", ("core", "failures"), 1),
            ("PDF test error", ("core", "errors"), 1),
            ("skipped PDF case", ("core", "skipped"), ["roundtrip test unavailable"]),
            ("GUI failure", ("gui", "result"), "FAIL"),
            ("headless Qt", ("gui", "qt_platform"), "offscreen"),
            ("another operating system", ("gui", "qt_platform"), "windows"),
            ("stale GUI version", ("gui", "app_version"), "0.2.0"),
        )
        for label, keys, value in cases:
            with self.subTest(run=label):
                report, _ = self.prepare()
                section = report
                for key in keys[:-1]:
                    section = section[key]
                section[keys[-1]] = value
                self.write_report(report)
                with self.assertRaises(ValueError):
                    self.verify()
        self.prepare("win32", "xcb")
        with self.subTest(run="Linux Qt cannot approve Windows"), self.assertRaises(ValueError):
            self.verify("win32")


class BuildProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "src/pdf_workbench/__init__.py"
        self.source.parent.mkdir(parents=True)
        self.source.write_text('__version__ = "0.3.0"\n', encoding="utf-8")
        self.project_file = self.root / "pyproject.toml"
        self.project_file.write_text('[project]\nversion = "0.3.0"\n', encoding="utf-8")
        self.commit = "d" * 40
        app_module = types.ModuleType("pdf_workbench")
        app_module.__version__ = "0.3.0"
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(patch.object(build, "ROOT", self.root))
        contexts.enter_context(patch.dict(sys.modules, {"pdf_workbench": app_module}))
        contexts.enter_context(patch.object(build.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, self.commit + "\n", "")))
        self.git_status = contexts.enter_context(patch.object(build.subprocess, "check_output", return_value=""))
        self.original_path = sys.path[:]
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), self.original_path))

    def test_clean_committed_build_records_the_actual_source_snapshot(self):
        provenance = build.build_provenance(release=True)
        self.assertEqual(provenance["app_version"], "0.3.0")
        self.assertEqual(provenance["source_commit"], self.commit)
        self.assertEqual(provenance["source_sha256"], {
            "src/pdf_workbench/__init__.py": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        })

    def test_conflicting_package_and_app_versions_cannot_build_a_release(self):
        self.project_file.write_text('[project]\nversion = "0.4.0"\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            build.build_provenance(release=True)

    def test_uncommitted_source_or_packaging_changes_cannot_build_a_release(self):
        for status in (" M src/pdf_workbench/app.py\n", "?? src/pdf_workbench/new.py\n",
                       " M packaging/release.py\n", " M pyproject.toml\n"):
            with self.subTest(status=status.strip()):
                self.git_status.return_value = status
                with self.assertRaises(ValueError):
                    build.build_provenance(release=True)

    def test_release_cannot_claim_a_commit_outside_a_git_checkout(self):
        with patch.object(build.subprocess, "run", return_value=subprocess.CompletedProcess([], 128, "", "no git checkout")):
            with self.assertRaises(ValueError):
                build.build_provenance(release=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
