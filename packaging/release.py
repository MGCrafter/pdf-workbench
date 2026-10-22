"""Package a tested native bundle into an archive with source/build provenance."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import verify_notices
from collect_notices import verify_bundle
from release_checks import V06_CORE_CHECKS, V06_GUI_CHECKS, V07_CORE_CHECKS, V07_GUI_CHECKS, V08_CORE_CHECKS, V08_GUI_CHECKS, V09_CORE_CHECKS, V09_GUI_CHECKS, required_core_checks, required_gui_checks, minimum_core_tests


def verify_acceptance(bundle: Path, results: Path, version: str, commit: str, source_hashes: dict) -> dict:
    reports = list(results.rglob('result.json'))
    if len(reports) != 1:
        raise ValueError('Expected exactly one frozen acceptance report.')
    report = json.loads(reports[0].read_text(encoding='utf-8'))
    executable = bundle / ('PDFWorkbench.exe' if sys.platform == 'win32' else 'PDFWorkbench')
    provenance = json.loads((bundle / '_internal/APP_PROVENANCE.json').read_text(encoding='utf-8'))
    if (report.get('result') != 'PASS' or report.get('frozen') is not True
            or report.get('app_version') != version or provenance.get('app_version') != version
            or provenance.get('source_commit') != commit or provenance.get('source_sha256') != source_hashes
            or report.get('provenance') != provenance
            or report.get('executable_sha256') != hashlib.sha256(executable.read_bytes()).hexdigest()):
        raise ValueError('Frozen acceptance does not match this executable and source commit.')
    core, gui = report.get('core', {}), report.get('gui', {})
    native_platforms = {'windows'} if sys.platform == 'win32' else {'xcb', 'wayland'}
    if (core.get('tests', 0) < 13 or core.get('failures') != 0 or core.get('errors') != 0
            or core.get('skipped') != [] or gui.get('result') != 'PASS'
            or gui.get('qt_platform') not in native_platforms or gui.get('app_version') != version):
        raise ValueError('A successful native frozen PDF/UI acceptance is required.')
    if tuple(int(part) for part in version.split('.')[:2]) >= (0, 4):
        required = {'appearance dark/light/system', 'appearance persisted',
                    'appearance preserves document and viewport', 'appearance document/export colors',
                    'appearance dialogs and icons'}
        if not required <= set(gui.get('checks', [])):
            raise ValueError('A complete native theme acceptance is required for this version.')
    if tuple(int(part) for part in version.split('.')[:2]) >= (0, 5):
        required = {'objects select/move/duplicate/delete/undo', 'objects text/image/stamp/symbol PDF roundtrip',
                    'objects bundled font and Unicode', 'asynchronous text search', 'images to PDF and undo',
                    'local OCR searchable text and preserved image'}
        if not required <= set(gui.get('checks', [])):
            raise ValueError('A complete native object/search/image/OCR acceptance is required for this version.')
    if tuple(int(part) for part in version.split('.')[:2]) >= (0, 6):
        if core.get('tests', 0) < 15 or not V06_CORE_CHECKS <= set(core.get('checks', [])):
            raise ValueError('Complete native password/compression/form PDF acceptance is required.')
        if not V06_GUI_CHECKS <= set(gui.get('checks', [])):
            raise ValueError('Complete native password/compression/form/motion GUI acceptance is required.')
    if tuple(int(part) for part in version.split('.')[:2]) >= (0, 7):
        if core.get('tests', 0) < 18 or not V07_CORE_CHECKS <= set(core.get('checks', [])):
            raise ValueError('Complete native comparison/form-design/page-marks PDF acceptance is required.')
        if not V07_GUI_CHECKS <= set(gui.get('checks', [])):
            raise ValueError('Complete native comparison/form-design/page-marks GUI acceptance is required.')
    if tuple(int(part) for part in version.split('.')[:2]) >= (0, 8):
        if core.get('tests', 0) < 23 or not V08_CORE_CHECKS <= set(core.get('checks', [])):
            raise ValueError('Complete native bookmarks/geometry/project/recovery/update PDF acceptance is required.')
        if not V08_GUI_CHECKS <= set(gui.get('checks', [])):
            raise ValueError('Complete native bookmarks/geometry/project/autosave/updater GUI acceptance is required.')
    if tuple(int(part) for part in version.split('.')[:2]) >= (0, 9):
        if core.get('tests', 0) < minimum_core_tests(version) or not required_core_checks(version) <= set(core.get('checks', [])):
            raise ValueError('Complete native v0.9 workflow/content/signature/PDF-A acceptance is required.')
        if not required_gui_checks(version) <= set(gui.get('checks', [])):
            raise ValueError('Complete native v0.9 tabs/workflow/content/signature/validated-PDF-A GUI acceptance is required.')
    return report


def package(bundle: Path, notices: Path, output: Path, results: Path) -> Path:
    with (ROOT / 'pyproject.toml').open('rb') as stream:
        version = tomllib.load(stream)['project']['version']
    if sys.platform not in ('win32', 'linux'):
        raise ValueError('Only the tested Windows and Linux release formats are supported.')
    target = 'windows' if sys.platform == 'win32' else 'linux'
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('This release is for x86_64; use a matching native builder.')
    executable = bundle / ('PDFWorkbench.exe' if target == 'windows' else 'PDFWorkbench')
    if not executable.is_file():
        raise ValueError('Expected native executable is missing.')
    manifest = verify_notices(notices)
    verify_bundle(bundle, notices)
    embedded = bundle / '_internal/licenses'
    if (embedded / 'manifest.json').read_bytes() != (notices / 'manifest.json').read_bytes():
        raise ValueError('Bundle notices differ from verified build notices.')
    for relative, digest in manifest['files'].items():
        if hashlib.sha256((embedded / relative).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Bundled notice checksum mismatch: {relative}')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    source_hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((ROOT / 'src').rglob('*'))
                     if p.is_file() and p.suffix in ('.py', '.ttf', '.txt')}
    report = verify_acceptance(bundle, results, version, commit, source_hashes)
    metadata = {'app_version': version, 'source_commit': commit,
                'source_repository': 'https://github.com/MGCrafter/pdf-workbench',
                'platform': target, 'architecture': 'x86_64',
                'python': platform.python_version(), 'libc': platform.libc_ver(),
                'packages': {name: importlib.metadata.version(name) for name in
                             ('PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'pypdf', 'PyInstaller', 'Pillow', 'cryptography', 'cffi')},
                'source_sha256': source_hashes,
                'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
                'acceptance': {'core': report['core'], 'gui_checks': report['gui']['checks'],
                               'qt_platform': report['gui']['qt_platform'], 'ocr': report['gui'].get('ocr')},
                'signed': False}
    (bundle / 'BUILD-INFO.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    instructions = ('PDF Workbench ' + version + '\n\n'
                    'Das gesamte Archiv in einen eigenen Ordner entpacken.\n'
                    'Windows: PDFWorkbench.exe per Doppelklick starten.\n'
                    'Linux: im entpackten Ordner ./PDFWorkbench ausführen.\n'
                    'Den Ordner _internal zusammen mit dem Programm behalten.\n'
                    'Python muss nicht installiert werden.\n\n'
                    'PDFs hinzufügen, Seiten auswählen und anordnen.\n'
                    'Die Vorschau bietet Kommentare, Textmarkierungen und gezeichnete Unterschriften.\n'
                    'Einfügen ergänzt Textfelder, Bilder, Stempel, Häkchen und Kreuze.\n'
                    'Mit dem Pfeilwerkzeug auswählen und ziehen; Doppelklick bearbeitet.\n'
                    'Strg+F sucht Text. Unter Datei lassen sich Bilder als PDF-Seiten übernehmen.\n'
                    'Datei > Durchsuchbare PDF erstellen nutzt optional eine lokale Tesseract-Installation.\n'
                    'Ansicht > Darstellung bietet System, Hell und Dunkel.\n'
                    'Werkzeuge bietet PDF-Komprimierung, Passwortschutz und das Ausfüllen vorhandener Formulare.\n'
                    'Werkzeuge > Lesezeichen bearbeitet Kapitel und Unterkapitel.\n'
                    'Seiten zuschneiden und Seitenformat ändern bieten eine echte PDF-Vorschau.\n'
                    'Strg+S speichert ein Projekt mit eingebetteten PDFs und Rückgängig-Historie.\n'
                    'Unter Datei lassen sich Projekte öffnen und automatische Sicherungen wiederherstellen.\n'
                    'Hilfe > Programm aktualisieren prüft, lädt und installiert neue Versionen nach Bestätigung.\n'
                    'Normale Exporte übernehmen einen Quellpasswortschutz nicht automatisch.\n'
                    'Ansicht > Darstellung > Bewegung reduzieren schaltet kurze Animationen ab.\n'
                    'PDF speichern schreibt eine neue Datei; Originale werden nicht geändert.\n\n'
                    'Anleitung: https://github.com/MGCrafter/pdf-workbench#loslegen\n'
                    'Quellcode und Drittanbieterquellen: https://github.com/MGCrafter/pdf-workbench/releases\n')
    (bundle / 'START_HERE.txt').write_text(instructions, encoding='utf-8')
    # Last mutation of the app bundle: inventory includes provenance, final
    # audited notices and the startup instructions in the public archive.
    sys.path.insert(0, str(ROOT / 'src'))
    from pdf_workbench.update_installer import write_bundle_manifest, validate_bundle
    write_bundle_manifest(bundle, app_version=version)
    validate_bundle(bundle, expected_version=version)
    output.mkdir(parents=True, exist_ok=True)
    suffix = '.zip' if target == 'windows' else '.tar.gz'
    archive = output / f'PDFWorkbench-v{version}-{target}-x86_64{suffix}'
    if archive.exists():
        raise FileExistsError(f'Release asset already exists: {archive}')
    for path in bundle.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(bundle.resolve()):
            raise ValueError(f'Bundle symlink escapes its directory: {path.name}')
    if target == 'windows':
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as stream:
            for path in sorted(bundle.rglob('*')):
                if path.is_file():
                    stream.write(path, 'PDFWorkbench/' + path.relative_to(bundle).as_posix())
        with zipfile.ZipFile(archive) as stream:
            if stream.testzip() is not None:
                raise ValueError('Release archive failed CRC verification.')
    else:
        with tarfile.open(archive, 'w:gz', compresslevel=9) as stream:
            stream.add(bundle, arcname='PDFWorkbench')
        with tarfile.open(archive, 'r:gz') as stream:
            if 'PDFWorkbench/PDFWorkbench' not in stream.getnames():
                raise ValueError('Linux release archive has no executable.')
    for label, payload in (('BUILD-INFO', metadata), ('acceptance', report)):
        path = output / f'PDFWorkbench-v{version}-{target}-{label}.json'
        with path.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(payload, indent=2) + '\n')
    print(f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}')
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--notices', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-results', type=Path, required=True)
    args = parser.parse_args()
    package(args.bundle.resolve(), args.notices.resolve(), args.output.resolve(), args.test_results.resolve())


if __name__ == '__main__':
    main()
