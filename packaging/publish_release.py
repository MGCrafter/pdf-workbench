"""Publish compact app downloads; retain verified sources in a separate archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request

from release_layout import prepare_assets

SOURCE_TAG = "source-archives"
SOURCE_MARKER = "<!-- pdf-workbench-materials-v1 -->"


def gh(*arguments, payload=None):
    result = subprocess.run(["gh", *map(str, arguments)],
                            input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "GitHub command failed")
    return result.stdout


def api(path, *, method="GET", payload=None, paged=False):
    arguments = ["api", path]
    if method != "GET":
        arguments += ["--method", method]
    if payload is not None:
        arguments += ["--input", "-"]
    if paged:
        arguments += ["--paginate", "--slurp"]
    # Reads may be retried. Mutations are reconciled with server state instead.
    for attempt in range(3):
        try:
            output = gh(*arguments, payload=payload)
            break
        except (RuntimeError, subprocess.TimeoutExpired):
            if method != "GET" or attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return json.loads(output) if output.strip() else None


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def assets(repo, release):
    pages = api(f"repos/{repo}/releases/{release['id']}/assets?per_page=100", paged=True)
    result = {item["name"]: item for page in pages for item in page}
    if len(result) != sum(map(len, pages)):
        raise ValueError("Duplicate release asset names")
    for name, item in result.items():
        if Path(name).name != name or name in (".", "..") or "\\" in name:
            raise ValueError("Unsafe release asset name")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", item.get("digest") or "") or item["state"] != "uploaded":
            raise ValueError("A complete server digest is required: " + name)
    return result


def same_asset(first, second):
    return all(first.get(key) == second.get(key) for key in ("id", "name", "size", "digest", "state"))


def verify_remote(item, path):
    if item["size"] != path.stat().st_size or item["digest"] != "sha256:" + digest(path):
        raise ValueError("Published bytes differ: " + path.name)


def public_head(item):
    request = urllib.request.Request(item["browser_download_url"], method="HEAD",
                                     headers={"User-Agent": "PDFWorkbench-release-verification"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 200 or not response.url.startswith("https://"):
            raise ValueError("Source archive is not publicly accessible")


def source_release(repo, target):
    releases = [item for page in api(f"repos/{repo}/releases?per_page=100", paged=True) for item in page]
    matches = [item for item in releases if item["tag_name"] == SOURCE_TAG]
    if matches:
        release = matches[0]
        if SOURCE_MARKER not in (release.get("body") or ""):
            raise ValueError("Existing source archive release is not managed by this publisher")
        return release
    body = (SOURCE_MARKER + "\n\nQuellen und Prüfberichte zu PDF Workbench.\n\n"
            "Die versionierten TAR-Dateien enthalten den vollständigen App-Quellcode, "
            "die passenden Bibliotheksquellen und Build-/Testberichte. Die JSON-Dateien "
            "nennen Inhalt, Größen und SHA-256-Prüfsummen.\n\n"
            "[Die App herunterladen](https://github.com/" + repo + "/releases/latest). "
            "Zum Benutzen werden diese Entwicklungsarchive nicht benötigt.\n")
    return api(f"repos/{repo}/releases", method="POST", payload={
        "tag_name": SOURCE_TAG, "target_commitish": target,
        "name": "Quellenarchiv und Prüfberichte", "body": body,
        "draft": True, "prerelease": False, "make_latest": "false"})


def publish_materials(repo, layout, target):
    release = source_release(repo, target)
    for path in (layout["materials_archive"], layout["materials_manifest"]):
        current = assets(repo, release)
        if path.name not in current:
            gh("release", "upload", SOURCE_TAG, path, "--repo", repo)
            current = assets(repo, release)
        verify_remote(current[path.name], path)
    if release["draft"]:
        release = api(f"repos/{repo}/releases/{release['id']}", method="PATCH",
                      payload={"draft": False, "make_latest": "false"})
    current = assets(repo, release)
    for path in (layout["materials_archive"], layout["materials_manifest"]):
        verify_remote(current[path.name], path)
        public_head(current[path.name])
    return current[layout["materials_archive"].name]["browser_download_url"]


def notes_with_sources(notes, source_url):
    # Historical notes end with details about individual dependency downloads.
    notes = re.split(r"\n`SHA256SUMS` enthält", notes, maxsplit=1)[0].rstrip()
    return (notes + "\n\n`SHA256SUMS` enthält die Prüfsummen der beiden App-Pakete.\n\n"
            "[Quellen und Prüfberichte](" + source_url + ") sind separat gebündelt. "
            "Zum Benutzen reicht das passende Windows- oder Linux-Paket.\n")


def root_commit():
    roots = subprocess.check_output(["git", "rev-list", "--max-parents=0", "HEAD"], text=True).splitlines()
    if len(roots) != 1:
        raise ValueError("Expected one fully fetched repository history")
    if subprocess.check_output(["git", "rev-parse", "--is-shallow-repository"], text=True).strip() != "false":
        raise ValueError("Publisher needs a full checkout for the source archive tag")
    return roots[0]


def download(item, destination):
    for attempt in range(3):
        temporary = destination.with_name(destination.name + ".download")
        try:
            value, size = hashlib.sha256(), 0
            request = urllib.request.Request(item["browser_download_url"], headers={"User-Agent": "PDFWorkbench-release-cleanup"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("xb") as stream:
                if not response.url.startswith("https://"):
                    raise ValueError("Refusing insecure release download")
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > item["size"]:
                        raise ValueError("Release download exceeds its recorded size")
                    value.update(chunk); stream.write(chunk)
            if size != item["size"] or "sha256:" + value.hexdigest() != item["digest"]:
                raise ValueError("Release download digest/size mismatch")
            temporary.replace(destination)
            return
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
        finally:
            temporary.unlink(missing_ok=True)


def check_snapshot(repo, release, before, native_names, *, checksum=None):
    current = assets(repo, release)
    required = set(native_names) | ({"SHA256SUMS"} if checksum is None else set())
    if set(current) - set(before) or not required <= set(current):
        raise ValueError("Release changed concurrently; no further cleanup is safe")
    for name, item in current.items():
        if name == "SHA256SUMS" and checksum is not None and item["digest"] == "sha256:" + digest(checksum):
            verify_remote(item, checksum)
            continue
        if not same_asset(before[name], item):
            raise ValueError("Release asset changed concurrently: " + name)
    for name in native_names:
        if not same_asset(before[name], current[name]):
            raise ValueError("Native app asset identity changed")
    return current


def resume_materials(repo, tag, commit, release, directory, natives):
    """A published receipt permits safe continuation after a partial cleanup."""
    releases = [item for page in api(f"repos/{repo}/releases?per_page=100", paged=True) for item in page]
    source = next((item for item in releases if item["tag_name"] == SOURCE_TAG), None)
    if source is None or source["draft"]:
        return None
    if SOURCE_MARKER not in (source.get("body") or ""):
        raise ValueError("Unmanaged source archive release")
    available = assets(repo, source)
    stem = f"PDFWorkbench-{tag}-sources-and-reports"
    if stem + ".json" not in available:
        return None
    path = directory / "published-receipt.json"
    download(available[stem + ".json"], path)
    receipt = json.loads(path.read_text())
    origin = receipt.get("original_release", {})
    if (receipt.get("schema_version") != 1 or receipt.get("app_tag") != tag
            or receipt.get("app_source_commit") != commit or origin.get("id") != release["id"]
            or origin.get("tag") != tag):
        raise ValueError("Source receipt does not identify this original app release")
    records = origin["assets"]
    before = {item["name"]: dict(item, state="uploaded") for item in records}
    if len(before) != len(records) or not natives <= before.keys():
        raise ValueError("Incomplete original release receipt")
    members = {item["filename"]: item for item in receipt["members"]}
    if len(members) != len(receipt["members"]) or set(members) != set(before)-natives:
        raise ValueError("Source receipt does not preserve every original extra")
    for name, item in before.items():
        if Path(name).name != name or "\\" in name or not re.fullmatch(r"sha256:[a-f0-9]{64}", item["digest"]):
            raise ValueError("Invalid original release record")
        if name not in natives and (members[name]["sha256"] != item["digest"][7:]
                                   or members[name]["size"] != item["size"]):
            raise ValueError("Source receipt and original asset differ")
    native_records = {item["filename"]: item for item in receipt["native_assets"]}
    if set(native_records) != natives or any(native_records[name]["sha256"] != before[name]["digest"][7:]
                                            or native_records[name]["size"] != before[name]["size"] for name in natives):
        raise ValueError("Native archive receipt differs")
    archive = receipt["archive"]
    remote = available.get(stem + ".tar")
    if (remote is None or archive["filename"] != remote["name"]
            or remote["size"] != archive["size"] or remote["digest"] != "sha256:" + archive["sha256"]):
        raise ValueError("Published source archive differs from its receipt")
    public_head(remote); public_head(available[stem + ".json"])
    checksum = directory / "SHA256SUMS"
    checksum.write_text("".join(before[name]["digest"][7:] + "  " + name + "\n" for name in sorted(natives)),
                        encoding="utf-8", newline="\n")
    return before, checksum, remote["browser_download_url"]


def consolidate(repo, tag, workspace):
    release = api(f"repos/{repo}/releases/tags/{tag}")
    if release["draft"]:
        raise ValueError("Only published app releases can be consolidated")
    before = assets(repo, release)
    natives = {f"PDFWorkbench-{tag}-windows-x86_64.zip", f"PDFWorkbench-{tag}-linux-x86_64.tar.gz"}
    if not natives <= before.keys():
        raise ValueError("Both native app packages are required")
    commit = subprocess.check_output(["git", "rev-list", "-n", "1", tag], text=True).strip()
    directory = workspace / tag; directory.mkdir(parents=True)
    resumed = resume_materials(repo, tag, commit, release, directory, natives)
    if resumed is not None:
        before, checksum, source_url = resumed
        print(tag + ": continuing from verified public source receipt", flush=True)
    else:
        incoming = directory / "input"; incoming.mkdir()
        print(f"{tag}: preserving {len(before)-2} materials on the cloud runner", flush=True)
        for name, item in before.items():
            if name not in natives:
                download(item, incoming / name)
        layout = prepare_assets(incoming, directory / "prepared", tag, commit,
                                {name: before[name] for name in natives})
        if {item["filename"] for item in layout["members"]} != set(before)-natives:
            raise ValueError("Sources bundle does not preserve every original extra")
        manifest_path = layout["materials_manifest"]
        manifest = json.loads(manifest_path.read_text())
        manifest["original_release"] = {"id": release["id"], "tag": tag, "assets": [
            {key: before[name][key] for key in ("id", "name", "size", "digest")} for name in sorted(before)]}
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        source_url = publish_materials(repo, layout, root_commit())
        checksum = layout["app_checksums"]
    # No deletion, replacement or app-note edit occurs until public archive proof.
    current = check_snapshot(repo, release, before, natives, checksum=checksum)
    old_checksum = current.get("SHA256SUMS")
    if old_checksum is None or old_checksum["digest"] != "sha256:" + digest(checksum):
        if old_checksum is not None:
            fresh_checksum = api(f"repos/{repo}/releases/assets/{old_checksum['id']}")
            if not same_asset(old_checksum, fresh_checksum):
                raise ValueError("Checksum asset changed concurrently")
            api(f"repos/{repo}/releases/assets/{old_checksum['id']}", method="DELETE")
        # Exclusive upload preserves any foreign replacement in the gap.
        gh("release", "upload", tag, checksum, "--repo", repo)
    current = check_snapshot(repo, release, before, natives, checksum=checksum)
    verify_remote(current["SHA256SUMS"], checksum)
    fresh = api(f"repos/{repo}/releases/{release['id']}")
    api(f"repos/{repo}/releases/{release['id']}", method="PATCH", payload={
        "body": notes_with_sources(fresh["body"], source_url)})
    removed = []
    for name, original in before.items():
        if name in natives or name == "SHA256SUMS":
            continue
        if name not in current:
            continue  # Already removed by an earlier, receipt-backed run.
        fresh = api(f"repos/{repo}/releases/assets/{original['id']}")
        if not same_asset(original, fresh):
            raise ValueError("Refusing to delete a changed asset: " + name)
        api(f"repos/{repo}/releases/assets/{original['id']}", method="DELETE")
        removed.append(name)
        time.sleep(1)  # Stay below GitHub's content-mutation rate limit.
    after = assets(repo, release)
    if set(after) != natives | {"SHA256SUMS"}:
        raise ValueError("Unexpected assets after consolidation")
    for name in natives:
        if not same_asset(before[name], after[name]):
            raise ValueError("Native app asset was modified")
    verify_remote(after["SHA256SUMS"], checksum)
    result = {"tag": tag, "result": "PASS", "before": len(before), "after": len(after),
              "removed": removed, "sources": source_url, "preserved_native_assets": [before[name] for name in sorted(natives)]}
    (workspace / tag / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{tag}: {len(before)} -> {len(after)} app assets; native IDs unchanged", flush=True)


def create(repo, tag, incoming, notes):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="pdf-release-layout-") as temporary:
        layout = prepare_assets(incoming, Path(temporary) / "prepared", tag, commit)
        source_url = publish_materials(repo, layout, root_commit())
        notes_path = Path(temporary) / "notes.md"
        notes_path.write_text(notes_with_sources(notes.read_text(), source_url))
        published = [item for page in api(f"repos/{repo}/releases?per_page=100", paged=True) for item in page]
        versions = [tuple(map(int, item["tag_name"][1:].split("."))) for item in published
                    if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", item["tag_name"])
                    and not item["draft"] and not item["prerelease"]]
        latest = not versions or tuple(map(int, tag[1:].split("."))) >= max(versions)
        gh("release", "create", tag, *layout["app_assets"], "--repo", repo,
           "--verify-tag", "--title", "PDF Workbench " + tag, "--notes-file", notes_path,
           "--latest" if latest else "--latest=false")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "consolidate"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--tag", action="append", required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--notes-file", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path("build/asset-cleanup"))
    args = parser.parse_args()
    if not args.repo or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("Use an owner/repository identity")
    if any(not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag) for tag in args.tag):
        parser.error("Use existing vMAJOR.MINOR.PATCH tags")
    if args.mode == "create":
        if len(args.tag) != 1 or args.assets is None or args.notes_file is None:
            parser.error("create needs one tag, --assets and --notes-file")
        create(args.repo, args.tag[0], args.assets.resolve(), args.notes_file)
    else:
        for tag in args.tag:
            consolidate(args.repo, tag, args.workspace.resolve())


if __name__ == "__main__":
    main()
