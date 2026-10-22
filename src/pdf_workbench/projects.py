"""Self-contained, bounded projects and crash recovery without pickle or source writes.

Project ZIPs embed immutable source PDFs, editable objects and paired history.
Projects containing previously encrypted PDFs require AES-256-GCM protection.
Passwords are never serialized. Recovery generations are new files, not edits
of the user's manual project, and active sessions are protected by OS locks.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile
import time
from uuid import uuid4
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pypdf import PdfReader

from .annotations import PageAnnotation
from .annotation_assets import restore_image_asset
from .bookmarks import SourceBookmark, WorkspaceBookmark
from .core import (OperationCancelled, PdfWorkbenchError, SourceDocument, PageRef,
                   Workspace, WorkspaceState, _check_cancel)
from .forms import validate_forms
from .text_appearance import TextStyle

ENCRYPTED_MAGIC = b'PDFWBENC1'
MAX_PROJECT_BYTES = 1024 ** 3
MAX_UNPACKED_BYTES = 2 * 1024 ** 3
MAX_MANIFEST_BYTES = 16 * 1024 ** 2
MAX_MEMBERS = 20_000
ID = re.compile(r'[A-Za-z0-9_.-]{1,200}\Z')
RECOVERY_NAME = re.compile(r'([a-f0-9]{32})-([0-9]{16,22})-([a-f0-9]{32})\.pdfwb\Z')


class ProjectPasswordRequired(PdfWorkbenchError):
    pass


class IncorrectProjectPassword(PdfWorkbenchError):
    pass


@dataclass(frozen=True)
class ProjectSnapshot:
    state: WorkspaceState
    selected_ids: tuple[str, ...] = ()
    active_uid: str | None = None
    zoom_mode: str = 'page'
    zoom_factor: float = 1.0
    project_path: str | None = None


@dataclass(frozen=True)
class ProjectIdentity:
    device: int
    inode: int
    modified_ns: int
    size: int
    sha256: str


@dataclass(frozen=True)
class LoadedProject:
    snapshot: ProjectSnapshot
    path: Path
    identity: ProjectIdentity | None
    encrypted: bool
    recovered_from: Path | None = None


@dataclass(frozen=True)
class RecoveryEntry:
    path: Path
    session_id: str
    modified_ns: int
    encrypted: bool


def _error(message='Die Projektdatei ist beschädigt oder wird noch nicht unterstützt.'):
    return PdfWorkbenchError(message)


def _json_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error('Die Projektdatei enthält doppelte Einträge.')
        result[key] = value
    return result


def _identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise _error('Eine Projektkennung ist ungültig.')
    return value


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise _error('Eine Zahlenangabe im Projekt ist ungültig.')
    return value


def _text(value, limit=32_768):
    if not isinstance(value, str) or '\0' in value or len(value) > limit:
        raise _error('Eine Textangabe im Projekt ist ungültig.')
    return value


def _bookmarks(tree, *, source=False, depth=0, budget=None):
    if depth > 64 or not isinstance(tree, list):
        raise _error('Die Lesezeichenhierarchie im Projekt ist ungültig.')
    budget = [10_000] if budget is None else budget
    result = []
    for value in tree:
        budget[0] -= 1
        if budget[0] < 0 or not isinstance(value, dict): raise _error()
        children = _bookmarks(value['children'], source=source, depth=depth+1, budget=budget)
        title = _text(value['title'], 2000)
        if source:
            target = value['page_index']
            if target is not None: _integer(target, 0, 100_000)
            result.append(SourceBookmark(title, target, children))
        else:
            uid = _identifier(value['uid']); target = value['page_uid']
            if target is not None: _identifier(target)
            result.append(WorkspaceBookmark(uid, title, target, children))
    return tuple(result)


def _password_key(password, salt):
    if not isinstance(password, str) or not password or len(password.encode('utf-8')) > 1024:
        raise _error('Bitte verwende ein Projektpasswort mit 1 bis 1024 UTF-8-Bytes.')
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password.encode('utf-8'))


def snapshot_requires_password(snapshot):
    # Historical sources are embedded too; Undo must not leak decrypted data.
    return any(source.was_encrypted for source in snapshot.state.sources)


def _annotation_dict(annotation, assets):
    result = {key: value for key, value in asdict(annotation).items() if key != 'asset'}
    if annotation.asset is not None:
        assets[annotation.asset.sha256] = annotation.asset.data
        result['asset'] = annotation.asset.sha256
    else: result['asset'] = None
    return result


def encode_project(snapshot: ProjectSnapshot, *, password=None, cancel=None, progress=None):
    _check_cancel(cancel)
    if not isinstance(snapshot, ProjectSnapshot): raise _error('Der Arbeitsbereich ist ungültig.')
    # Validate the complete history before serializing anything.
    validator = Workspace(); validator.restore_state(snapshot.state)
    if snapshot_requires_password(snapshot) and password is None:
        raise ProjectPasswordRequired('Der Arbeitsbereich enthält entsperrte PDFs. Bitte schütze das Projekt mit einem Passwort.')
    assets, pdfs = {}, {}
    def page(value):
        return {'uid': value.uid, 'source_id': value.source_id, 'page_index': value.page_index,
                'rotation': value.rotation, 'annotations': [_annotation_dict(a, assets) for a in value.annotations]}
    sources = []
    for index, source in enumerate(snapshot.state.sources):
        _check_cancel(cancel)
        digest = hashlib.sha256(source.data).hexdigest(); pdfs[digest] = source.data
        sources.append({'source_id': source.source_id, 'path': str(source.path), 'path_flavour': os.name,
                        'data_sha256': digest, 'page_count': source.page_count,
                        'page_sizes': source.page_sizes, 'was_encrypted': source.was_encrypted,
                        'original_size': source.original_size,
                        'page_annotations': [[_annotation_dict(a, assets) for a in entries] for entries in source.page_annotations],
                        'bookmarks': [asdict(b) for b in source.bookmarks], 'bookmark_warnings': source.bookmark_warnings})
        if progress: progress(round(25*(index+1)/max(1,len(snapshot.state.sources))), 'Projektquellen vorbereiten …')
    state = snapshot.state
    manifest = {'format': 'pdf-workbench-project', 'schema_version': 1, 'saved_at_ns': time.time_ns(),
                'sources': sources, 'pages': [page(p) for p in state.pages],
                'bookmarks': [asdict(b) for b in state.bookmarks],
                'undo': [[page(p) for p in entries] for entries in state.undo],
                'redo': [[page(p) for p in entries] for entries in state.redo],
                'undo_bookmarks': [[asdict(b) for b in entries] for entries in state.undo_bookmarks],
                'redo_bookmarks': [[asdict(b) for b in entries] for entries in state.redo_bookmarks],
                'selected_ids': list(snapshot.selected_ids), 'active_uid': snapshot.active_uid,
                'zoom_mode': snapshot.zoom_mode, 'zoom_factor': snapshot.zoom_factor,
                'project_path': snapshot.project_path,
                'assets': {sha: {'filename': f'assets/{sha}.png', 'size': len(data)} for sha, data in assets.items()}}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(manifest_bytes) > MAX_MANIFEST_BYTES or sum(map(len, pdfs.values())) + sum(map(len, assets.values())) > MAX_UNPACKED_BYTES:
        raise _error('Dieses Projekt ist zu groß. Bitte teile den Arbeitsbereich auf.')
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr('manifest.json', manifest_bytes, compress_type=zipfile.ZIP_DEFLATED)
        members = [(f'sources/{sha}.pdf', data) for sha, data in pdfs.items()] + [(f'assets/{sha}.png', data) for sha, data in assets.items()]
        if len(members) + 1 > MAX_MEMBERS: raise _error('Das Projekt enthält zu viele Dateien.')
        for index, (name, data) in enumerate(members):
            _check_cancel(cancel); archive.writestr(name, data)
            if progress: progress(25+round(60*(index+1)/max(1,len(members))), 'Projektinhalte sichern …')
    data = output.getvalue(); _check_cancel(cancel)
    if password is not None:
        salt, nonce = os.urandom(16), os.urandom(12)
        header = ENCRYPTED_MAGIC + salt + nonce
        data = header + AESGCM(_password_key(password, salt)).encrypt(nonce, data, header)
    if len(data) > MAX_PROJECT_BYTES: raise _error('Eine Projektdatei darf höchstens 1 GiB groß sein.')
    _check_cancel(cancel)
    return data


def decode_project(data: bytes, *, password=None, cancel=None):
    _check_cancel(cancel)
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_PROJECT_BYTES: raise _error()
    encrypted = data.startswith(ENCRYPTED_MAGIC)
    if encrypted:
        if password is None: raise ProjectPasswordRequired('Dieses Projekt ist verschlüsselt. Bitte gib sein Projektpasswort ein.')
        end = len(ENCRYPTED_MAGIC)+28
        if len(data) < end+16: raise _error()
        header = data[:end]; salt = data[len(ENCRYPTED_MAGIC):len(ENCRYPTED_MAGIC)+16]; nonce = data[len(ENCRYPTED_MAGIC)+16:end]
        try: data = AESGCM(_password_key(password, salt)).decrypt(nonce, data[end:], header)
        except InvalidTag: raise IncorrectProjectPassword('Das Projektpasswort ist falsch oder die verschlüsselte Datei wurde verändert.') from None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist(); names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or not 0 < len(entries) <= MAX_MEMBERS or 'manifest.json' not in names: raise _error()
            if sum(entry.file_size for entry in entries) > MAX_UNPACKED_BYTES: raise _error('Die entpackten Projektinhalte sind zu groß.')
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (str(path) != entry.filename or path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                        or entry.is_dir() or stat.S_ISLNK(mode) or entry.flag_bits & 1
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    raise _error('Das Projekt enthält unzulässige Archivpfade oder Einträge.')
            if archive.getinfo('manifest.json').file_size > MAX_MANIFEST_BYTES: raise _error()
            manifest = json.loads(archive.read('manifest.json'), object_pairs_hook=_json_unique,
                                  parse_constant=lambda value: (_ for _ in ()).throw(_error()))
            if manifest.get('format') != 'pdf-workbench-project' or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1: raise _error()
            expected = {'manifest.json'}; assets = {}
            for digest, descriptor in manifest['assets'].items():
                if not re.fullmatch(r'[a-f0-9]{64}', digest) or descriptor['filename'] != f'assets/{digest}.png': raise _error()
                name = descriptor['filename']; expected.add(name)
                if archive.getinfo(name).file_size != descriptor['size']: raise _error()
                payload = archive.read(name)
                if hashlib.sha256(payload).hexdigest() != digest: raise _error('Ein Projektbild stimmt nicht mit seiner Prüfsumme überein.')
                assets[digest] = restore_image_asset(payload)
            def annotation(value):
                value = dict(value); asset = value.pop('asset')
                value['asset'] = assets[asset] if asset is not None else None
                value['style'] = TextStyle(**value['style'])
                return PageAnnotation(**value)
            def page(value):
                _identifier(value['uid']); _identifier(value['source_id'])
                _integer(value['page_index'], 0, 100_000); _integer(value['rotation'], 0, 270)
                if value['rotation'] % 90: raise _error()
                return PageRef(value['uid'], value['source_id'], value['page_index'], value['rotation'], tuple(annotation(a) for a in value['annotations']))
            sources = []; ids = set(); pdf_cache = {}
            for descriptor in manifest['sources']:
                _check_cancel(cancel)
                sid = _identifier(descriptor['source_id'])
                if sid in ids: raise _error();
                ids.add(sid); digest = descriptor['data_sha256']
                if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest): raise _error()
                name = f'sources/{digest}.pdf'; expected.add(name)
                if digest not in pdf_cache:
                    payload = archive.read(name)
                    if hashlib.sha256(payload).hexdigest() != digest: raise _error('Eine Projektquelle stimmt nicht mit ihrer Prüfsumme überein.')
                    reader = PdfReader(io.BytesIO(payload))
                    if reader.is_encrypted or not 0 < len(reader.pages) <= 100_000: raise _error()
                    validate_forms(reader)
                    if reader.trailer['/Root'].get('/Perms'): raise _error('Signierte Dokumente werden in Projekten noch nicht unterstützt.')
                    sizes = tuple((float(p.mediabox.width), float(p.mediabox.height)) for p in reader.pages)
                    if any(not math.isfinite(v) or v <= 0 for pair in sizes for v in pair): raise _error()
                    pdf_cache[digest] = payload, sizes
                payload, sizes = pdf_cache[digest]
                if _integer(descriptor['page_count'], 1, 100_000) != len(sizes) or tuple(tuple(pair) for pair in descriptor['page_sizes']) != sizes: raise _error()
                path = _text(descriptor['path'])
                if descriptor.get('path_flavour') != os.name:
                    label = (PureWindowsPath(path).name if descriptor.get('path_flavour') == 'nt' else PurePosixPath(path).name)
                    path = str(Path.home() / '.pdf-workbench-foreign-sources' / label)
                if type(descriptor['was_encrypted']) is not bool: raise _error()
                sources.append(SourceDocument(sid, Path(path), payload, len(sizes), sizes,
                    tuple(tuple(annotation(a) for a in entries) for entries in descriptor['page_annotations']),
                    descriptor['was_encrypted'], _integer(descriptor['original_size'], 0, MAX_UNPACKED_BYTES),
                    _bookmarks(descriptor['bookmarks'], source=True), tuple(_text(w, 5000) for w in descriptor['bookmark_warnings'])))
            if set(names) != expected: raise _error('Das Projekt enthält unerwartete oder fehlende Dateien.')
            if not encrypted and any(source.was_encrypted for source in sources):
                raise _error('Ein Projekt mit entsperrten PDFs muss verschlüsselt sein.')
            undo = tuple(tuple(page(p) for p in entries) for entries in manifest['undo'])
            redo = tuple(tuple(page(p) for p in entries) for entries in manifest['redo'])
            state = WorkspaceState(tuple(sources), tuple(page(p) for p in manifest['pages']), _bookmarks(manifest['bookmarks']),
                                   undo, redo, tuple(_bookmarks(entries) for entries in manifest['undo_bookmarks']),
                                   tuple(_bookmarks(entries) for entries in manifest['redo_bookmarks']))
            workspace = Workspace(); workspace.restore_state(state)
            current_ids = {p.uid for p in state.pages}
            selected = tuple(_identifier(uid) for uid in manifest['selected_ids'])
            active = manifest['active_uid']
            if not set(selected) <= current_ids or (active is not None and active not in current_ids): raise _error()
            mode, factor = manifest['zoom_mode'], manifest['zoom_factor']
            if mode not in ('page', 'width', 'custom') or type(factor) not in (float, int) or not math.isfinite(factor) or not 0 < factor <= 1000: raise _error()
            project_path = manifest.get('project_path')
            if project_path is not None: _text(project_path)
            _check_cancel(cancel)
            return ProjectSnapshot(state, selected, active, mode, float(factor), project_path)
    except (PdfWorkbenchError, OperationCancelled): raise
    except Exception:
        # Parsing/library exceptions can contain arbitrary input strings.
        raise _error() from None


def _regular_bytes(path):
    if path.is_symlink(): raise _error('Projektverknüpfungen werden zum Schutz vorhandener Dateien nicht verwendet.')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_PROJECT_BYTES: raise _error()
            data = stream.read(MAX_PROJECT_BYTES+1); after = os.fstat(stream.fileno())
            if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or len(data) != before.st_size: raise _error('Die Projektdatei wurde beim Lesen verändert. Bitte erneut öffnen.')
            return data, ProjectIdentity(before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size, hashlib.sha256(data).hexdigest())
    except BaseException:
        # fdopen owns descriptor after its successful construction.
        raise


def read_project(path, *, password=None, cancel=None):
    path = Path(path).expanduser().absolute(); actual = path
    recovered = None
    if not path.exists() and not path.is_symlink():
        candidates = sorted(path.parent.glob(f'.{path.name}.previous-*'), key=lambda p:p.stat().st_mtime_ns, reverse=True)
        if candidates: actual = candidates[0]; recovered = actual
    try:
        data, identity = _regular_bytes(actual); _check_cancel(cancel)
        snapshot = decode_project(data, password=password, cancel=cancel)
        return LoadedProject(snapshot, path, None if recovered else identity, data.startswith(ENCRYPTED_MAGIC), recovered)
    except OSError as error:
        raise _error(f'Das Projekt konnte nicht gelesen werden: {error}') from error


def _same_identity(path, expected):
    try:
        _, actual = _regular_bytes(path)
        return actual == expected
    except (OSError, PdfWorkbenchError): return False


def _commit_new(temporary, target):
    try:
        info = temporary.stat()
        os.link(temporary, target)
        return info.st_dev, info.st_ino
    except OSError as error:
        if error.errno not in (errno.EPERM,errno.EACCES,errno.ENOTSUP,errno.EOPNOTSUPP,errno.EXDEV): raise
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0), 0o600)
        identity = None
        written_size = 0; written_digest = hashlib.sha256()
        try:
            info = os.fstat(descriptor); identity = info.st_dev,info.st_ino
            with os.fdopen(descriptor,'wb') as output, temporary.open('rb') as source:
                for chunk in iter(lambda:source.read(1024**2),b''):
                    count = output.write(chunk)
                    written_size += count; written_digest.update(chunk[:count])
                output.flush();os.fsync(output.fileno())
                info = os.fstat(output.fileno())
                return info.st_dev, info.st_ino
        except BaseException as failure:
            from .core import _discard_partial
            cleanup = _discard_partial(target, identity, expected_size=written_size,
                                       expected_sha256=written_digest.hexdigest())
            if cleanup and isinstance(failure, OSError):
                raise OSError(failure.errno, str(failure) + cleanup) from failure
            raise


def write_project(snapshot, path, *, password=None, expected=None, cancel=None, progress=None):
    path = Path(path).expanduser().absolute()
    if path.suffix.lower() != '.pdfwb': raise _error('Bitte verwende für Projekte die Dateiendung .pdfwb.')
    if any(path.resolve() == source.path.resolve() for source in snapshot.state.sources): raise _error('Eine Originaldatei darf nicht durch ein Projekt ersetzt werden.')
    if expected is None and os.path.lexists(path): raise _error('Die Projektdatei existiert bereits. Bitte wähle einen neuen Namen.')
    if expected is not None and not _same_identity(path, expected): raise _error('Die Projektdatei wurde außerhalb dieses Fensters verändert. Bitte speichere unter einem neuen Namen.')
    data = encode_project(snapshot,password=password,cancel=cancel,progress=progress)
    temporary = None; previous = None; lock = None
    try:
        if expected is not None:lock = _SessionLock(path.with_name('.'+path.name+'.saving'))
        descriptor,name = tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent);temporary=Path(name)
        with os.fdopen(descriptor,'wb') as stream:
            for start in range(0,len(data),1024**2): _check_cancel(cancel);stream.write(data[start:start+1024**2])
            stream.flush();os.fsync(stream.fileno())
        _check_cancel(cancel)
        if expected is not None:
            if not _same_identity(path,expected): raise _error('Das Projekt wurde inzwischen verändert. Bitte unter neuem Namen speichern.')
            previous = path.with_name('.'+path.name+'.previous-'+uuid4().hex)
            os.rename(path,previous)
            if not _same_identity(previous,expected): raise _error('Die Projektdatei wurde während des Speicherns ersetzt. Die verschobene Datei bleibt als Sicherung erhalten.')
        committed_inode = _commit_new(temporary,path)
        _, identity = _regular_bytes(path)
        if ((identity.device, identity.inode) != committed_inode or
                identity.size != len(data) or identity.sha256 != hashlib.sha256(data).hexdigest()):
            backup_note = f' Die vorige Version bleibt erhalten: {previous}' if previous else ''
            raise _error('Das Projektziel wurde während des Speicherns ersetzt. Bitte unter neuem Namen speichern.' + backup_note)
        if previous is not None and _same_identity(previous,expected): previous.unlink();previous=None
        try:
            if os.name != 'nt':
                directory_fd=os.open(path.parent,os.O_RDONLY);os.fsync(directory_fd);os.close(directory_fd)
        except OSError: pass
        if progress:progress(100,'Projekt gespeichert.')
        return identity
    except (PdfWorkbenchError,OperationCancelled):raise
    except OSError as error:raise _error(f'Das Projekt konnte nicht gespeichert werden: {error}') from error
    finally:
        if previous is not None and previous.exists() and not os.path.lexists(path):
            try:_commit_new(previous,path)
            except OSError:pass
        if temporary is not None:temporary.unlink(missing_ok=True)
        if lock is not None:lock.close()


class _SessionLock:
    def __init__(self,path):
        self.stream=None
        if path.is_symlink():raise _error('Der Wiederherstellungsordner enthält eine unzulässige Verknüpfung.')
        descriptor=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        self.stream=os.fdopen(descriptor,'r+b',buffering=0)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):raise _error('Die Sperrdatei ist ungültig.')
            if os.fstat(descriptor).st_size==0:self.stream.write(b'1')
            self.stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(descriptor,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:self.stream.close();self.stream=None;raise

    def close(self):
        if self.stream is not None:self.stream.close();self.stream=None


class RecoveryStore:
    def __init__(self,directory,*,session_id=None):
        self.directory=Path(directory).expanduser().absolute()
        if self.directory.is_symlink():raise _error('Der Wiederherstellungsordner darf keine Verknüpfung sein.')
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.session_id=session_id or uuid4().hex
        if not re.fullmatch(r'[a-f0-9]{32}',self.session_id):raise _error()
        self.lock=_SessionLock(self.directory/(self.session_id+'.lock'))
        self.closed=False

    def save(self,snapshot,*,password=None,cancel=None,progress=None):
        if self.closed:raise _error('Die Wiederherstellungssitzung ist bereits geschlossen.')
        path=self.directory/f'{self.session_id}-{time.time_ns()}-{uuid4().hex}.pdfwb'
        write_project(snapshot,path,password=password,cancel=cancel,progress=progress)
        entries=sorted(self.directory.glob(self.session_id+'-*.pdfwb'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
        for old in entries[2:]:
            if not old.is_symlink() and RECOVERY_NAME.fullmatch(old.name):old.unlink(missing_ok=True)
        return path

    def discard(self):
        for path in self.directory.glob(self.session_id+'-*.pdfwb'):
            if RECOVERY_NAME.fullmatch(path.name) and not path.is_symlink():path.unlink(missing_ok=True)

    def close(self,*,discard=False):
        try:
            if discard:self.discard()
        finally:
            if not self.closed:self.closed=True;self.lock.close()


def list_recoveries(directory):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir():return ()
    result=[];active=set();checked=set()
    for path in sorted(directory.glob('*.pdfwb'),reverse=True):
        match=RECOVERY_NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():continue
        sid=match[1]
        if sid not in checked:
            checked.add(sid)
            try:lock=_SessionLock(directory/(sid+'.lock'));lock.close()
            except OSError:active.add(sid)
        if sid in active:continue
        try:
            with path.open('rb') as stream:encrypted=stream.read(len(ENCRYPTED_MAGIC))==ENCRYPTED_MAGIC
            result.append(RecoveryEntry(path,sid,path.stat().st_mtime_ns,encrypted))
        except OSError:continue
    # Keep the preceding generation selectable if the newest file was damaged.
    return tuple(sorted(result,key=lambda e:e.modified_ns,reverse=True))


def discard_recovery(directory, session_id):
    """Remove only an inactive app-owned session; never touch live sessions."""
    directory=Path(directory)
    if directory.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}',session_id):raise _error()
    lock=_SessionLock(directory/(session_id+'.lock'))
    try:
        for path in directory.glob(session_id+'-*.pdfwb'):
            if RECOVERY_NAME.fullmatch(path.name) and not path.is_symlink():path.unlink(missing_ok=True)
    finally:lock.close()
