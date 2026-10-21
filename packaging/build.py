"""Build on the target OS; use --release with a verified notices directory."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build_provenance(release: bool) -> dict:
    sys.path.insert(0, str(ROOT / 'src'))
    from pdf_workbench import __version__
    with (ROOT / 'pyproject.toml').open('rb') as stream:
        version = tomllib.load(stream)['project']['version']
    if version != __version__:
        raise ValueError('pyproject.toml and the app version differ.')
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        commit = None
    else:
        commit = result.stdout.strip() if result.returncode == 0 else None
    if release:
        if commit is None:
            raise ValueError('A release requires a committed source checkout.')
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all',
                                         '--', 'src', 'packaging', 'pyproject.toml'], cwd=ROOT, text=True)
        if dirty.strip():
            raise ValueError('Commit source and packaging changes before a release build.')
    return {'schema_version': 1, 'app_version': version, 'source_commit': commit,
            'source_sha256': {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted((ROOT / 'src').rglob('*'))
                              if path.is_file() and path.suffix in ('.py', '.ttf', '.txt')}}


def verify_notices(directory: Path) -> dict:
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version') != 1 or manifest.get('complete') is not True or not manifest.get('files'):
        raise ValueError('Release notices are incomplete; run packaging/collect_notices.py first.')
    from PyQt6.QtCore import qVersion
    versions = {'Qt': qVersion(), **{name: importlib.metadata.version(name) for name in
                ('PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'pypdf', 'PyInstaller', 'Pillow', 'cryptography')}}
    for name, version in versions.items():
        if manifest.get('versions', {}).get(name) != version:
            raise ValueError(f'Notices describe a different {name} version.')
    for relative, digest in manifest['files'].items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError(f'Invalid notice path: {relative}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Notice checksum mismatch: {relative}')
    if not manifest.get('source_archives'):
        raise ValueError('Corresponding-source manifest is missing.')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distpath', type=Path, default=ROOT / 'dist')
    parser.add_argument('--workpath', type=Path, default=ROOT / 'build')
    parser.add_argument('--release', action='store_true')
    parser.add_argument('--notices', type=Path)
    args = parser.parse_args()
    distpath, workpath = args.distpath.resolve(), args.workpath.resolve()
    provenance = build_provenance(args.release)
    workpath.mkdir(parents=True, exist_ok=True)
    provenance_path = workpath / 'APP_PROVENANCE.json'
    provenance_path.write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    notice_data = []
    if args.release:
        if not args.notices:
            parser.error('--release requires --notices')
        verify_notices(args.notices)
        notice_data = ['--add-data', f'{args.notices.resolve()}:licenses']
    else:
        notice_data = ['--add-data', f'{ROOT / "licenses" / "pypdf-LICENSE"}:licenses']
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
        '--onedir', '--name', 'PDFWorkbench', '--paths', str(ROOT / 'src'),
        '--paths', str(ROOT / 'packaging'),
        '--distpath', str(distpath), '--workpath', str(workpath), '--specpath', str(workpath),
        '--copy-metadata', 'pypdf', '--copy-metadata', 'PyQt6',
        '--copy-metadata', 'Pillow', '--copy-metadata', 'cryptography',
        '--add-data', f'{ROOT / "LICENSE"}:.',
        '--add-data', f'{ROOT / "THIRD_PARTY_NOTICES.md"}:.',
        '--add-data', f'{provenance_path}:.',
        '--add-data', f'{ROOT / "src/pdf_workbench/fonts"}:pdf_workbench/fonts',
        *notice_data, str(ROOT / 'packaging/entry.py'),
    ], cwd=ROOT, check=True)
    print(f'Build: {distpath / "PDFWorkbench"}')


if __name__ == '__main__':
    main()
