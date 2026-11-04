"""Build the icon files from the swallow's geometry (src/saywell/brand.py):

- assets/app.ico: the exe and installer icon, every size drawn on its own;
- src/saywell/assets/icon.png: a 1024 px copy for documents and the website.

Run: .venv\\Scripts\\python.exe scripts\\make_icon.py
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from saywell.brand import icon_image  # noqa: E402
from saywell.ui import ASSETS, ICON_SIZES  # noqa: E402


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def main() -> None:
    app = QApplication.instance() or QApplication([])  # noqa: F841 - image plugins need an app
    images = [png_bytes(icon_image(size)) for size in ICON_SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, image in zip(ICON_SIZES, images, strict=True):
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(image), offset + len(data))
        data += image
    target = ROOT / "assets" / "app.ico"
    target.write_bytes(header + entries + data)
    print(f"{target}  {len(images)} 个尺寸  {target.stat().st_size / 1024:.0f} KB")
    large = ASSETS / "icon.png"
    icon_image(1024).save(str(large))
    print(f"{large}  1024 px")


if __name__ == "__main__":
    main()
