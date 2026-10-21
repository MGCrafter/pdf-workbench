"""Bounded HTTPS updates from this project's stable GitHub releases only."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .core import OperationCancelled, PdfWorkbenchError

REPOSITORY = "MGCrafter/pdf-workbench"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
CHECK_ENABLED_KEY = "updates/check_automatically"
LAST_CHECK_KEY = "updates/last_checked"
MAX_DOWNLOAD = 512 * 1024 * 1024
MAX_METADATA = 2 * 1024 * 1024
MAX_CHECKSUMS = 64 * 1024
ALLOWED_HOSTS = frozenset(("api.github.com", "github.com", "release-assets.githubusercontent.com",
                           "objects.githubusercontent.com", "github-releases.githubusercontent.com"))


class UpdateError(PdfWorkbenchError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"v?(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})", value):
        raise UpdateError("Die Versionsangabe ist keine unterstützte stabile Version.")
    return tuple(map(int, value.removeprefix("v").split(".")))


def native_platform() -> str:
    system = platform.system().lower()
    if system not in ("windows", "linux") or platform.machine().lower() not in ("x86_64", "amd64"):
        raise UpdateError("Automatische Updates unterstützen derzeit portable Windows- und Linux-Bundles für x86_64.")
    return system


def should_check(last_checked, now=None) -> bool:
    now = time.time() if now is None else now
    try:
        previous = float(last_checked)
        return not 0 < previous <= now + 3600 or now - previous >= CHECK_INTERVAL_SECONDS
    except (TypeError, ValueError, OverflowError):
        return True


def _cancel(cancel):
    if cancel and cancel(): raise OperationCancelled("Update abgebrochen. Das installierte Programm bleibt erhalten.")


def _https_url(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443) or parts.fragment):
            raise ValueError
    except (TypeError, ValueError):
        raise UpdateError("Das Update verweist auf eine nicht erlaubte Downloadadresse.") from None
    return url


class _Redirects(HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url):
    _https_url(url)
    request = Request(url, headers={"User-Agent": "PDFWorkbench-Updater", "Accept": "application/vnd.github+json" if url == API_URL else "application/octet-stream",
                                    "X-GitHub-Api-Version": "2022-11-28", "Accept-Encoding": "identity"})
    try:
        response = build_opener(_Redirects()).open(request, timeout=20)
        _https_url(response.geturl())
        if response.status != 200:
            response.close(); raise UpdateError("Der Updateserver lieferte keine vollständige Datei.")
        return response
    except HTTPError as error:
        if error.code == 403 or error.code == 429:
            raise UpdateError("GitHub begrenzt gerade Updateanfragen. Bitte später erneut versuchen.") from None
        raise UpdateError(f"Die Updateanfrage wurde vom Server abgelehnt (HTTP {error.code}).") from None
    except (URLError, TimeoutError, OSError):
        raise UpdateError("Der Updateserver ist gerade nicht erreichbar. Bitte die Internetverbindung prüfen und später erneut versuchen.") from None


def _read(url, limit, cancel=None):
    _cancel(cancel)
    with _open(url) as response:
        chunks, size = [], 0
        while True:
            _cancel(cancel)
            data = response.read(min(128 * 1024, limit + 1 - size))
            if not data: break
            chunks.append(data); size += len(data)
            if size > limit: raise UpdateError("Die Updateantwort überschreitet die erlaubte Größe.")
    _cancel(cancel)
    return b"".join(chunks)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    platform: str
    asset: ReleaseAsset
    checksums: ReleaseAsset
    release_url: str


@dataclass(frozen=True)
class DownloadedUpdate:
    release: UpdateRelease
    archive: Path


def _asset(metadata, name, version, limit):
    candidates = [asset for asset in metadata if isinstance(asset, dict) and asset.get("name") == name]
    if len(candidates) != 1: raise UpdateError("Das Release enthält kein eindeutiges, passendes Updatepaket.")
    asset = candidates[0]
    expected = f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"
    digest = asset.get("digest", "")
    if (asset.get("browser_download_url") != expected or type(asset.get("size")) is not int
            or not 0 < asset["size"] <= limit or asset.get("state") != "uploaded"
            or not isinstance(digest,str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)):
        raise UpdateError("Das Updatepaket hat keine vollständigen, vertrauenswürdigen Größen- und SHA-256-Angaben.")
    return ReleaseAsset(name, expected, asset["size"], digest[7:])


def release_from_metadata(metadata, current_version, platform_name=None):
    target = native_platform() if platform_name is None else platform_name
    if target not in ("windows", "linux"): raise UpdateError("Für diese Plattform gibt es kein passendes Updatepaket.")
    if not isinstance(metadata, dict) or metadata.get("draft") is not False or metadata.get("prerelease") is not False:
        raise UpdateError("Es wird nur eine veröffentlichte stabile Version installiert.")
    version = metadata.get("tag_name", "")
    if not isinstance(version,str) or not version.startswith("v"): raise UpdateError("Die Release-Version ist ungültig.")
    newer = version_tuple(version); current = version_tuple(current_version)
    if newer <= current: return None
    version = version[1:]
    url = f"{RELEASES_URL}/tag/v{version}"
    assets = metadata.get("assets")
    if metadata.get("html_url") != url or not isinstance(assets,list) or len(assets)>100:
        raise UpdateError("Die Release-Angaben gehören nicht zu diesem Programm.")
    suffix = ".zip" if target == "windows" else ".tar.gz"
    name = f"PDFWorkbench-v{version}-{target}-x86_64{suffix}"
    return UpdateRelease(version,target,_asset(assets,name,version,MAX_DOWNLOAD),
                         _asset(assets,"SHA256SUMS",version,MAX_CHECKSUMS),url)


def check_latest(current_version, platform_name=None, cancel=None):
    data = _read(API_URL,MAX_METADATA,cancel)
    try: metadata = json.loads(data)
    except (ValueError,UnicodeError): raise UpdateError("Die Release-Antwort konnte nicht gelesen werden.") from None
    return release_from_metadata(metadata,current_version,platform_name)


def _verify_checksums(data, release):
    if len(data) != release.checksums.size or hashlib.sha256(data).hexdigest() != release.checksums.sha256:
        raise UpdateError("Die Prüfsummendatei stimmt nicht mit den Release-Metadaten überein.")
    expected = {f"PDFWorkbench-v{release.version}-windows-x86_64.zip", f"PDFWorkbench-v{release.version}-linux-x86_64.tar.gz"}
    try:
        records = {}
        for line in data.decode("ascii").splitlines():
            match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9._-]+)",line)
            if not match or match[2] in records or match[2] not in expected: raise ValueError
            records[match[2]] = match[1]
        if set(records) != expected or records[release.asset.name] != release.asset.sha256: raise ValueError
    except (ValueError,UnicodeError,KeyError):
        raise UpdateError("SHA256SUMS und die Release-Metadaten widersprechen sich.") from None


def download_update(release: UpdateRelease, cache_dir, cancel=None, progress=None) -> DownloadedUpdate:
    if release.platform != native_platform(): raise UpdateError("Dieses Updatepaket passt nicht zum laufenden System.")
    version_tuple(release.version)
    expected_name=f"PDFWorkbench-v{release.version}-{release.platform}-x86_64"+(".zip" if release.platform=="windows" else ".tar.gz")
    metadata=[{"name":asset.name,"browser_download_url":asset.url,"size":asset.size,"digest":"sha256:"+asset.sha256,"state":"uploaded"} for asset in (release.asset,release.checksums)]
    if (_asset(metadata,expected_name,release.version,MAX_DOWNLOAD)!=release.asset
            or _asset(metadata,"SHA256SUMS",release.version,MAX_CHECKSUMS)!=release.checksums):
        raise UpdateError("Die Updateangaben gehören nicht zum erwarteten Release.")
    _verify_checksums(_read(release.checksums.url,MAX_CHECKSUMS,cancel),release)
    cache = Path(cache_dir).expanduser().resolve(); cache.mkdir(parents=True,exist_ok=True)
    for previous in cache.glob("pdf-workbench-update-*/"+release.asset.name):
        _cancel(cancel)
        if previous.is_symlink() or not previous.is_file() or previous.stat().st_size!=release.asset.size:continue
        digest=hashlib.sha256()
        with previous.open("rb") as stream:
            while chunk:=stream.read(1024*1024):_cancel(cancel);digest.update(chunk)
        if digest.hexdigest()==release.asset.sha256:
            if progress:progress(100,"Vorhandener Download wurde erneut geprüft.")
            return DownloadedUpdate(release,previous)
    run = Path(tempfile.mkdtemp(prefix="pdf-workbench-update-",dir=cache))
    archive = run/release.asset.name
    try:
        digest, received = hashlib.sha256(), 0
        with _open(release.asset.url) as response, archive.open("xb") as stream:
            length = response.headers.get("Content-Length")
            if length is not None and length != str(release.asset.size): raise UpdateError("Der Download hat eine unerwartete Dateigröße.")
            while True:
                _cancel(cancel)
                chunk = response.read(128*1024)
                if not chunk: break
                received += len(chunk)
                if received > release.asset.size or received > MAX_DOWNLOAD: raise UpdateError("Das Updatepaket ist größer als angekündigt.")
                digest.update(chunk); stream.write(chunk)
                if progress: progress(round(100*received/release.asset.size),"Update wird heruntergeladen …")
            stream.flush(); os.fsync(stream.fileno())
        _cancel(cancel)
        if received != release.asset.size or digest.hexdigest() != release.asset.sha256:
            raise UpdateError("Das Updatepaket stimmt nicht mit beiden veröffentlichten SHA-256-Prüfsummen überein.")
        return DownloadedUpdate(release,archive)
    except BaseException:
        shutil.rmtree(run); raise
