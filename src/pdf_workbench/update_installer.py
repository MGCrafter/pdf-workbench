"""Staged portable-bundle replacement, using a separate frozen helper process.

Only manifest-owned application files are replaced. Other files are copied
after the old process exits, and the complete old folder remains as a backup.
No shell, elevated privilege or execution-policy workaround is involved.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
from uuid import uuid4
import zipfile

from .updates import DownloadedUpdate, UpdateError, _cancel, native_platform, version_tuple

MANIFEST = "BUNDLE-MANIFEST.json"
MAX_FILES = 20_000
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_FILE = 512 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024


def _link(path):
    return path.is_symlink() or (hasattr(path,"is_junction") and path.is_junction())


def _relative(name):
    if (not isinstance(name,str) or not name or "\\" in name or "\x00" in name or name.startswith("/")
            or unicodedata.normalize("NFC",name) != name): raise UpdateError("Das Paket enthält einen unsicheren Dateipfad.")
    parts = name.rstrip("/").split("/")
    for part in parts:
        if (part in ("",".","..") or part[-1:] in (" ",".") or re.search(r'[<>:"|?*\x00-\x1f]',part)
                or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?",part)):
            raise UpdateError("Das Paket enthält einen nicht portablen Dateipfad.")
    return "/".join(parts)


def _digest(path,cancel=None):
    value=hashlib.sha256()
    with path.open("rb") as stream:
        while data:=stream.read(1024*1024): _cancel(cancel); value.update(data)
    return value.hexdigest()


def _json(path,limit=MAX_MANIFEST):
    if _link(path) or not path.is_file() or path.stat().st_size>limit: raise UpdateError("Der Update-Nachweis fehlt oder ist zu groß.")
    try: return json.loads(path.read_bytes())
    except (ValueError,UnicodeError): raise UpdateError("Der Update-Nachweis ist nicht lesbar.") from None


def _write_json(path,value,*,exclusive=False):
    with path.open("x" if exclusive else "w",encoding="utf-8") as stream:
        json.dump(value,stream,indent=2,ensure_ascii=False);stream.write("\n");stream.flush();os.fsync(stream.fileno())
    if os.name!="nt":path.chmod(0o600)


def write_bundle_manifest(bundle,app_version=None):
    """Call after all packaging mutations, before archiving the bundle.

    The manifest excludes itself. Links are an explicit build error, never an
    omitted file. Ordinary runtime files and release/build metadata are covered.
    """
    root=Path(bundle).resolve()
    if app_version is None:app_version=_json(root/"_internal/APP_PROVENANCE.json")["app_version"]
    version_tuple(app_version);records={};spellings={}
    for path in sorted(root.rglob("*")):
        if _link(path):raise UpdateError(f"Updatefähige Bundles erlauben keine Dateilinks: {path.relative_to(root)}")
        if path.is_dir():continue
        if not stat.S_ISREG(path.stat().st_mode):raise UpdateError("Das Bundle enthält eine spezielle Datei.")
        name=_relative(path.relative_to(root).as_posix())
        if name==MANIFEST:continue
        if name.casefold() in spellings:raise UpdateError("Das Bundle enthält mehrdeutige Dateinamen.")
        spellings[name.casefold()]=name
        records[name]={"size":path.stat().st_size,"sha256":_digest(path)}
    if not records or len(records)>MAX_FILES:raise UpdateError("Das Bundle hat eine ungültige Dateianzahl.")
    manifest={"schema":1,"app_version":app_version,"files":records}
    _write_json(root/MANIFEST,manifest)
    return manifest


def validate_bundle(bundle,*,expected_version=None,allow_foreign=False,cancel=None):
    root=Path(bundle)
    if _link(root) or not root.is_dir():raise UpdateError("Der Programmordner ist kein gewöhnlicher Ordner.")
    manifest=_json(root/MANIFEST)
    if (not isinstance(manifest,dict) or manifest.get("schema")!=1 or not isinstance(manifest.get("files"),dict)
            or not 1<=len(manifest["files"])<=MAX_FILES):raise UpdateError("Der Programmordner besitzt keinen gültigen Dateinachweis für Updates.")
    version_tuple(manifest.get("app_version"))
    if expected_version is not None and manifest["app_version"]!=expected_version:raise UpdateError("Paket und angekündigte Updateversion stimmen nicht überein.")
    records=manifest["files"];canonical=set();total=0
    for name,record in records.items():
        _cancel(cancel);_relative(name)
        if name==MANIFEST or name.casefold() in canonical:raise UpdateError("Der Dateinachweis enthält mehrdeutige Pfade.")
        canonical.add(name.casefold())
        if (not isinstance(record,dict) or type(record.get("size")) is not int or not 0<=record["size"]<=MAX_FILE
                or not isinstance(record.get("sha256"),str) or not re.fullmatch(r"[a-f0-9]{64}",record["sha256"])):
            raise UpdateError("Der Dateinachweis enthält ungültige Dateiangaben.")
        total+=record["size"]
        if total>MAX_EXPANDED:raise UpdateError("Die Programmdaten sind zu groß für ein portables Update.")
        path=root.joinpath(*name.split("/"))
        if (any(_link(parent) for parent in (path,*path.parents) if parent!=root.parent)
                or not path.is_file() or path.stat().st_size!=record["size"] or _digest(path,cancel)!=record["sha256"]):
            raise UpdateError(f"Die Programmdatei {name} fehlt oder wurde verändert. Bitte das Programm neu in einen eigenen Ordner entpacken.")
    actual=set()
    for path in root.rglob("*"):
        _cancel(cancel)
        if _link(path):raise UpdateError("Dateilinks im Programmordner verhindern ein sicheres portables Update.")
        if path.is_file():actual.add(path.relative_to(root).as_posix())
        elif not path.is_dir():raise UpdateError("Der Programmordner enthält eine spezielle Datei.")
    if not allow_foreign and actual!=set(records)|{MANIFEST}:raise UpdateError("Das neue Bundle enthält Dateien außerhalb seines Dateinachweises.")
    return manifest


def _copy_member(stream,target,size,cancel):
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("xb") as output:
        remaining=size
        while remaining:
            _cancel(cancel);data=stream.read(min(128*1024,remaining))
            if not data:raise UpdateError("Das Updatearchiv ist unvollständig.")
            output.write(data);remaining-=len(data)
        if stream.read(1):raise UpdateError("Eine Archivdatei ist größer als angekündigt.")


def extract_update(downloaded:DownloadedUpdate,cancel=None):
    _cancel(cancel)
    release,archive=downloaded.release,Path(downloaded.archive)
    if (_link(archive) or archive.name!=release.asset.name or not archive.is_file() or archive.stat().st_size!=release.asset.size
            or _digest(archive,cancel)!=release.asset.sha256):raise UpdateError("Das heruntergeladene Paket wurde nach der Prüfung verändert.")
    root=Path(tempfile.mkdtemp(prefix="unpacked-",dir=archive.parent));kinds={};spellings={};entries=set();total=0;count=0
    def member(name,is_dir,size):
        nonlocal total,count
        _cancel(cancel);name=_relative(name);count+=1
        if count>MAX_FILES or type(size) is not int or not 0<=size<=MAX_FILE:raise UpdateError("Das Archiv überschreitet die erlaubten Dateigrenzen.")
        if not name.startswith("PDFWorkbench/") and name!="PDFWorkbench":raise UpdateError("Das Archiv besitzt keinen eindeutigen PDFWorkbench-Programmordner.")
        if name=="PDFWorkbench" and not is_dir:raise UpdateError("Der Programmordner ist keine Verzeichnisdatei.")
        key=name.casefold()
        if key in entries:raise UpdateError("Das Archiv enthält doppelte Dateipfade.")
        entries.add(key);parts=name.split("/")
        for index in range(1,len(parts)+1):
            spelling="/".join(parts[:index]);prefix=spelling.casefold();kind="dir" if index<len(parts) or is_dir else "file"
            if prefix in kinds and (kinds[prefix]!=kind or spellings[prefix]!=spelling):raise UpdateError("Das Archiv enthält kollidierende Dateipfade.")
            kinds[prefix]=kind;spellings[prefix]=spelling
        total+=size
        if total>MAX_EXPANDED:raise UpdateError("Das entpackte Update überschreitet die erlaubte Größe.")
        return root.joinpath(*parts)
    try:
        if release.platform=="windows":
            with zipfile.ZipFile(archive) as stream:
                for info in stream.infolist():
                    mode=info.external_attr>>16;kind=stat.S_IFMT(mode)
                    if info.flag_bits&1 or kind not in (0,stat.S_IFREG,stat.S_IFDIR):raise UpdateError("Verschlüsselte Archivdateien und Links werden nicht entpackt.")
                    target=member(info.filename,info.is_dir(),info.file_size)
                    if info.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                    if info.file_size>max(1,info.compress_size)*300:raise UpdateError("Die Archiv-Kompressionsrate ist ungewöhnlich hoch.")
                    with stream.open(info) as data:_copy_member(data,target,info.file_size,cancel)
        elif release.platform=="linux":
            with tarfile.open(archive,"r:gz") as stream:
                for info in stream:
                    if not (info.isdir() or info.isreg()) or getattr(info,"sparse",None):raise UpdateError("Links, Geräte und Sparse-Dateien werden nicht entpackt.")
                    target=member(info.name,info.isdir(),info.size)
                    if info.isdir():target.mkdir(parents=True,exist_ok=True);continue
                    data=stream.extractfile(info)
                    if data is None:raise UpdateError("Eine Archivdatei konnte nicht gelesen werden.")
                    with data:_copy_member(data,target,info.size,cancel)
                    target.chmod(info.mode&0o755)
        else:raise UpdateError("Dieses Paket gehört zu keiner unterstützten Plattform.")
        bundle=root/"PDFWorkbench";validate_bundle(bundle,expected_version=release.version,cancel=cancel)
        return bundle
    except BaseException:
        shutil.rmtree(root);raise


@dataclass(frozen=True)
class InstallPlan:
    plan_path: str
    token: str
    install_dir: str
    candidate_dir: str
    helper_dir: str
    backup_dir: str
    old_manifest_sha256: str
    old_version: str
    new_version: str
    platform: str
    restart_args: tuple[str,...]=()
    old_pid: int=0

    @property
    def executable_name(self):return "PDFWorkbench.exe" if self.platform=="windows" else "PDFWorkbench"

    @property
    def status_path(self):return Path(self.plan_path).with_name("install-status.json")

    @property
    def ready_path(self):return Path(self.plan_path).with_name("gui-started.json")


def _status(plan,state,message=""):
    _write_json(plan.status_path,{"token":plan.token,"state":state,"message":message,"time":time.time()})


def _foreign(old,new,manifest,*,copy=False,cancel=None):
    owned=set(manifest["files"])|{MANIFEST}
    new_names={p.relative_to(new).as_posix().casefold():p for p in new.rglob("*")}
    for path in old.rglob("*"):
        _cancel(cancel)
        name=path.relative_to(old).as_posix();_relative(name)
        if _link(path):raise UpdateError("Ein Dateilink bei eigenen Dateien verhindert das sichere Update.")
        if name in owned:continue
        destination=new.joinpath(*name.split("/"));collision=new_names.get(name.casefold())
        if path.is_dir():
            if collision is not None and not collision.is_dir():raise UpdateError(f"Eine eigene Datei kollidiert mit dem Update: {name}")
            if copy:destination.mkdir(parents=True,exist_ok=True)
            continue
        if not path.is_file():raise UpdateError("Eigene Spezialdateien verhindern das sichere Update.")
        if collision is not None or any(part.exists() and not part.is_dir() for part in destination.parents if part!=new.parent):
            raise UpdateError(f"Eine eigene Datei würde vom Update ersetzt: {name}. Bitte sie zuvor außerhalb des Programmordners speichern.")
        if copy:
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)


def _environment():
    environment=os.environ.copy();environment["PYINSTALLER_RESET_ENVIRONMENT"]="1"
    return environment


def prepare_install(downloaded,install_dir=None,*,restart_args=(),cancel=None):
    if not getattr(sys,"frozen",False):raise UpdateError("Ein Quellcode-Checkout wird nicht aktualisiert. Bitte das neue portable Programmpaket verwenden.")
    target=Path(sys.executable).resolve().parent if install_dir is None else Path(install_dir).absolute()
    if _link(target) or target.resolve()!=Path(sys.executable).resolve().parent:raise UpdateError("Nur der tatsächlich gestartete portable Programmordner kann aktualisiert werden.")
    platform_name=native_platform()
    if downloaded.release.platform!=platform_name:raise UpdateError("Das Update passt nicht zum laufenden Programm.")
    old=validate_bundle(target,allow_foreign=True,cancel=cancel)
    from . import __version__
    if old["app_version"]!=__version__:raise UpdateError("Der Dateinachweis gehört nicht zur laufenden Programmversion.")
    if version_tuple(downloaded.release.version)<=version_tuple(old["app_version"]):raise UpdateError("Ein Downgrade oder dieselbe Version wird nicht installiert.")
    args=tuple(restart_args)
    if len(args)>16 or any(not isinstance(arg,str) or "\x00" in arg or len(arg)>8192 for arg in args):raise UpdateError("Die Neustartargumente sind ungültig.")
    helper=extract_update(downloaded,cancel)
    candidate=None
    try:
        _foreign(target,helper,old,cancel=cancel)
        required=sum(p.stat().st_size for p in helper.rglob("*") if p.is_file())+sum(p.stat().st_size for p in target.rglob("*") if p.is_file())+32*1024*1024
        if shutil.disk_usage(target.parent).free<required:raise UpdateError("Für Update und Sicherung ist nicht genügend freier Speicher vorhanden.")
        candidate=Path(tempfile.mkdtemp(prefix=f".{target.name}.update-",dir=target.parent))
        shutil.copytree(helper,candidate,dirs_exist_ok=True)
        token=uuid4().hex+uuid4().hex
        plan=InstallPlan(str(helper.parent/"install-plan.json"),token,str(target),str(candidate),str(helper),
                         str(target.with_name(f".{target.name}.backup-{uuid4().hex}")),_digest(target/MANIFEST),
                         old["app_version"],downloaded.release.version,platform_name,args)
        _write_json(Path(plan.plan_path),asdict(plan),exclusive=True)
        _status(plan,"prepared")
        _cancel(cancel)
        executable=candidate/plan.executable_name
        if not executable.is_file() or (platform_name=="linux" and not os.access(executable,os.X_OK)):raise UpdateError("Im Update fehlt das ausführbare Programm.")
        with (helper.parent/"probe.log").open("wb") as log:
            process=subprocess.Popen([str(executable),"--update-probe",plan.plan_path,plan.token],cwd=helper.parent,env=_environment(),stdout=log,stderr=log)
            deadline=time.monotonic()+45
            try:
                while process.poll() is None:
                    _cancel(cancel)
                    if time.monotonic()>deadline:raise UpdateError("Die neue Version konnte nicht rechtzeitig gestartet werden.")
                    time.sleep(.1)
            except BaseException:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                raise
        _cancel(cancel)
        if process.returncode!=0:raise UpdateError("Das neue Programm konnte vor dem Austausch nicht gestartet werden.")
        receipt=_json(helper.parent/"probe-ready.json",4096)
        if process.returncode!=0 or receipt.get("token")!=token or receipt.get("version")!=plan.new_version:raise UpdateError("Das neue Programm konnte vor dem Austausch nicht gestartet werden.")
        return plan
    except BaseException:
        if candidate is not None:shutil.rmtree(candidate)
        shutil.rmtree(helper.parent)
        raise


def discard_install(plan):
    """Release a prepared candidate on Cancel; retain the verified download."""
    plan=load_plan(plan.plan_path,plan.token)
    if _json(plan.status_path,4096).get("state")!="prepared":raise UpdateError("Ein bereits gestarteter Updateauftrag wird nicht gelöscht.")
    candidate=Path(plan.candidate_dir)
    if candidate.exists():shutil.rmtree(candidate)
    shutil.rmtree(Path(plan.helper_dir).parent)


def load_plan(path,token):
    path=Path(path).absolute();data=_json(path,64*1024)
    try:
        data["restart_args"]=tuple(data.get("restart_args",()))
        plan=InstallPlan(**data)
        if (plan.plan_path!=str(path) or not re.fullmatch(r"[a-f0-9]{64}",token) or plan.token!=token
                or plan.platform not in ("windows","linux") or version_tuple(plan.new_version)<=version_tuple(plan.old_version)
                or not re.fullmatch(r"[a-f0-9]{64}",plan.old_manifest_sha256)):raise ValueError
        target,candidate,backup,helper=(Path(value) for value in (plan.install_dir,plan.candidate_dir,plan.backup_dir,plan.helper_dir))
        if (any(not item.is_absolute() or _link(item) for item in (target,candidate,backup,helper))
                or candidate.parent!=target.parent or backup.parent!=target.parent
                or not candidate.name.startswith(f".{target.name}.update-") or not backup.name.startswith(f".{target.name}.backup-")
                or helper.parent!=path.parent or helper.is_relative_to(target) or path.is_relative_to(target)
                or len({target,candidate,backup,helper})!=4):raise ValueError
        return plan
    except (TypeError,ValueError,KeyError):raise UpdateError("Der Installationsauftrag ist ungültig oder wurde verändert.") from None


def launch_install(plan,old_pid=None):
    plan=replace(plan,old_pid=os.getpid() if old_pid is None else old_pid)
    if type(plan.old_pid) is not int or plan.old_pid<=0:raise UpdateError("Der alte Programmprozess ist ungültig.")
    _write_json(Path(plan.plan_path),asdict(plan));load_plan(plan.plan_path,plan.token)
    kwargs={"cwd":str(Path(plan.plan_path).parent),"env":_environment(),"stdin":subprocess.DEVNULL,"stdout":subprocess.DEVNULL,"stderr":subprocess.DEVNULL,"close_fds":True}
    if os.name=="nt":kwargs["creationflags"]=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP
    else:kwargs["start_new_session"]=True
    if _json(plan.status_path,4096).get("state")!="prepared":raise UpdateError("Dieser Updateauftrag wurde bereits gestartet.")
    _status(plan,"launched")
    try:return subprocess.Popen([str(Path(plan.helper_dir)/plan.executable_name),"--apply-update",plan.plan_path,plan.token],**kwargs)
    except BaseException:
        _status(plan,"prepared","Der Update-Helfer konnte nicht gestartet werden.");raise


def _wait_old(plan,timeout=90):
    if type(plan.old_pid) is not int or plan.old_pid<=0 or plan.old_pid==os.getpid():raise UpdateError("Es kann nicht auf den angegebenen alten Prozess gewartet werden.")
    expected=(Path(plan.install_dir)/plan.executable_name).resolve()
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD);kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes=(wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
        kernel.WaitForSingleObject.argtypes=(wintypes.HANDLE,wintypes.DWORD);kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
        handle=kernel.OpenProcess(0x100000|0x1000,False,plan.old_pid)
        if not handle:
            if ctypes.get_last_error()==87:return
            raise UpdateError("Der alte Programmprozess konnte nicht geprüft werden.")
        try:
            buffer=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)) or Path(buffer.value).resolve()!=expected:raise UpdateError("Die Prozesskennung gehört nicht zur alten Programmkopie.")
            if kernel.WaitForSingleObject(handle,round(timeout*1000))!=0:raise UpdateError("Das alte Programm wurde nicht rechtzeitig beendet. Bitte andere Instanzen schließen.")
        finally:kernel.CloseHandle(handle)
    else:
        proc=Path(f"/proc/{plan.old_pid}/exe")
        try:
            if proc.resolve(strict=True)!=expected:raise UpdateError("Die Prozesskennung gehört nicht zur alten Programmkopie.")
        except FileNotFoundError:return
        deadline=time.monotonic()+timeout
        while proc.exists():
            if time.monotonic()>deadline:raise UpdateError("Das alte Programm wurde nicht rechtzeitig beendet.")
            time.sleep(.1)


def _start(plan,*,receipt=True):
    command=[str(Path(plan.install_dir)/plan.executable_name)]
    if receipt:command += ["--update-started",plan.plan_path,plan.token]
    command += list(plan.restart_args)
    return subprocess.Popen(command,cwd=plan.install_dir,env=_environment(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)


def apply_install(plan,*,wait_timeout=90,start_timeout=45):
    """Called by the detached helper. Keep backup even after a successful start."""
    target,candidate,backup=map(Path,(plan.install_dir,plan.candidate_dir,plan.backup_dir));moved=False;installed=False;child=None
    try:
        with Path(plan.plan_path).with_name("apply.lock").open("x",encoding="ascii") as lock:lock.write(plan.token)
    except FileExistsError:return False
    try:
        _status(plan,"waiting");_wait_old(plan,wait_timeout)
        if backup.exists() or _digest(target/MANIFEST)!=plan.old_manifest_sha256:raise UpdateError("Der alte Programmordner wurde während des Updates verändert.")
        old=validate_bundle(target,expected_version=plan.old_version,allow_foreign=True)
        validate_bundle(candidate,expected_version=plan.new_version)
        _foreign(target,candidate,old,copy=True)
        _status(plan,"installing")
        target.rename(backup);moved=True
        candidate.rename(target);installed=True
        _status(plan,"starting")
        child=_start(plan);deadline=time.monotonic()+start_timeout
        while time.monotonic()<deadline:
            if plan.ready_path.exists():
                ready=_json(plan.ready_path,4096)
                if ready.get("token")==plan.token and ready.get("version")==plan.new_version:
                    _status(plan,"installed",f"Version {plan.new_version} ist gestartet. Die vorige Programmkopie bleibt als Sicherung erhalten.");return True
            if child.poll() is not None:break
            time.sleep(.1)
        raise UpdateError("Die neue Version hat ihren erfolgreichen Start nicht bestätigt. Die vorige Version wird wiederhergestellt.")
    except BaseException as error:
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        try:
            if moved:
                if installed:target.rename(target.with_name(f".{target.name}.failed-{uuid4().hex}"))
                backup.rename(target)
                _status(plan,"rolled_back",str(error));_start(plan,receipt=False)
            else:_status(plan,"failed",str(error))
        except BaseException as rollback_error:
            _status(plan,"recovery_required",f"{error}\nDie Sicherung bleibt unter {backup}. Wiederherstellung: {rollback_error}")
        return False


def probe_update_cli(argv):
    if len(argv)!=2:return 2
    plan=load_plan(*argv)
    from . import __version__
    if (not getattr(sys,"frozen",False) or Path(sys.executable).resolve().parent!=Path(plan.candidate_dir).resolve()
            or __version__!=plan.new_version):raise UpdateError("Die Update-Probe gehört nicht zum vorbereiteten Programm.")
    validate_bundle(Path(sys.executable).parent,expected_version=plan.new_version)
    _write_json(Path(plan.plan_path).with_name("probe-ready.json"),{"token":plan.token,"version":__version__},exclusive=True)
    return 0


def mark_update_started(path,token):
    plan=load_plan(path,token)
    from . import __version__
    if (not getattr(sys,"frozen",False) or Path(sys.executable).resolve().parent!=Path(plan.install_dir).resolve()
            or __version__!=plan.new_version):raise UpdateError("Die Startbestätigung gehört nicht zum installierten Update.")
    _write_json(plan.ready_path,{"token":token,"version":__version__},exclusive=True)


def apply_update_cli(argv):
    if len(argv)!=2:return 2
    plan=load_plan(*argv)
    if not getattr(sys,"frozen",False) or Path(sys.executable).resolve().parent!=Path(plan.helper_dir).resolve():raise UpdateError("Der Update-Helfer muss außerhalb des installierten Programms laufen.")
    return 0 if apply_install(plan) else 1
