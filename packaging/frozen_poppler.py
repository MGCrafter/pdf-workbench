"""Render both native builders' actual frozen exports in the Linux source job."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def review(root, output, assets, commit, version):
    from pdf_workbench.pdfa import parse_verapdf_report
    reports = []
    for platform, plugin in (("linux", "xcb"), ("windows", "windows")):
        fixture_root = root / ("runtime-" + platform) / "frozen-smoke"
        cores = list(fixture_root.glob("*/v09-core"))
        if len(cores) != 1:
            raise ValueError(f"Expected exactly one actual {platform} frozen fixture set")
        core = cores[0]
        native = json.loads((core.parent / "result.json").read_text(encoding="utf-8"))
        if (native.get("result") != "PASS" or native.get("frozen") is not True
                or native.get("app_version") != version
                or native.get("provenance", {}).get("source_commit") != commit
                or native.get("gui", {}).get("qt_platform") != plugin
                or native.get("core", {}).get("skipped") != []
                or native.get("core", {}).get("tests", 0) < 34
                or not isinstance(native.get("executable_sha256"), str)
                or len(native["executable_sha256"]) != 64):
            raise ValueError(f"The {platform} fixtures lack matching successful native frozen provenance")
        xml = (core / "test_pdfa_requires_actual_validator_and_preserves_order_orientation/verapdf.xml").read_bytes()
        if not parse_verapdf_report(xml.decode("utf-8")).compliant:
            raise ValueError(f"The {platform} real veraPDF report is not compliant PDF/A-2b")
        destination = output / platform
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(Path(__file__).with_name("v09_poppler_review.py")),
                        "--core", str(core), "--output", str(destination)], check=True)
        report = json.loads((destination / "result.json").read_text(encoding="utf-8"))
        if report.get("result") != "PASS" or len(report.get("checks", [])) != 7 or not report.get("input_sha256"):
            raise ValueError(f"The {platform} independent renderer did not complete all seven checks")
        report.update(platform=platform, app_version=version, source_commit=commit,
                      frozen_executable_sha256=native["executable_sha256"], native_qt_platform=plugin)
        reports.append((platform, report, xml))
    # Publish only the small flat reports, after both platforms pass. PDFs,
    # screenshots, test certificates and renderer output stay in build artifacts.
    assets.mkdir(parents=True, exist_ok=True)
    for platform, report, xml in reports:
        path = assets / f"PDFWorkbench-v{version}-{platform}-poppler.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        with (assets / f"PDFWorkbench-v{version}-{platform}-pdfa.xml").open("xb") as stream:
            stream.write(xml)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assets", required=True, type=Path)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    with Path("pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    review(args.root, args.output, args.assets, commit, version)


if __name__ == "__main__":
    main()
