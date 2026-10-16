"""Capture the application's own widgets for layout QA; no desktop content is read."""

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

from better_voice_input.app import MainWindow
from better_voice_input.pipeline import ModelDownloads
from better_voice_input.settings import Settings
from better_voice_input.ui import STYLE, ModelDialog, SettingsDialog

app = QApplication([])
fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
for filename in ("msyh.ttc", "msyhbd.ttc", "SegoeIcons.ttf", "segmdl2.ttf"):
    if (fonts / filename).is_file():
        QFontDatabase.addApplicationFont(str(fonts / filename))
app.setStyleSheet(STYLE)
root = Path(__file__).resolve().parents[1]
output = root / "artifacts" / "screenshots"
output.mkdir(parents=True, exist_ok=True)
window = MainWindow(Settings(), native=False)
window.show()
app.processEvents()
window.grab().save(str(output / "main-empty.png"))
window.original.setPlainText(
    "嗯，我我我想明天九点，不对，十点和张三开会。地点在上海。\n对了，前面地点说错了，是杭州。周三或者周四上线吧，还没确定。"
)
window.result.setPlainText("我想明天十点和张三在杭州开会。周三或者周四上线吧，还没确定。")
window.status.setText("整理好了")
window.stats.setText("SenseVoice Small 识别 0.8 秒    文字整理 2.1 秒    本次 520 tokens")
app.processEvents()
window.grab().save(str(output / "main-result.png"))
window.state = "recording"
window.status.setText("正在听，慢慢说")
window.hint.setText("最长 2 分钟；停顿不会自动结束。Esc 取消本次录音。")
window.refresh_controls()
for level in (20, 60, 35, 80, 45, 70, 30, 55):
    window.meter.setValue(level)
    window.meter._advance()
app.processEvents()
window.grab().save(str(output / "main-recording.png"))
window.state = "idle"
window.refresh_controls()
window.set_notice("请核对“王五”的识别结果。原文有“不是＋数字”的表达，请核对预算。")
window.resize(800, 620)
app.processEvents()
window.grab().save(str(output / "main-compact.png"))
dialog = SettingsDialog(Settings(), window)
dialog.show()
app.processEvents()
dialog.grab().save(str(output / "settings.png"))
dialog.close()
models = ModelDialog(Settings(), ModelDownloads(), lambda _settings: True, window)
models.show()
app.processEvents()
models.grab().save(str(output / "models.png"))
models.tab_buttons[1].click()
app.processEvents()
models.grab().save(str(output / "models-plaza.png"))
models.close()
overlay = window.overlay
overlay.label.setText("正在听  00:07")
overlay.level.show()
overlay.set_tone("rec")
for level in (30, 70, 50, 90, 40, 60):
    overlay.level.setValue(level)
    overlay.level._advance()
overlay.show_near_bottom()
app.processEvents()
overlay.grab().save(str(output / "overlay.png"))
window.shutdown()
window.close()
print(output)
