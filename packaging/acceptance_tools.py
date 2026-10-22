"""Prepare verified external tools for CI acceptance, outside the app bundle.

Only original downloads are cached; their pinned SHA-256 is checked on every
use before execution. Linux Ghostscript/Tesseract come from apt's signed archive;
Java is supplied by the hosted builder. No tool is shipped with PDF Workbench.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


# veraPDF signature verified against its published fingerprint:
# https://software.verapdf.org/ (13DD102B4DD69354D12DE5A83184863278B17FE7)
VERAPDF = (
    "https://software.verapdf.org/rel/1.30/verapdf-greenfield-1.30.3-installer.zip",
    "b512bf24945503a33fc10738ecffc1f2dccaaf8593a448e94ee9ac86a9559a59",
)
# Artifex's release-asset digest; WinGet's UB-Mannheim installer digest:
# https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/tag/gs10080
# https://github.com/microsoft/winget-pkgs/tree/master/manifests/u/UB-Mannheim/TesseractOCR/5.4.0.20240606
GHOSTSCRIPT = (
    "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs10080/gs10080w64.exe",
    "52a91b8bf09298788d7a57b9206127026c23eacd75405f0a131e26dc381dce50",
)
TESSERACT = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe",
    "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9",
)
TESSDATA_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
MODELS = {
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
    "deu": "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d",
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(cache, url, expected):
    """Reuse only intact bytes; never retain a partial or unverified download."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / url.rsplit("/", 1)[-1]
    if target.exists():
        if not target.is_file() or target.is_symlink() or digest(target) != expected:
            raise ValueError(f"Cached acceptance tool checksum mismatch: {target.name}")
        print(f"Verified cached {target.name}", flush=True)
        return target
    fd, name = tempfile.mkstemp(prefix=".download-", dir=cache)
    temporary = Path(name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "PDFWorkbench-native-acceptance"})
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=120) as response:
            shutil.copyfileobj(response, output)
        if digest(temporary) != expected:
            raise ValueError(f"Acceptance tool checksum mismatch: {target.name}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Downloaded and verified {target.name}", flush=True)
    return target


def run(command, **kwargs):
    return subprocess.run(list(map(str, command)), check=True, timeout=300, **kwargs)


def install_verapdf(cache, output, java):
    archive = download(cache, *VERAPDF)
    installer = output / "verapdf-installer.jar"
    with zipfile.ZipFile(archive) as zipped:
        names = [n for n in zipped.namelist() if n.endswith("/verapdf-izpack-installer-1.30.3.jar")]
        if len(names) != 1:
            raise ValueError("The pinned veraPDF archive does not contain one expected installer")
        with zipped.open(names[0]) as source, installer.open("wb") as target:
            shutil.copyfileobj(source, target)
    root = ET.Element("AutomatedInstallation", langpack="eng")
    prefix = "com.izforge.izpack.panels."
    ET.SubElement(root, prefix + "htmlhello.HTMLHelloPanel", id="welcome")
    panel = ET.SubElement(root, prefix + "target.TargetPanel", id="install_dir")
    ET.SubElement(panel, "installpath").text = str(output / "verapdf")
    panel = ET.SubElement(root, prefix + "packs.PacksPanel", id="sdk_pack_select")
    for index, name in enumerate(("veraPDF GUI", "veraPDF CLI", "veraPDF Documentation", "veraPDF Sample Plugins")):
        ET.SubElement(panel, "pack", index=str(index), name=name, selected=str(index == 1).lower())
    ET.SubElement(root, prefix + "install.InstallPanel", id="install")
    ET.SubElement(root, prefix + "finish.FinishPanel", id="finish")
    configuration = output / "verapdf-install.xml"
    ET.ElementTree(root).write(configuration, encoding="utf-8", xml_declaration=True)
    run([java, "-jar", installer, configuration])
    cli = output / "verapdf/bin/cli-1.30.3.jar"
    if not cli.is_file():
        raise ValueError("veraPDF CLI installation is incomplete")
    run([java, "-jar", cli, "--version"])
    return cli


def prepare(cache, output):
    output.mkdir(parents=True, exist_ok=True)
    java = shutil.which("java")
    if not java:
        raise ValueError("The hosted builder must provide Java for actual veraPDF validation")
    run([java, "-version"])
    if sys.platform == "win32":
        engine_root = output / "tesseract"
        gs_root = output / "ghostscript"
        run([download(cache, *TESSERACT), "/S", f"/D={engine_root}"])
        # Artifex's setup can wait for interactive UI even with /S. Its signed,
        # pinned NSIS archive is also directly extractable, as used by the VM
        # acceptance. Hosted Windows builders already contain 7-Zip.
        sevenzip = shutil.which("7z") or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip/7z.exe")
        if not Path(sevenzip).is_file():
            raise ValueError("The Windows builder must provide 7-Zip for private Ghostscript extraction")
        run([sevenzip, "x", "-y", f"-o{gs_root}", download(cache, *GHOSTSCRIPT)])
        engine = engine_root / "tesseract.exe"
        gs = gs_root / "bin/gswin64c.exe"
    else:
        engine = Path(shutil.which("tesseract") or "missing-tesseract")
        gs = Path(shutil.which("gs") or "missing-ghostscript")
    if not engine.is_file() or not gs.is_file():
        raise ValueError("Native acceptance requires both real Tesseract and Ghostscript")
    run([engine, "--version"])
    run([gs, "--version"])
    listing = run([engine, "--list-langs"], capture_output=True, text=True).stdout
    match = re.search(r'List of available languages in "([^"]+)"', listing)
    candidates = ([Path(match[1])] if match else []) + [engine.parent / "tessdata",
        Path("/usr/share/tesseract-ocr/4.00/tessdata"), Path("/usr/share/tesseract-ocr/5/tessdata")]
    installed = next((p for p in candidates if (p / "pdf.ttf").is_file() and (p / "osd.traineddata").is_file()), None)
    if installed is None:
        raise ValueError("The full Tesseract installation must include pdf.ttf and osd.traineddata")
    tessdata = output / "tessdata"
    tessdata.mkdir(exist_ok=True)
    for code, expected in MODELS.items():
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_COMMIT}/{code}.traineddata"
        shutil.copy2(download(cache, url, expected), tessdata / (code + ".traineddata"))
    for name in ("pdf.ttf", "osd.traineddata"):
        shutil.copy2(installed / name, tessdata / name)
    cli = install_verapdf(cache, output, java)
    environment = {
        "PDFWB_TEST_VERAPDF": str(cli), "PDFWB_TEST_GHOSTSCRIPT": str(gs),
        "PDF_WORKBENCH_OCR_TEST_TESSDATA": str(tessdata), "TESSDATA_PREFIX": str(tessdata),
        "PDF_WORKBENCH_REQUIRE_OCR": "1", "PDF_WORKBENCH_REQUIRE_PDFA": "1",
    }
    run([engine, "--list-langs"], env={**os.environ, **environment})
    return environment, engine.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    environment, engine_path = prepare(args.cache.resolve(), args.output.resolve())
    for key in ("GITHUB_ENV", "GITHUB_PATH"):
        if not os.environ.get(key):
            raise ValueError(f"{key} is required to configure subsequent acceptance steps")
    with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8") as stream:
        for key, value in environment.items():
            if "\n" in value or "\r" in value:
                raise ValueError("Unsafe acceptance environment value")
            stream.write(f"{key}={value}\n")
    with Path(os.environ["GITHUB_PATH"]).open("a", encoding="utf-8") as stream:
        stream.write(str(engine_path) + "\n")
    (args.output / "tool-environment.json").write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
