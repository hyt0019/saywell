"""Capture the application's own widgets for layout QA; no desktop content is read."""

import os
import random
import time
from datetime import date, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

from saywell.app import MainWindow
from saywell.pipeline import ModelDownloads
from saywell.settings import Settings
from saywell.stats import Day, StatsStore
from saywell.ui import STYLE, ModelDialog, SettingsDialog

app = QApplication([])
fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
for filename in ("msyh.ttc", "msyhbd.ttc", "SegUIVar.ttf", "segoeui.ttf", "seguisb.ttf"):
    if (fonts / filename).is_file():
        QFontDatabase.addApplicationFont(str(fonts / filename))
app.setStyleSheet(STYLE)


def settle(milliseconds: int = 900):
    """Let number roll-ups, fades and slides finish before grabbing."""
    deadline = time.monotonic() + milliseconds / 1000
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


root = Path(__file__).resolve().parents[1]
output = root / "artifacts" / "screenshots"
output.mkdir(parents=True, exist_ok=True)
window = MainWindow(Settings(), native=False)
window.usage = StatsStore.empty()  # Never show or touch the real statistics file.
window.refresh_overview()
window.show()
settle(400)
window.grab().save(str(output / "main-empty.png"))

# Five months of made-up use, only for the screenshot.
rng = random.Random(7)
today = date.today()
for offset in range(150):
    if rng.random() < (0.8 if offset < 45 else 0.45):
        count = rng.randint(2, 30)
        chars = count * rng.randint(25, 90)
        window.usage.days[(today - timedelta(days=offset)).isoformat()] = Day(
            count, chars, chars / 3.2, count, count * 620, count * 130, count * 0.004
        )
window.balance, window.balance_time = "¥18.62", time.time()
window.refresh_overview()
window.overview.set_latest(
    "我想明天十点和张三在杭州开会。周三或者周四上线吧，还没确定。", "20:41 · 识别 0.8 秒 · 整理 2.1 秒"
)
settle()
window.grab().save(str(output / "main-overview.png"))
scroll = window.pages.widget(0)
scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
settle(200)
window.grab().save(str(output / "main-overview-bottom.png"))
scroll.verticalScrollBar().setValue(0)
window.show_page(1)
window.original.setPlainText(
    "嗯，我我我想明天九点，不对，十点和张三开会。地点在上海。\n对了，前面地点说错了，是杭州。周三或者周四上线吧，还没确定。"
)
window.result.setPlainText("我想明天十点和张三在杭州开会。周三或者周四上线吧，还没确定。")
window.status.setText("整理好了")
window.stats.setText("SenseVoice Small 识别 0.8 秒    文字整理 2.1 秒    本次 520 tokens")
settle(400)
window.grab().save(str(output / "main-result.png"))
window.state = "recording"
window.status.setText("正在听，慢慢说")
window.hint.setText("最长 2 分钟；停顿不会自动结束。Esc 取消本次录音。")
window.refresh_controls()
for level in (20, 60, 35, 80, 45, 70, 30, 55):
    window.meter.setValue(level)
    window.meter._advance()
settle(400)
window.grab().save(str(output / "main-recording.png"))
window.state = "idle"
window.refresh_controls()
window.set_notice("请核对“王五”的识别结果。原文有“不是＋数字”的表达，请核对预算。")
window.resize(800, 620)
settle(400)
window.grab().save(str(output / "main-compact.png"))
dialog = SettingsDialog(Settings(), window)
dialog.api_status.finish(True, "连接正常 · 响应 412 毫秒 · 余额 ¥18.62")
dialog.show()
settle(400)
dialog.grab().save(str(output / "settings.png"))
dialog.close()
models = ModelDialog(Settings(), ModelDownloads(), lambda _settings: True, window)
models.cards["sensevoice"].check_status.finish(True, "“今天下午三点开会” · 识别用时 0.3 秒")
models.cards["xasr"].check_status.running("正在校验 5 个文件…")
models.show()
settle(400)
models.grab().save(str(output / "models.png"))
models.tab_buttons[1].click()
settle(400)
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
settle(400)
overlay.grab().save(str(output / "overlay.png"))
window.shutdown()
window.close()
print(output)
