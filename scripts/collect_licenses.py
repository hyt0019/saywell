"""Retain license files shipped with installed distributions in the local build."""

import importlib.metadata
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "Saywell" / "licenses"
count = 0
for dist in importlib.metadata.distributions():
    for filename in dist.files or []:
        if (
            filename.name.lower().startswith(("license", "copying", "copyright"))
            and ".." not in filename.parts
        ):
            source = dist.locate_file(filename)
            if source.is_file():
                target = destination / dist.metadata["Name"] / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                count += 1
print(f"Preserved {count} upstream license files.")
