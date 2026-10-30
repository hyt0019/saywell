"""Write release/update.json for the version in pyproject.toml, signed with the update key.

    python scripts/sign_update.py            # run by scripts/build_installer.ps1 after the installer
    python scripts/sign_update.py --new-key  # once: create private/update-signing.key

Installed copies download update.json, check its signature against PUBLIC_KEY in
src/saywell/updates.py and only then download the installer it names. Keep a copy of the secret
key somewhere safe outside this folder: without it no installed copy accepts another update."""

import argparse
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))

from saywell.signing import public_key, sign  # noqa: E402

KEY = root / "private" / "update-signing.key"


def new_key() -> None:
    if KEY.exists():
        raise SystemExit(f"{KEY} already exists; a new key would make installed copies refuse updates.")
    KEY.parent.mkdir(exist_ok=True)
    secret = secrets.token_bytes(32)
    KEY.write_text(secret.hex() + "\n", encoding="ascii")
    print(f"Wrote {KEY}. Back it up, then set PUBLIC_KEY in src/saywell/updates.py to:")
    print(public_key(secret).hex())


def write_update() -> None:
    from saywell.updates import DOWNLOADS, PUBLIC_KEY, REPOSITORY, parse_update

    version = re.search(r'^version = "(.+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)[1]
    name = f"Saywell-Setup-{version}.exe"
    installer = root / "release" / name
    # The first line of the release notes is a one-sentence summary of the version.
    notes = (root / "docs" / "releases" / f"v{version}.md").read_text(encoding="utf-8").strip().splitlines()[0]
    secret = bytes.fromhex(KEY.read_text(encoding="ascii").strip())
    if public_key(secret) != PUBLIC_KEY:
        raise SystemExit(f"{KEY} does not match PUBLIC_KEY in src/saywell/updates.py.")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    manifest = json.dumps(
        {
            "version": version,
            "notes": notes,
            "page": f"{REPOSITORY}/releases/tag/v{version}",
            "installer": {
                "name": name,
                "size": installer.stat().st_size,
                "sha256": digest,
                "urls": [f"{DOWNLOADS}/releases/{name}", f"{REPOSITORY}/releases/download/v{version}/{name}"],
            },
        },
        ensure_ascii=False,
    )
    data = json.dumps({"manifest": manifest, "signature": sign(secret, manifest.encode()).hex()}, ensure_ascii=False)
    parse_update(data.encode())  # The same check installed copies make.
    (root / "release" / "update.json").write_text(data + "\n", encoding="utf-8")
    print(f"Wrote release\\update.json for {version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-key", action="store_true", help="create the signing key (once)")
    if parser.parse_args().new_key:
        new_key()
    else:
        write_update()
