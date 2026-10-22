"""Optional local Tesseract OCR; retain original PDF graphics and annotations.

Only the disposable recognition image is rasterized. The exported document
keeps its original page contents and gains an invisible, searchable text layer.
No engine or language file is downloaded implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import io
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Mapping, Sequence
import urllib.request

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream

from .annotations import visible_page_box
from .core import (CancelCheck, PageRef, PdfWorkbenchError, Progress, SourceDocument,
                   _check_cancel, _protect_destination, export_pdf)


class OcrError(PdfWorkbenchError):
    """An optional OCR component is missing or recognition could not finish."""


TESSDATA_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
TESSDATA_LICENSE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
TESSDATA_LICENSE_TEXT = r"""
                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
LANGUAGE_PACKS = {
    "eng": {"name": "Englisch", "size": 4113088,
            "sha256": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"},
    "deu": {"name": "Deutsch", "size": 1525436,
            "sha256": "19d219bbb6672c869d20a9636c6816a81eb9a71796cb93ebe0cb1530e2cdb22d"},
}


@dataclass(frozen=True)
class OcrEngine:
    executable: Path
    version: str
    tessdata_dir: Path | None
    pdf_font: Path | None
    languages: tuple[str, ...]


@dataclass(frozen=True)
class OcrResult:
    path: Path
    processed_pages: int
    skipped_pages: int


def default_cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "pdf-workbench/tessdata"


def external_environment() -> dict[str, str]:
    """System tools must not load PyInstaller's bundled shared libraries."""
    environment = os.environ.copy()
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if key + "_ORIG" in environment:
            environment[key] = environment[key + "_ORIG"]
        elif getattr(sys, "frozen", False):
            environment.pop(key, None)
    environment["LC_ALL"] = "C"
    environment.setdefault("OMP_THREAD_LIMIT", "2")
    return environment


def _verified_pack(code: str, directory: Path) -> Path | None:
    path = directory / (code + ".traineddata")
    if not path.is_file() or path.is_symlink():
        return None
    lock = LANGUAGE_PACKS[code]
    if path.stat().st_size != lock["size"]:
        return None
    return path if hashlib.sha256(path.read_bytes()).hexdigest() == lock["sha256"] else None


def _file_identity(status) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _is_owned_file(path: Path, identity: tuple[int, int]) -> bool:
    if not identity[1]:
        return False
    try:
        status = path.lstat()
        return stat.S_ISREG(status.st_mode) and _file_identity(status) == identity
    except FileNotFoundError:
        return False


def _unlink_owned(path: Path, identity: tuple[int, int]):
    # A canceled writer may outlive a concurrent rename/replacement. Its file
    # descriptor identifies what it created; a pathname alone does not.
    if _is_owned_file(path, identity):
        path.unlink(missing_ok=True)


def _install_model_notices(directory: Path):
    license_data = TESSDATA_LICENSE_TEXT.encode("utf-8")
    if hashlib.sha256(license_data).hexdigest() != TESSDATA_LICENSE_SHA256:
        raise OcrError("Der mitgelieferte Sprachmodell-Lizenztext ist verändert.")
    origin = f"https://github.com/tesseract-ocr/tessdata_fast/tree/{TESSDATA_COMMIT}"
    source_data = (f"Tesseract tessdata_fast language models\nSource: {origin}\nCommit: {TESSDATA_COMMIT}\n"
                   f"License: https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_COMMIT}/LICENSE\nLicense SHA-256: {TESSDATA_LICENSE_SHA256}\n" +
                   "".join(f"{code}.traineddata SHA-256: {lock['sha256']}\n" for code, lock in LANGUAGE_PACKS.items())).encode()
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in (("tessdata_fast-LICENSE", license_data), ("tessdata_fast-SOURCE.txt", source_data)):
        target = directory/name
        if os.path.lexists(target):
            if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                raise OcrError("Ein vorhandener Sprachmodell-Lizenzbeleg wird nicht überschrieben.")
            continue
        descriptor, temporary_name = tempfile.mkstemp(prefix=".ocr-notice-", suffix=".partial", dir=directory)
        temporary = Path(temporary_name)
        temporary_identity = _file_identity(os.fstat(descriptor))
        created_identity = None
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if not _is_owned_file(temporary, temporary_identity):
                raise OcrError("Der temporäre Sprachmodell-Lizenzbeleg wurde gleichzeitig verändert.")
            try:
                os.link(temporary,target)
                created_identity = temporary_identity
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != data:
                    raise OcrError("Der Sprachmodell-Lizenzbeleg wurde gleichzeitig verändert.")
            except OSError:
                with target.open("xb") as stream:
                    created_identity = _file_identity(os.fstat(stream.fileno()))
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                raise OcrError("Der Sprachmodell-Lizenzbeleg wurde gleichzeitig verändert.")
        except BaseException:
            if created_identity is not None:
                _unlink_owned(target, created_identity)
            raise
        finally:
            _unlink_owned(temporary, temporary_identity)


def cached_languages(cache_dir=None) -> tuple[str, ...]:
    directory = Path(cache_dir) if cache_dir else default_cache_dir()
    return tuple(code for code in LANGUAGE_PACKS if _verified_pack(code, directory))


_EXTERNAL_START_LOCK = threading.Lock()


def _external_popen(command, **options):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return subprocess.Popen(command, **options)
    # PyInstaller changes the process DLL directory. A child must resolve its
    # own libraries; restore the app's previous directory after process start.
    with _EXTERNAL_START_LOCK:
        kernel = ctypes.windll.kernel32
        get_directory, set_directory = kernel.GetDllDirectoryW, kernel.SetDllDirectoryW
        get_directory.argtypes = [ctypes.c_uint, ctypes.c_wchar_p]
        get_directory.restype = ctypes.c_uint
        set_directory.argtypes = [ctypes.c_wchar_p]
        set_directory.restype = ctypes.c_bool
        needed = get_directory(0, None)
        previous = None
        if needed:
            buffer = ctypes.create_unicode_buffer(needed + 1)
            get_directory(len(buffer), buffer); previous = buffer.value
        if not set_directory(None):
            raise OcrError("Die DLL-Suchpfade konnten für Tesseract nicht vorbereitet werden.")
        process = None
        try:
            process = subprocess.Popen(command, **options)
            return process
        finally:
            if not set_directory(previous):
                if process is not None and process.poll() is None:
                    process.terminate(); process.wait(timeout=2)
                raise OcrError("Der DLL-Suchpfad der PDF-App konnte nicht wiederhergestellt werden.")


def _capture_tool(command, cancel=None, timeout=8):
    _check_cancel(cancel)
    options = {"env": external_environment()}
    if sys.platform == "win32": options["creationflags"] = subprocess.CREATE_NO_WINDOW
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error_output:
        process = _external_popen(command, stdout=output, stderr=error_output, **options)
        started = time.monotonic()
        try:
            while process.poll() is None:
                _check_cancel(cancel)
                if time.monotonic() - started > timeout:
                    raise OcrError("Die Prüfung von Tesseract hat das Zeitlimit überschritten.")
                time.sleep(.05)
            _check_cancel(cancel)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
        output.seek(0); error_output.seek(0)
        result = (output.read(1024*1024).decode("utf-8", "replace"), error_output.read(1024*1024).decode("utf-8", "replace"))
        if process.returncode:
            raise OcrError("Tesseract konnte nicht geprüft werden: " + result[1][-1000:])
        return result


def discover_engine(executable=None, cache_dir=None, *, cancel=None) -> OcrEngine | None:
    found = str(executable) if executable else shutil.which("tesseract")
    if not found and sys.platform == "win32":
        for variable in ("ProgramFiles", "PROGRAMFILES", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base and (candidate := Path(base)/"Tesseract-OCR/tesseract.exe").is_file():
                found = str(candidate); break
    if not found:
        return None
    path = Path(found).expanduser().resolve()
    if not path.is_file():
        raise OcrError("Die gewählte Tesseract-Programmdatei wurde nicht gefunden.")
    try:
        version = _capture_tool([str(path), "--version"], cancel)[0].splitlines()[0]
        version_match = re.match(r"tesseract v?(\d+)\.(\d+)", version)
        if not version_match or tuple(map(int, version_match.groups())) < (4, 1):
            raise OcrError("Bitte Tesseract 4.1 oder neuer verwenden.")
        result = _capture_tool([str(path), "--list-langs"], cancel)
    except (OSError, subprocess.SubprocessError, IndexError) as error:
        raise OcrError(f"Tesseract konnte nicht geprüft werden: {error}") from error
    output = result[0] + "\n" + result[1]
    match = re.search(r'List of available languages in "([^"]+)"', output)
    reported_languages = {line for line in output.splitlines()
                          if re.fullmatch(r"[a-z][a-z0-9_/-]+", line)}
    prefix = path.parent.parent
    candidates = []
    if match:
        candidates.append(Path(match.group(1)).expanduser())
    if configured := os.environ.get("TESSDATA_PREFIX"):
        candidates.extend((Path(configured).expanduser(), Path(configured).expanduser() / "tessdata"))
    candidates.extend((path.parent / "tessdata", prefix / "share/tessdata",
                       prefix / f"share/tesseract-ocr/{version_match.group(1)}/tessdata",
                       prefix / "share/tesseract-ocr/4.00/tessdata",
                       prefix / "share/tesseract-ocr/5/tessdata"))
    candidates = list(dict.fromkeys(candidates))
    # Tesseract 4.1's language list has no directory in its header. Locate
    # distro/portable data using the executable prefix and explicit settings.
    tessdata = next((directory for directory in candidates
                     if any((directory / (code + ".traineddata")).is_file()
                            for code in reported_languages)), None)
    pdf_font = next((directory / "pdf.ttf" for directory in candidates if (directory / "pdf.ttf").is_file()), None)
    languages = {code for code in reported_languages
                 if tessdata and (tessdata / (code + ".traineddata")).is_file()}
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    languages.update(cached_languages(cache))
    return OcrEngine(path, version, tessdata, pdf_font, tuple(sorted(languages)))


def download_language(code: str, cache_dir=None, cancel: CancelCheck | None = None,
                      progress: Progress | None = None) -> Path:
    if code not in LANGUAGE_PACKS:
        raise OcrError("Dieses Sprachpaket wird nicht angeboten.")
    directory = Path(cache_dir) if cache_dir else default_cache_dir()
    _check_cancel(cancel)
    _install_model_notices(directory)
    existing = _verified_pack(code, directory)
    if existing:
        if progress: progress(100, f"{LANGUAGE_PACKS[code]['name']} ist bereits vorhanden.")
        return existing
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (code + ".traineddata")
    lock = LANGUAGE_PACKS[code]
    url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_COMMIT}/{code}.traineddata"
    descriptor, name = tempfile.mkstemp(prefix="." + code + "-", suffix=".partial", dir=directory)
    temporary = Path(name)
    temporary_identity = _file_identity(os.fstat(descriptor))
    created_identity = None
    try:
        digest, received = hashlib.sha256(), 0
        request = urllib.request.Request(url, headers={"User-Agent": "PDFWorkbench-local-OCR/1"})
        with os.fdopen(descriptor, "wb") as stream, urllib.request.urlopen(request, timeout=10) as response:
            while True:
                _check_cancel(cancel)
                chunk = response.read(64 * 1024)
                if not chunk: break
                received += len(chunk)
                if received > lock["size"]:
                    raise OcrError("Das Sprachpaket hat eine unerwartete Größe.")
                digest.update(chunk); stream.write(chunk)
                if progress: progress(int(received * 99 / lock["size"]), f"{lock['name']}: {received // 1024} / {lock['size'] // 1024} KiB")
            stream.flush(); os.fsync(stream.fileno())
        _check_cancel(cancel)
        if received != lock["size"] or digest.hexdigest() != lock["sha256"]:
            raise OcrError("Das Sprachpaket ist unvollständig oder seine Prüfsumme stimmt nicht.")
        if not _is_owned_file(temporary, temporary_identity):
            raise OcrError("Der temporäre Sprachcache wurde gleichzeitig verändert.")
        if os.path.lexists(target):
            existing = _verified_pack(code, directory)
            if existing: return existing
            raise OcrError("Im Sprachcache liegt bereits eine andere Datei. Bitte einen neuen Cacheordner verwenden.")
        try:
            os.link(temporary, target)
            created_identity = temporary_identity
        except FileExistsError:
            existing = _verified_pack(code, directory)
            if not existing: raise OcrError("Das Sprachpaket wurde gleichzeitig verändert.")
        except OSError:
            # Exclusive creation also supports filesystems without hard links.
            with target.open("xb") as stream:
                created_identity = _file_identity(os.fstat(stream.fileno()))
                with temporary.open("rb") as input_stream:
                    while chunk := input_stream.read(64 * 1024):
                        _check_cancel(cancel); stream.write(chunk)
                stream.flush(); os.fsync(stream.fileno())
        _check_cancel(cancel)
        if not _verified_pack(code, directory):
            raise OcrError("Das Sprachpaket wurde gleichzeitig verändert.")
        if progress: progress(100, f"{lock['name']} ist bereit.")
        return target
    except (PdfWorkbenchError, KeyboardInterrupt):
        raise
    except Exception as error:
        raise OcrError(f"Das Sprachpaket konnte nicht geladen werden: {error}") from error
    finally:
        if created_identity is not None and sys.exc_info()[0] is not None:
            _unlink_owned(target, created_identity)
        _unlink_owned(temporary, temporary_identity)


def _language_files(engine: OcrEngine, languages, cache: Path):
    if not languages or any(code not in LANGUAGE_PACKS for code in languages):
        raise OcrError("Bitte Deutsch und/oder Englisch auswählen.")
    files = {}
    for code in dict.fromkeys(languages):
        path = _verified_pack(code, cache)
        if path is None and engine.tessdata_dir:
            candidate = engine.tessdata_dir / (code + ".traineddata")
            if candidate.is_file(): path = candidate
        if path is None:
            raise OcrError(f"Das Sprachpaket {LANGUAGE_PACKS[code]['name']} fehlt. Bitte zuerst bewusst herunterladen oder lokal installieren.")
        files[code] = path
    if engine.pdf_font is None or not engine.pdf_font.is_file():
        raise OcrError("Tesseract benötigt pdf.ttf aus seiner vollständigen Installation, um durchsuchbare PDFs zu erzeugen.")
    return files


def _run_tesseract(engine, image: Path, output: Path, languages, tessdata: Path,
                   dpi: int, timeout: float, cancel):
    command = [str(engine.executable), str(image), str(output), "--tessdata-dir", str(tessdata),
               "-l", "+".join(languages), "--dpi", str(dpi),
               "-c", "textonly_pdf=1", "-c", "tessedit_create_pdf=1"]
    options = {"env": external_environment(), "stdout": subprocess.DEVNULL}
    if sys.platform == "win32": options["creationflags"] = subprocess.CREATE_NO_WINDOW
    with output.with_suffix(".log").open("wb") as log:
        try:
            process = _external_popen(command, stderr=log, **options)
        except OSError as error:
            raise OcrError(f"Tesseract konnte nicht gestartet werden: {error}") from error
        started = time.monotonic()
        try:
            while process.poll() is None:
                _check_cancel(cancel)
                if time.monotonic() - started > timeout:
                    raise OcrError(f"Die Texterkennung hat das Zeitlimit von {timeout:g} Sekunden überschritten.")
                time.sleep(.05)
            _check_cancel(cancel)
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
    if process.returncode != 0 or not output.with_suffix(".pdf").is_file():
        message = output.with_suffix(".log").read_text(encoding="utf-8", errors="replace")[-2000:]
        raise OcrError("Tesseract konnte die Seite nicht erkennen. " + message.strip())


def _text_only_page(path: Path):
    reader = PdfReader(path)
    if len(reader.pages) != 1 or reader.pages[0].get("/Annots"):
        raise OcrError("Die OCR-Ausgabe enthält keine einzelne reine Textebene.")
    page = reader.pages[0]
    content = page.get_contents()
    mode = 0; stack = []
    if content is not None:
        for operands, operator in ContentStream(content, reader).operations:
            if operator == b"q": stack.append(mode)
            elif operator == b"Q": mode = stack.pop() if stack else 0
            if operator == b"Tr": mode = int(operands[0])
            if operator in (b"Do", b"INLINE IMAGE", b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"sh"):
                raise OcrError("Die OCR-Ausgabe enthält sichtbare Grafik statt einer reinen Textebene.")
            if operator in (b"Tj", b"TJ", b"'", b'"') and mode != 3:
                raise OcrError("Die OCR-Ausgabe enthält sichtbaren Text.")
    return reader, page


def _layer_transform(page, layer):
    left, bottom, right, top = visible_page_box(page)
    width, height = right - left, top - bottom
    layer_width, layer_height = float(layer.mediabox.width), float(layer.mediabox.height)
    if min(layer_width, layer_height) <= 0 or not all(math.isfinite(v) for v in (layer_width, layer_height)):
        raise OcrError("Die OCR-Textebene hat ein ungültiges Seitenformat.")
    rotation = int(page.get("/Rotate", 0)) % 360
    if rotation == 0: return (width/layer_width, 0, 0, height/layer_height, left, bottom)
    if rotation == 90: return (0, height/layer_width, -width/layer_height, 0, right, bottom)
    if rotation == 180: return (-width/layer_width, 0, 0, -height/layer_height, right, top)
    if rotation == 270: return (0, -height/layer_width, width/layer_height, 0, left, top)
    raise OcrError("Diese Seitendrehung wird von der Texterkennung nicht unterstützt.")


def export_searchable_pdf(sources: Mapping[str, SourceDocument], pages: Sequence[PageRef],
                          destination, *, engine: OcrEngine | None = None,
                          languages=("deu", "eng"), cache_dir=None, dpi=300, timeout=120,
                          cancel: CancelCheck | None = None, progress: Progress | None = None, bookmarks=None,
                          publication_record=None) -> OcrResult:
    target = Path(destination).expanduser().absolute()
    _protect_destination(target, sources); _check_cancel(cancel)
    if not pages or target.suffix.lower() != ".pdf":
        raise OcrError("Bitte Seiten und einen neuen PDF-Dateinamen auswählen.")
    if not target.parent.is_dir():
        raise OcrError("Bitte einen vorhandenen Zielordner auswählen.")
    if not isinstance(dpi, int) or isinstance(dpi, bool) or not 72 <= dpi <= 600 or not math.isfinite(timeout) or timeout <= 0:
        raise OcrError("Für OCR werden 72 bis 600 dpi und ein positives Zeitlimit benötigt.")
    cache = Path(cache_dir) if cache_dir else default_cache_dir()
    engine = engine or discover_engine(cache_dir=cache)
    if engine is None: raise OcrError("Tesseract wurde nicht gefunden. Bitte zuerst eine lokale Installation auswählen.")
    language_files = _language_files(engine, languages, cache)
    from PyQt6 import sip
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions
    if QGuiApplication.instance() is None:
        raise OcrError("Zum Rendern der Erkennungsbilder muss die PDF-App laufen.")
    processed = skipped = 0
    with tempfile.TemporaryDirectory(prefix=".pdf-workbench-ocr-", dir=target.parent) as temporary:
        directory = Path(temporary)
        base = export_pdf(sources, pages, directory / "original-pages.pdf", cancel, bookmarks=bookmarks)
        reader = PdfReader(base); writer = PdfWriter(); writer.clone_document_from_reader(reader)
        document = QPdfDocument(None)
        try:
            document.load(str(base))
            if document.status() != QPdfDocument.Status.Ready:
                raise OcrError("Die Seiten konnten für OCR nicht geöffnet werden.")
            tessdata = directory / "tessdata"; tessdata.mkdir()
            for code, path in language_files.items(): shutil.copyfile(path, tessdata / (code + ".traineddata"))
            shutil.copyfile(engine.pdf_font, tessdata / "pdf.ttf")
            for index, page in enumerate(writer.pages):
                _check_cancel(cancel)
                if progress: progress(int(index * 85 / len(pages)), f"Texterkennung: Seite {index+1} von {len(pages)} …")
                if (reader.pages[index].extract_text() or "").strip():
                    skipped += 1; continue
                left, bottom, right, top = visible_page_box(page)
                unit = float(page.get("/UserUnit", 1))
                if not math.isfinite(unit) or unit <= 0: raise OcrError("Die Seite hat eine ungültige PDF-Maßeinheit.")
                width, height = (right-left)*unit, (top-bottom)*unit
                if int(page.get("/Rotate", 0)) % 180: width, height = height, width
                pixels = (max(1, round(width*dpi/72)), max(1, round(height*dpi/72)))
                if pixels[0]*pixels[1] > 40_000_000 or max(pixels) > 15000:
                    raise OcrError("Die Seite ist für diese OCR-Auflösung zu groß. Bitte weniger dpi verwenden.")
                image = document.render(index, QSize(*pixels), QPdfDocumentRenderOptions())
                _check_cancel(cancel)
                image_path = directory / f"page-{index}.png"
                if image.isNull() or not image.save(str(image_path), "PNG"):
                    raise OcrError("Das Erkennungsbild konnte nicht erzeugt werden.")
                output = directory / f"text-{index}"
                _run_tesseract(engine, image_path, output, languages, tessdata, dpi, timeout, cancel)
                layer_reader, layer = _text_only_page(output.with_suffix(".pdf"))
                page.merge_transformed_page(layer, _layer_transform(page, layer), over=True, expand=False)
                processed += 1
            _check_cancel(cancel)
            stream = io.BytesIO(); writer.write(stream); data = stream.getvalue()
            # The first export has already applied the workspace's edited
            # outline and output page order. Carry that exact safe outline
            # through the final export, including an explicitly empty tree.
            from .bookmarks import read_source_bookmarks
            staged_bookmarks, staged_warnings = read_source_bookmarks(reader, cancel)
            staged = SourceDocument("ocr-result", directory/"merged.pdf", data, len(pages),
                                    tuple((float(page.mediabox.width), float(page.mediabox.height)) for page in writer.pages),
                                    bookmarks=staged_bookmarks, bookmark_warnings=staged_warnings)
            final_pages = tuple(PageRef(f"ocr-{index}", staged.source_id, index) for index in range(len(pages)))
            result = export_pdf({**sources, staged.source_id: staged}, final_pages, target, cancel,
                                lambda value, message: progress(85+value*15//100, message) if progress else None,
                                publication_record=publication_record)
            return OcrResult(result, processed, skipped)
        finally:
            try:
                document.close()
            finally:
                # Qt keeps its QFile device alive after close(). Destroy it
                # synchronously before TemporaryDirectory removes the PDF.
                sip.delete(document)
