import hashlib
import json
from pathlib import Path

import httpx
import pytest

from saywell import signing, updates
from saywell.updates import Release, UpdateError, download_release, fetch_release, is_newer, parse_update

# RFC 8032, section 7.1, tests 1-3: secret key, public key, message, signature.
RFC_VECTORS = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]
SECRET = bytes(range(32))


@pytest.mark.parametrize(("secret", "public", "message", "signature"), RFC_VECTORS)
def test_ed25519_matches_rfc_8032(secret, public, message, signature):
    secret, public, message, signature = map(bytes.fromhex, (secret, public, message, signature))
    assert signing.public_key(secret) == public
    assert signing.sign(secret, message) == signature
    assert signing.verify(public, message, signature)
    assert not signing.verify(public, message + b"!", signature)
    assert not signing.verify(public, message, signature[:-1] + bytes([signature[-1] ^ 1]))


def test_signature_with_an_oversized_scalar_is_refused():
    secret, public, message, signature = map(bytes.fromhex, RFC_VECTORS[0])
    s = int.from_bytes(signature[32:], "little") + signing._L  # Same point, non-canonical encoding.
    assert not signing.verify(public, message, signature[:32] + s.to_bytes(32, "little"))


def manifest(version="9.9.0", **changes) -> dict:
    fields = {
        "version": version,
        "notes": "这一版修了一些问题。",
        "page": f"https://github.com/hyt0019/saywell/releases/tag/v{version}",
        "installer": {
            "name": f"Saywell-Setup-{version}.exe",
            "size": 11,
            "sha256": hashlib.sha256(b"new version").hexdigest(),
            "urls": [f"https://github.com/hyt0019/saywell/releases/download/v{version}/Saywell-Setup-{version}.exe"],
        },
    }
    fields.update(changes)
    return fields


def signed(fields: dict, secret: bytes = SECRET) -> bytes:
    text = json.dumps(fields, ensure_ascii=False)
    return json.dumps({"manifest": text, "signature": signing.sign(secret, text.encode()).hex()}).encode()


@pytest.fixture(autouse=True)
def test_key(monkeypatch):
    monkeypatch.setattr(updates, "PUBLIC_KEY", signing.public_key(SECRET))


def test_a_signed_manifest_describes_the_release():
    release = parse_update(signed(manifest()))
    assert release.version == "9.9.0" and release.installer == "Saywell-Setup-9.9.0.exe" and release.size == 11
    assert release.urls[0].startswith("https://github.com/")


@pytest.mark.parametrize(
    "data",
    [
        signed(manifest(), secret=bytes(32)),  # Someone else's key.
        signed(manifest()).replace(b"9.9.0", b"9.9.1"),  # Edited after signing.
        b"not json",
        json.dumps({"manifest": {"version": "9.9.0"}, "signature": "00"}).encode(),
        signed(manifest(installer={**manifest()["installer"], "name": "../evil.exe"})),
        signed(manifest(installer={**manifest()["installer"], "name": "Saywell-Setup-1.0.0.exe"})),
        signed(manifest(installer={**manifest()["installer"], "urls": ["http://example.com/a.exe"]})),
        signed(manifest(installer={**manifest()["installer"], "sha256": "abc"})),
        signed(manifest(installer={**manifest()["installer"], "size": "11"})),
        signed(manifest(page="javascript:alert(1)")),
        signed(manifest(version="latest")),
    ],
)
def test_unsigned_or_malformed_manifests_are_refused(data):
    with pytest.raises(UpdateError):
        parse_update(data)


def test_versions_compare_by_number():
    assert is_newer("0.6.10", "0.6.9") and is_newer("1.0", "0.9.9")
    assert not is_newer("0.6.5", "0.6.5") and not is_newer("0.6.4", "0.6.5")


def test_the_first_valid_manifest_wins_and_bad_sources_are_skipped():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "down.example":
            raise httpx.ConnectError("refused")
        if request.url.host == "evil.example":
            return httpx.Response(200, content=signed(manifest(), secret=bytes(32)))
        return httpx.Response(200, content=signed(manifest()))

    urls = ("https://down.example/u.json", "https://evil.example/u.json", "https://good.example/u.json")
    release = fetch_release(urls, transport=httpx.MockTransport(handler))
    assert release.version == "9.9.0" and seen == ["down.example", "evil.example", "good.example"]


def test_fetch_reports_why_no_manifest_was_found():
    def missing(request):
        if request.url.host == "slow.example":
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(404)

    urls = ("https://a.example/u.json", "https://slow.example/u.json")
    with pytest.raises(UpdateError, match="HTTP 404"):  # Not "cannot connect": one server did answer.
        fetch_release(urls, transport=httpx.MockTransport(missing))
    with pytest.raises(UpdateError, match="连不上"):
        fetch_release(urls[1:], transport=httpx.MockTransport(missing))
    huge = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * (updates.MANIFEST_LIMIT + 1)))
    with pytest.raises(UpdateError, match="过大"):
        fetch_release(("https://a.example/u.json",), transport=huge)


def release(urls: tuple[str, ...]) -> Release:
    return Release("9.9.0", "", "https://example.com", "Saywell-Setup-9.9.0.exe", 11, manifest()["installer"]["sha256"], urls)


def test_installer_is_downloaded_from_the_next_address_and_checked(tmp_path, monkeypatch):
    def handler(request):
        if request.url.host == "broken.example":
            return httpx.Response(200, content=b"bad version")  # Right size, wrong content.
        return httpx.Response(200, content=b"new version")

    real_client = httpx.Client
    monkeypatch.setattr(
        "saywell.models.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)
    )
    urls = ("https://broken.example/a.exe", "https://good.example/a.exe")
    path = download_release(release(urls), lambda received: None, folder=tmp_path)
    assert path == tmp_path / "Saywell-Setup-9.9.0.exe" and path.read_bytes() == b"new version"
    assert not list(tmp_path.glob("*.part"))


def test_download_fails_when_no_address_has_the_right_file(tmp_path, monkeypatch):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"bad version"))
    monkeypatch.setattr("saywell.models.httpx.Client", lambda **kwargs: real_client(transport=transport, **kwargs))
    with pytest.raises(UpdateError, match="下载失败"):
        download_release(release(("https://a.example/a.exe",)), lambda received: None, folder=tmp_path)
    assert not (tmp_path / "Saywell-Setup-9.9.0.exe").exists()


def test_old_installers_are_removed_and_newer_ones_kept(tmp_path):
    for name in ("Saywell-Setup-0.6.5.exe", "Saywell-Setup-0.6.6.exe", "Saywell-Setup-0.6.7.exe.part", "notes.txt"):
        (tmp_path / name).write_text("x")
    updates.remove_old_installers(tmp_path, current="0.6.6")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["Saywell-Setup-0.6.7.exe.part", "notes.txt"]


def test_only_the_installed_copy_updates_itself(monkeypatch, tmp_path):
    import winreg

    assert not updates.installed_here()  # Run from the source.
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.sys, "executable", str(tmp_path / "Saywell" / "Saywell.exe"))

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *args: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (str(tmp_path / "SAYWELL") + "\\", 1))
    assert updates.installed_here()  # Inno Setup's path ends in a backslash; case does not matter.
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (str(tmp_path / "Other") + "\\", 1))
    assert not updates.installed_here()


def test_install_command_quotes_paths_and_starts_the_program_again():
    command = updates.install_command(
        Path(r"C:\Users\O'Brien\AppData\Local\Saywell\updates\Saywell-Setup-9.9.0.exe"),
        "9.9.0",
        Path(r"C:\Users\O'Brien\AppData\Local\Programs\Saywell\Saywell.exe"),
        Path(r"C:\Users\O'Brien\AppData\Local\Saywell\update-install.log"),
    )
    assert command == (
        r"Start-Process -Wait -FilePath 'C:\Users\O''Brien\AppData\Local\Saywell\updates\Saywell-Setup-9.9.0.exe' "
        r"""-ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LOG="C:\Users\O''Brien\AppData\Local\Saywell\update-install.log"'; """
        r"Start-Process -FilePath 'C:\Users\O''Brien\AppData\Local\Programs\Saywell\Saywell.exe' "
        r"-ArgumentList '--updated','9.9.0'"
    )
