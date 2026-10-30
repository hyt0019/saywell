"""Updates: find a newer release, download its installer and hand over to it.

Each release publishes update.json next to its installer. The manifest inside is signed with the
project's Ed25519 key, and the installer is checked against the size and SHA-256 it names, so a
mirror or download proxy can at most delay an update, never change what gets installed."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

import httpx

from . import __version__
from .models import GITHUB_PROXIES, ModelError, ModelFile, download_file
from .settings import data_dir
from .signing import verify

# Checks update.json; the secret half stays with the maintainer (scripts/sign_update.py).
PUBLIC_KEY = bytes.fromhex("5e5dea91a30245525a2020a576b51be1b4cea549fbd0ae970fe5079ac643a7ac")
REPOSITORY = "https://github.com/hyt0019/saywell"
# Tencent Cloud COS in Guangzhou, fast in mainland China; scripts/publish_cos.py uploads each release.
DOWNLOADS = "https://saywell-1328034135.cos.ap-guangzhou.myqcloud.com"
_LATEST = f"{REPOSITORY}/releases/latest/download/update.json"
# Where update.json of the newest release is looked for, in order.
MANIFEST_URLS = (f"{DOWNLOADS}/update.json", _LATEST, *(proxy + _LATEST for proxy in GITHUB_PROXIES))
# Inno Setup's uninstall entry for the installer's AppId names the folder it installed into.
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{8C6A7C72-4900-4CAE-AC30-A85A108AA07F}_is1"
_VERSION = r"\d+(?:\.\d+){1,3}"
_INSTALLER = re.compile(rf"Saywell-Setup-({_VERSION})\.exe")
MANIFEST_LIMIT = 64 * 1024


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    page: str
    installer: str
    size: int
    sha256: str
    urls: tuple[str, ...]


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def is_newer(version: str, current: str = __version__) -> bool:
    return version_key(version) > version_key(current)


def _https(value) -> bool:
    return isinstance(value, str) and value.startswith("https://") and len(value) <= 500


def parse_update(data: bytes) -> Release:
    """The release an update.json describes, once its signature and every field check out."""
    try:
        outer = json.loads(data)
        manifest, signature = outer["manifest"], bytes.fromhex(outer["signature"])
    except (ValueError, TypeError, KeyError):
        raise UpdateError("更新信息格式不对，已忽略。") from None
    if not isinstance(manifest, str) or not verify(PUBLIC_KEY, manifest.encode(), signature):
        raise UpdateError("更新信息的签名不对，已忽略。")
    try:
        fields = json.loads(manifest)
        installer = fields["installer"]
        release = Release(
            fields["version"],
            fields["notes"],
            fields["page"],
            installer["name"],
            installer["size"],
            installer["sha256"],
            tuple(installer["urls"]),
        )
    except (ValueError, TypeError, KeyError):
        raise UpdateError("更新信息格式不对，已忽略。") from None
    name = _INSTALLER.fullmatch(release.installer) if isinstance(release.installer, str) else None
    if not (
        isinstance(release.version, str)
        and re.fullmatch(_VERSION, release.version)
        and name
        and name[1] == release.version
        and isinstance(release.notes, str)
        and len(release.notes) <= 300
        and _https(release.page)
        and type(release.size) is int
        and 0 < release.size <= 500_000_000
        and isinstance(release.sha256, str)
        and re.fullmatch(r"[0-9a-f]{64}", release.sha256)
        and 0 < len(release.urls) <= 10
        and all(_https(url) for url in release.urls)
    ):
        raise UpdateError("更新信息格式不对，已忽略。")
    return release


def fetch_release(urls: tuple[str, ...] = MANIFEST_URLS, transport: httpx.BaseTransport | None = None) -> Release:
    """The newest published release, from the first address that returns a valid update.json. When
    none does, the most telling problem is reported: a refused file over a server's answer over
    no answer at all."""
    refused: UpdateError | None = None
    answered: httpx.HTTPStatusError | None = None
    timeout = httpx.Timeout(15, connect=8)
    with httpx.Client(follow_redirects=True, timeout=timeout, transport=transport) as client:
        for url in urls:
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    data = b""
                    for chunk in response.iter_bytes():
                        data += chunk
                        if len(data) > MANIFEST_LIMIT:
                            raise UpdateError("更新信息过大，已忽略。")
                return parse_update(data)
            except UpdateError as exc:
                refused = exc
            except httpx.HTTPStatusError as exc:
                answered = answered or exc
            except httpx.HTTPError:
                pass
    if refused:
        raise refused
    if answered:
        raise UpdateError(f"更新服务器暂时没有响应（HTTP {answered.response.status_code}），请稍后再试。")
    raise UpdateError("连不上更新服务器，请检查网络后重试。")


def updates_dir() -> Path:
    return data_dir() / "updates"


def download_release(
    release: Release, report: Callable[[int], None], cancel: Event | None = None, folder: Path | None = None
) -> Path:
    """Download the installer into the updates folder and return its path once its digest matches."""
    folder = folder or updates_dir()
    target = folder / release.installer
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for url in release.urls:
            try:
                spec = ModelFile(release.installer, url, release.size, release.sha256)
                download_file(spec, target, report, cancel)
                return target
            except ModelError:
                continue
    except OSError:
        raise UpdateError("无法保存更新，请检查磁盘空间和用户目录权限。") from None
    raise UpdateError("更新下载失败，请检查网络后重试。")


def remove_old_installers(folder: Path | None = None, current: str = __version__) -> None:
    """Installers, finished or not, of this version or older ones are no longer needed."""
    folder = folder or updates_dir()
    for path in folder.glob("Saywell-Setup-*"):
        name = _INSTALLER.fullmatch(path.name.removesuffix(".part"))
        if name and not is_newer(name[1], current):
            try:
                path.unlink()
            except OSError:
                pass  # Still running, perhaps; removed on a later start.


def installed_here() -> bool:
    """True when this program is the copy the installer put in place, the only one an update can
    replace. Copies run from the cloud-drive folder, a build folder or the source are not."""
    if not getattr(sys, "frozen", False):
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            location = winreg.QueryValueEx(key, "InstallLocation")[0]
    except OSError:
        return False
    return isinstance(location, str) and Path(location) == Path(sys.executable).parent


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def install_command(installer: Path, version: str, program: Path, log: Path) -> str:
    """PowerShell that runs the installer without its wizard, waits for it, then starts the program
    again with --updated, whether or not the install went through: a failed install rolls back,
    and the old version then says so instead of silently staying closed."""
    # Start-Process joins the arguments with spaces, so the log path carries its own quotes.
    flags = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f'/LOG="{log}"')
    arguments = ",".join(_quoted(item) for item in flags)
    return (
        f"Start-Process -Wait -FilePath {_quoted(str(installer))} -ArgumentList {arguments}; "
        f"Start-Process -FilePath {_quoted(str(program))} -ArgumentList '--updated',{_quoted(version)}"
    )


def start_install(installer: Path, version: str) -> None:
    """Hand over to the installer; the caller quits right after so its files can be replaced. The
    installer also stops any copy still running from the install folder."""
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    command = install_command(installer, version, Path(sys.executable), data_dir() / "update-install.log")
    subprocess.Popen(
        [str(system / "WindowsPowerShell" / "v1.0" / "powershell.exe"), "-NoProfile", "-NonInteractive"]
        + ["-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", command],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
