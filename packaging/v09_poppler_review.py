"""Check actual v0.9 exports with Poppler, independently of MuPDF/Qt rendering."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image
from pypdf import PdfReader


def command(*args):
    process = subprocess.run([str(arg) for arg in args], check=True, capture_output=True, timeout=45)
    return process.stdout.decode('utf-8', 'replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    assert shutil.which('pdftoppm') and shutil.which('pdftotext') and shutil.which('pdfsig')
    checks = []; inputs = {}
    def source(case, name):
        path = args.core / case / name; inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest(); return path
    def raster(path, number, label):
        prefix = args.output / label
        command('pdftoppm', '-f', number, '-l', number, '-singlefile', '-r', 72, '-cropbox', '-png', path, prefix)
        return Image.open(prefix.with_suffix('.png')).convert('RGB')
    def red(image, point):
        value = image.getpixel(point); assert value[0] > 220 and max(value[1:]) < 45, (point, value)
    def green(image, point):
        value = image.getpixel(point); assert value[1] > 140 and max(value[::2]) < 45, (point, value)

    path = source('test_duplex_reverse_odd_fronts_and_one_undo', 'ordered.pdf')
    expected = ['F1', 'B1', 'F2', 'B2', 'F3']
    for index, label in enumerate(expected, 1):
        text = command('pdftotext', '-f', index, '-l', index, '-raw', path, '-')
        assert label in text and all(other not in text for other in expected if other != label)
        image = raster(path, index, 'duplex-' + str(index)); assert image.size == (300, 500)
        red(image, (279, 479) if index in (2, 4) else (21, 21))
        green(image, (35, 35) if index in (2, 4) else (265, 465))
    checks.append('Poppler duplex F1,B1,F2,B2,F3 with correct 180-degree back marker orientation')

    path = source('test_image_text_exports_follow_final_selection_and_rotation', 'selected.pdf')
    for index, size, marker, greenpoint in ((1, (500, 300), (479, 21), (35, 265)), (2, (300, 500), (21, 21), (265, 465))):
        image = raster(path, index, 'selected-' + str(index)); assert image.size == size
        red(image, marker); green(image, greenpoint)
        exported = Image.open(args.core / 'test_image_text_exports_follow_final_selection_and_rotation' / f'Seite-{index:04d}.png').convert('RGB')
        assert exported.size == image.size
        red(exported, marker); green(exported, greenpoint)
    checks.append('Poppler selected A2,A1 page dimensions/rotation/color markers agree with PNG export')

    path = source('test_redaction_physically_removes_original_streams_metadata_and_annotations', 'redacted.pdf')
    image = raster(path, 1, 'redacted'); assert image.getpixel((70, 60)) == (0, 0, 0); green(image, (265, 465))
    assert not command('pdftotext', path, '-').strip()
    checks.append('Poppler redacted area is black, outside green marker retained, no extractable text')

    path = source('test_text_replacement_isolated_copy_rejects_overflow_and_undo', 'text.pdf')
    text = command('pdftotext', '-f', 2, '-l', 2, '-raw', path, '-')
    assert 'Z1' in text and 'A1' not in text and 'PUBLIC' in text
    image = raster(path, 2, 'edited-text'); red(image, (21, 21)); green(image, (265, 465))
    checks.append('Poppler sees replaced Z1 without old A1 and preserves vector markers/PUBLIC text')

    path = source('test_toc_and_links_follow_reordered_rotated_pages', 'linked.pdf')
    reader = PdfReader(path); link = reader.pages[0]['/Annots'][0].get_object()
    dest = link.get('/Dest') or link['/A']['/D']
    assert dest[0].idnum == reader.pages[1].indirect_reference.idnum
    assert tuple(round(float(n), 2) for n in link['/Rect']) == (60., 50., 90., 200.)
    path = source('test_toc_and_links_follow_reordered_rotated_pages', 'toc.pdf')
    reader = PdfReader(path); children = reader.outline[1]
    assert reader.get_destination_page_number(children[0]) == 2 and reader.get_destination_page_number(children[1]) == 1
    image = raster(path, 1, 'contents'); assert image.size == (596, 842)
    text = command('pdftotext', '-f', 1, '-l', 1, path, '-'); assert 'Kapitel Ä' in text and 'A1' in text and 'A2' in text
    checks.append('Independent pypdf resolves rotated raw Link Rect/targets and ToC child destinations; Poppler renders text')

    path = source('test_pdfa_requires_actual_validator_and_preserves_order_orientation', 'archive.pdf')
    text = command('pdftotext', '-f', 1, '-l', 1, '-raw', path, '-'); assert 'A2' in text and 'A1' not in text
    image = raster(path, 1, 'archive'); assert image.size == (500, 300); red(image, (479, 21)); green(image, (35, 265))
    checks.append('Poppler validates actual PDF/A output order, landscape dimensions and rotated color markers')

    path = source('test_signature_integrity_trust_separation_and_tamper', 'signed.pdf')
    report = command('pdfsig', '-nocert', path)
    assert 'Signature is Valid' in report and 'Total document signed' in report
    (args.output / 'pdfsig.txt').write_text(report)
    checks.append('Independent Poppler pdfsig verifies real signature integrity and full document coverage')
    report = {'result': 'PASS', 'checks': checks, 'input_sha256': inputs}
    (args.output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'result': 'PASS', 'checks': len(checks), 'output': str(args.output)}))


if __name__ == '__main__': main()
