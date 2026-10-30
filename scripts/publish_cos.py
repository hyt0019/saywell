"""Upload the release in release/ to Tencent Cloud COS, where installed copies look first.

    python scripts/publish_cos.py

Run after scripts/build_installer.ps1 and before the GitHub release (docs/DEVELOPMENT.md). Needs a
key allowed to write to the bucket in TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY. The
installer and its .sha256 go to releases/, releases/Saywell-Setup-latest.exe (the website's
/download link) is pointed at the new installer, and update.json is replaced last, so the
manifest never names an installer that is not there yet. Every object is read back afterwards."""

import hashlib
import hmac
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

from saywell.updates import DOWNLOADS, parse_update  # noqa: E402

HOST = urlsplit(DOWNLOADS).hostname
EXE = "application/vnd.microsoft.portable-executable"
TEXT = "text/plain; charset=utf-8"
JSON = "application/json; charset=utf-8"
FOREVER = "public, max-age=31536000, immutable"
# The website's /download link points here.
LATEST = "releases/Saywell-Setup-latest.exe"


def authorization(method: str, path: str, secret_id: str, secret_key: str) -> str:
    """COS request signature (cloud.tencent.com/document/product/436/7778), covering only the host."""
    now = int(time.time())
    key_time = f"{now - 60};{now + 3600}"
    sign_key = hmac.new(secret_key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
    request = f"{method.lower()}\n{path}\n\nhost={HOST}\n"
    to_sign = f"sha1\n{key_time}\n{hashlib.sha1(request.encode()).hexdigest()}\n"
    signature = hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()
    return (
        f"q-sign-algorithm=sha1&q-ak={secret_id}&q-sign-time={key_time}&q-key-time={key_time}"
        f"&q-header-list=host&q-url-param-list=&q-signature={signature}"
    )


def put(client: httpx.Client, key: str, body: bytes, headers: dict[str, str]) -> None:
    path = "/" + key
    secret_id, secret_key = os.environ["TENCENTCLOUD_SECRET_ID"], os.environ["TENCENTCLOUD_SECRET_KEY"]
    headers = headers | {"Authorization": authorization("put", path, secret_id, secret_key)}
    response = client.put(path, content=body, headers=headers)
    if response.status_code != 200:
        raise SystemExit(f"Uploading {key} failed: HTTP {response.status_code}\n{response.text[:500]}")
    print("uploaded", key)


def main() -> None:
    if not (os.environ.get("TENCENTCLOUD_SECRET_ID") and os.environ.get("TENCENTCLOUD_SECRET_KEY")):
        raise SystemExit("Set TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY first.")
    version = re.search(r'^version = "(.+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
    name = f"Saywell-Setup-{version}.exe"
    installer = (root / "release" / name).read_bytes()
    checksum = (root / "release" / f"{name}.sha256").read_bytes()
    manifest = (root / "release" / "update.json").read_bytes()
    release = parse_update(manifest)  # The same check installed copies make.
    digest = hashlib.sha256(installer).hexdigest()
    if (release.version, release.installer, release.sha256) != (version, name, digest):
        raise SystemExit("release/update.json does not describe this installer; run scripts/sign_update.py.")
    if f"{DOWNLOADS}/releases/{name}" not in release.urls:
        raise SystemExit("release/update.json does not list the COS address; run scripts/sign_update.py.")

    saved_as = {"Content-Disposition": f'attachment; filename="{name}"'}
    uploads = [
        (f"releases/{name}", installer, {"Content-Type": EXE, "Cache-Control": FOREVER} | saved_as),
        (f"releases/{name}.sha256", checksum, {"Content-Type": TEXT, "Cache-Control": FOREVER}),
        # Copied inside COS rather than uploaded again.
        (LATEST, b"", {"x-cos-copy-source": f"{HOST}/releases/{name}", "x-cos-metadata-directive": "Replaced",
                       "Content-Type": EXE, "Cache-Control": "no-cache"} | saved_as),
        ("update.json", manifest, {"Content-Type": JSON, "Cache-Control": "no-cache"}),
    ]
    with httpx.Client(base_url=f"https://{HOST}", timeout=httpx.Timeout(600, connect=15)) as client:
        for key, body, headers in uploads:
            put(client, key, body, headers)
        # Read everything back the way installed copies and visitors will: anonymously.
        for key in (f"releases/{name}", LATEST):
            response = client.head("/" + key)
            if response.status_code != 200 or int(response.headers["Content-Length"]) != len(installer):
                raise SystemExit(f"{key} is not readable as uploaded (HTTP {response.status_code}).")
        if client.get("/update.json").content != manifest:
            raise SystemExit("update.json in COS differs from release/update.json.")
    print(f"Published {version} to {DOWNLOADS}")


if __name__ == "__main__":
    main()
