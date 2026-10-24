from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import replace
from datetime import date

from PySide6.QtCore import QEvent, QLockFile, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .api_config import is_deepseek_api
from .audio import AudioError, Recorder
from .cleanup import TRANSLATE_LANGUAGES, ApiCleaner, CleanupError
from .core import CleanupResult, single_line_text
from .dashboard import Overview
from .history import HistoryStore
from .models import get_model, models_ready
from .pipeline import Events, ModelDownloads, Pipeline
from .session import SessionGate
from .settings import Settings, data_dir, read_key
from .shortcuts import DEFAULT_HOTKEY, INSERT_HOTKEY, SOLO_HOLD_MS, parse_shortcut, shortcut_label
from .startup import StartupError, migrate_legacy_entry, save_settings_with_startup, startup_enabled
from .stats import StatsStore
from .ui import (
    STYLE,
    Glyph,
    HeroCard,
    LevelBars,
    MicButton,
    ModelDialog,
    RecordingOverlay,
    Segmented,
    SettingsDialog,
    Toast,
    app_icon,
    dialog_header,
    fade_in,
    footer_bar,
    glyph_label,
    label,
    make_button,
    run_task,
    text_column,
    tray_icon,
)
from .vocabulary import delete_lexicons, warm
from .windows import (
    Hotkeys,
    PasteError,
    SoloKeyHook,
    current_target,
    mask_alt_release,
    modifiers_held,
    paste_text,
    shortcut_held,
    tap_key,
)

IDLE_STATS = "音频只在本机识别，录音不落盘；整理时仅发送文字至所选 API。"


class MainWindow(QMainWindow):
    PILLS = {
        "recording": ("rec", "录音中"),
        "busy": ("busy", "处理中"),
        "waiting": ("busy", "等待选择输入框"),
    }
    TONES = {"idle": "#AEAEB2", "rec": "#FF3B30", "busy": "#FF9F0A", "done": "#34C759"}

    def __init__(self, settings: Settings | None = None, native: bool = True):
        super().__init__()
        self.settings = settings or Settings.load()
        self.gate = SessionGate()
        self.events = Events()
        self.pipeline = Pipeline(self.events)
        self.downloads = ModelDownloads()
        self.model_dialog: ModelDialog | None = None
        self.recorder = Recorder(self.events.level.emit)
        self.state = "idle"
        self.target = None
        self.from_hotkey = False
        self.extra_warnings: list[str] = []
        self.quitting = False
        self.native = native
        self.hotkeys = Hotkeys() if native else None
        # A recording key held on its own (right Ctrl, right Alt, CapsLock): a press only starts
        # recording after SOLO_HOLD_MS, so quick taps and shortcuts such as right Ctrl+C keep working.
        self.solo: SoloKeyHook | None = None
        self.solo_state = ""  # "pending" while waiting, "chord" if another key came, "active" once started
        self.solo_timer = QTimer(self)
        self.solo_timer.setSingleShot(True)
        self.solo_timer.setInterval(SOLO_HOLD_MS)
        self.solo_timer.timeout.connect(self.solo_long_press)
        self.overlay = RecordingOverlay()
        self.usage = StatsStore() if self.settings.collect_stats else None
        self.balance: str | None = None
        self.balance_time = 0.0
        # Graphics card the speech model last ran on; "CPU" after GPU acceleration fell back, "" if unknown.
        self.device = ""
        self.setWindowTitle("好好说 Saywell")
        self.setWindowIcon(app_icon())
        available = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(1100, max(680, min(860, available - 40)))
        self.setMinimumSize(860, 620)
        self._build_ui()
        self.events.stage.connect(self.on_stage)
        self.events.transcript.connect(self.on_transcript)
        self.events.completed.connect(self.on_complete)
        self.events.failed.connect(self.on_error)
        self.events.level.connect(self.on_level)
        self.downloads.progress.connect(self.on_download_progress)
        self.downloads.finished.connect(self.on_download_finished)
        self.downloads.failed.connect(self.on_download_failed)
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.activated.connect(self.cancel)
        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray.setToolTip("好好说 Saywell · 语音输入")
        menu = QMenu(self)
        show = QAction("打开好好说", self)
        show.triggered.connect(self.reveal)
        menu.addAction(show)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.reveal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        if native:
            self.tray.show()
            QApplication.instance().installNativeEventFilter(self.hotkeys)
            self.hotkeys.events.triggered.connect(self.on_hotkey)
            self.configure_hotkeys()
        self.find_dropped_models()
        self.refresh_controls()
        self.refresh_overview()
        if native:
            QTimer.singleShot(1500, self.update_balance)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(28, 22, 28, 16)
        layout.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(46, 46))
        header.addWidget(logo)
        header.addSpacing(4)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        name = QHBoxLayout()
        name.setSpacing(8)
        name.addWidget(label("好好说", "brand"))
        name.addWidget(label("Saywell", "brandLatin"), 0, Qt.AlignmentFlag.AlignBottom)
        name.addStretch()
        brand.addLayout(name)
        brand.addWidget(label("保留你的意思，整理你的表达。", "muted"))
        header.addLayout(brand)
        header.addStretch()
        self.page_switch = Segmented(("概览", "文字"))
        self.page_group, self.page_buttons = self.page_switch.group, self.page_switch.buttons
        self.page_switch.changed.connect(self.show_page)
        header.addWidget(self.page_switch)
        header.addStretch()
        self.history_button = make_button("历史", Glyph.HISTORY, "toolbar")
        self.history_button.clicked.connect(self.open_history)
        self.import_button = make_button("导入录音", Glyph.IMPORT, "toolbar")
        self.import_button.clicked.connect(self.import_audio)
        self.models_button = make_button("语音模型", Glyph.MODEL, "toolbar")
        self.models_button.clicked.connect(self.open_models)
        self.settings_button = make_button("设置", Glyph.SETTINGS, "toolbar")
        self.settings_button.clicked.connect(self.open_settings)
        for button in (self.history_button, self.import_button, self.models_button, self.settings_button):
            header.addWidget(button)
        layout.addLayout(header)

        self.hero = hero = HeroCard()
        recording = QHBoxLayout(hero)
        recording.setContentsMargins(28, 18, 22, 16)
        recording.setSpacing(20)
        status_layout = QVBoxLayout()
        status_layout.setSpacing(6)
        self.pill = QLabel()
        self.pill.setObjectName("pill")
        self.pill.setTextFormat(Qt.TextFormat.RichText)
        status_layout.addWidget(self.pill)
        self.status = label("准备好，慢慢说", "status")
        self.hint = label("按住快捷键说话，松开后自动整理并输入。停顿和改口都没关系。", "heroHint", wrap=True)
        status_layout.addWidget(self.status)
        status_layout.addWidget(self.hint)
        status_layout.addSpacing(6)
        # At rest the bottom line shows the shortcuts; while listening it becomes the voice level.
        self.hero_bottom = QStackedWidget()
        self.hero_bottom.setFixedHeight(34)
        meta = QWidget()
        chips = QHBoxLayout(meta)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(8)
        self.shortcut_label = label("", "heroMeta")
        self.insert_label = label(f"{INSERT_HOTKEY} 补输结果", "heroMeta")
        self.model_chip = make_button("", Glyph.MODEL, "heroChip")
        self.model_chip.setToolTip("更换或下载本地语音模型")
        self.model_chip.clicked.connect(self.open_models)
        chips.addWidget(self.shortcut_label)
        chips.addWidget(label("·", "heroMeta"))
        chips.addWidget(self.insert_label)
        chips.addWidget(label("·", "heroMeta"))
        chips.addWidget(self.model_chip)
        chips.addStretch()
        self.hero_bottom.addWidget(meta)
        self.meter = LevelBars(56)
        self.hero_bottom.addWidget(self.meter)
        status_layout.addWidget(self.hero_bottom)
        recording.addLayout(status_layout, 1)
        controls = QVBoxLayout()
        controls.setSpacing(4)
        controls.addStretch()
        self.record_button = MicButton("开始录音")
        self.record_button.setObjectName("primary")
        self.record_button.clicked.connect(lambda: self.toggle_recording(False))
        controls.addWidget(self.record_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.cancel_button = make_button("取消", None, "heroGhost")
        policy = self.cancel_button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.cancel_button.setSizePolicy(policy)
        self.cancel_button.clicked.connect(self.cancel)
        controls.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)
        controls.addStretch()
        recording.addLayout(controls)
        layout.addWidget(hero)

        self.notice = QLabel()
        self.notice.setObjectName("notice")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)

        self.overview = Overview()
        self.overview.open_text.connect(lambda: self.show_page(1))
        self.overview.copy_latest.connect(lambda: self.copy_text(True))
        self.overview.insert_latest.connect(self.schedule_manual_insert)
        self.overview.refresh_balance.connect(lambda: self.update_balance(force=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 6, 4)
        holder_layout.addWidget(self.overview)
        holder_layout.addStretch()
        scroll.setWidget(holder)
        self.pages.addWidget(scroll)

        text_page = QWidget()
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(14)
        self.pages.addWidget(text_page)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(16)
        splitter.setChildrenCollapsible(False)
        original_widget, self.original, _ = text_column(
            "原始识别", "录音后，原始识别文字会出现在这里。\n\n也可以粘贴一段文字，直接测试整理效果。"
        )
        result_widget, self.result, _ = text_column(
            "整理结果", "结巴、重复和明确的改口会被整理。\n\n原文保留在左侧，结果可以直接编辑。", accent=True
        )
        splitter.addWidget(original_widget)
        splitter.addWidget(result_widget)
        splitter.setSizes([500, 500])
        text_layout.addWidget(splitter, 1)
        self.original.textChanged.connect(self.refresh_controls)
        self.result.textChanged.connect(self.result_edited)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.clean_button = make_button("整理文字", Glyph.EDIT)
        self.clean_button.clicked.connect(self.clean_text)
        self.copy_original_button = make_button("复制原文", Glyph.COPY, "ghost")
        self.copy_original_button.clicked.connect(lambda: self.copy_text(False))
        self.clear_button = make_button("清空", Glyph.CLEAR, "ghost")
        self.clear_button.clicked.connect(self.clear)
        actions.addWidget(self.clean_button)
        actions.addWidget(self.copy_original_button)
        actions.addWidget(self.clear_button)
        actions.addStretch()
        # 翻译：把（改好的）整理结果交给 API 译成所选语言，替换右侧内容，Ctrl+Z 可撤销。
        self.translating = False
        self.translate_button = make_button("翻译", Glyph.TRANSLATE)
        self.translate_menu = QMenu(self.translate_button)
        self.translate_menu.aboutToShow.connect(self.fill_translate_menu)
        self.translate_button.setMenu(self.translate_menu)
        actions.addWidget(self.translate_button)
        self.copy_button = make_button("复制结果", Glyph.COPY)
        self.copy_button.clicked.connect(lambda: self.copy_text(True))
        self.insert_button = make_button("输入到其他窗口", Glyph.SEND, "primary")
        self.insert_button.clicked.connect(self.schedule_manual_insert)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.insert_button)
        text_layout.addLayout(actions)
        self.toast = Toast(central)
        self.show_page(0)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(glyph_label(Glyph.LOCK, "#8E8E93", 13))
        self.stats = label(IDLE_STATS, "caption")
        self.stats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.stats, 1)
        self.download_status = label("", "caption")
        self.download_status.hide()
        bottom.addWidget(self.download_status)
        self.download_progress = QProgressBar()
        self.download_progress.setTextVisible(False)
        self.download_progress.setRange(0, 100)
        self.download_progress.setFixedWidth(140)
        self.download_progress.hide()
        bottom.addWidget(self.download_progress)
        self.download_button = make_button("下载语音模型", Glyph.DOWNLOAD, "primary")
        self.download_button.clicked.connect(self.open_models)
        bottom.addWidget(self.download_button)
        layout.addLayout(bottom)

    def show_page(self, index: int):
        changed = self.pages.currentIndex() != index
        self.pages.setCurrentIndex(index)
        self.page_switch.set_current(index)
        if changed and self.isVisible():
            fade_in(self.pages.currentWidget(), 200)
        if index == 1:
            self.original.setFocus()

    def refresh_overview(self):
        try:
            deepseek = is_deepseek_api(self.settings.api_base_url)
        except ValueError:
            deepseek = False
        self.overview.refresh(self.usage, date.today(), deepseek, self.usage is not None)
        provider = "DeepSeek" if deepseek else self.provider_name()
        updated = time.strftime("%H:%M 更新", time.localtime(self.balance_time)) if self.balance else ""
        self.overview.set_balance(self.balance if deepseek else None, provider, updated)

    def provider_name(self) -> str:
        from urllib.parse import urlsplit

        host = urlsplit(self.settings.api_base_url).hostname or ""
        parts = host.removeprefix("api.").split(".")
        return parts[0].capitalize() if parts and parts[0] else ""

    def update_balance(self, force: bool = False):
        """Fetch the DeepSeek balance in the background, at most once a minute unless asked."""
        try:
            if not is_deepseek_api(self.settings.api_base_url):
                return
        except ValueError:
            return
        if not force and time.time() - self.balance_time < 60:
            return
        from .checks import fetch_balance

        base_url = self.settings.api_base_url
        self.overview.refresh_button.setEnabled(False)

        def work(_report):
            return fetch_balance(base_url, read_key(api_base_url=base_url))

        def done(value):
            self.overview.refresh_button.setEnabled(True)
            if isinstance(value, str):
                self.balance, self.balance_time = value, time.time()
            self.refresh_overview()

        run_task(self, work, done)

    def record_usage(self, data: dict):
        if self.usage is None:
            return
        result = data["result"]
        if data.get("duration"):
            self.usage.record_dictation(result.original, data["duration"])
        self.usage.record_request(result.usage, self.settings.api_base_url, self.settings.model)
        try:
            self.usage.save()
        except OSError:
            pass

    def refresh_controls(self):
        if not hasattr(self, "insert_button"):  # Still building the window.
            return
        idle = self.state == "idle"
        self.record_button.setEnabled(self.state in ("idle", "recording"))
        self.record_button.setText("结束并整理" if self.state == "recording" else "开始录音")
        self.record_button.setObjectName("recording" if self.state == "recording" else "primary")
        self.record_button.set_busy(self.state == "busy")
        self.hero.set_mode(self.state)
        self.cancel_button.setVisible(not idle)
        self.import_button.setEnabled(idle)
        self.settings_button.setEnabled(idle)
        self.history_button.setEnabled(idle)
        self.models_button.setEnabled(idle)
        self.model_chip.setEnabled(idle)
        self.clean_button.setEnabled(idle and bool(self.original.toPlainText().strip()))
        self.copy_original_button.setEnabled(bool(self.original.toPlainText().strip()))
        self.copy_button.setEnabled(bool(self.result.toPlainText().strip()))
        self.insert_button.setEnabled(
            idle and bool(self.result.toPlainText().strip()) and not self.gate.inserted and not self.translating
        )
        self.translate_button.setEnabled(idle and bool(self.result.toPlainText().strip()) and not self.translating)
        self.clear_button.setEnabled(idle and not self.translating)
        self.original.setReadOnly(not idle)
        self.result.setReadOnly(not idle or self.translating)
        model = get_model(self.settings.asr_model)
        ready = models_ready(self.settings.models, model=model.id)
        self.download_button.setVisible(not ready and self.downloads.active is None)
        self.download_button.setEnabled(idle)
        if not ready:
            where = "未下载"
        elif model.gpu and self.settings.gpu_acceleration and self.device != "CPU":
            where = "GPU 加速"
        else:
            where = "本机识别"
        self.model_chip.setText(f"{model.name} · {where}")
        if self.state in self.PILLS:
            tone, text = self.PILLS[self.state]
        else:
            tone, text = ("done", "已整理") if self.result.toPlainText().strip() else ("idle", "就绪")
        self.pill.setText(f"<span style='color:{self.TONES[tone]}'>●</span>&nbsp;&nbsp;{text}")
        self.pill.setProperty("tone", tone)
        bottom = 1 if self.state == "recording" else 0
        if self.hero_bottom.currentIndex() != bottom:
            self.hero_bottom.setCurrentIndex(bottom)
            if self.isVisible():
                fade_in(self.hero_bottom.currentWidget(), 280)
        action ="按住说话" if self.settings.hold_to_talk else "按一下开始，再按结束"
        self.shortcut_label.setText(f"{shortcut_label(self.settings.hotkey)} {action}")

    def result_edited(self):
        if self.state == "idle":
            self.gate.inserted = False
        self.refresh_controls()

    def set_notice(self, text: str = ""):
        appearing = bool(text) and not self.notice.isVisible() and self.isVisible()
        self.notice.setText(text)
        self.notice.setVisible(bool(text))
        if appearing:
            fade_in(self.notice, 320)

    def configure_hotkeys(self):
        if not self.hotkeys:
            return
        if not self.activate_hotkey(self.settings.hotkey):
            self.set_notice("录音快捷键被其他程序占用，请在设置中更换。仍可点击开始录音。")
        if not self.hotkeys.register(2, 0x0001, ord("V")):
            self.set_notice(f"{INSERT_HOTKEY} 被其他程序占用，请使用复制结果或输入按钮。")

    def activate_hotkey(self, name: str) -> bool:
        """Listen for the recording shortcut name instead of the current one."""
        self.suspend_hotkey()
        shortcut = parse_shortcut(name) or parse_shortcut(DEFAULT_HOTKEY)
        if not shortcut.solo:
            return self.hotkeys.register(1, shortcut.modifiers, shortcut.key)
        # CapsLock is kept from other programs and re-sent on a quick tap, so holding it to
        # talk does not flip it; the right-hand modifiers pass through untouched.
        self.solo = SoloKeyHook(shortcut.key, swallow=shortcut.name == "CapsLock")
        self.solo.events.pressed.connect(self.solo_pressed)
        self.solo.events.released.connect(self.solo_released)
        self.solo.events.chorded.connect(self.solo_chorded)
        if self.solo.start():
            return True
        self.solo = None
        return False

    def suspend_hotkey(self):
        """Stop listening for the recording shortcut, e.g. while settings are open."""
        if self.hotkeys:
            self.hotkeys.unregister(1)
        if self.solo:
            self.solo.stop()
            self.solo = None
        self.solo_timer.stop()
        self.solo_state = ""

    def hotkey_active(self) -> bool:
        return bool(self.solo) or bool(self.hotkeys and 1 in self.hotkeys.registered)

    def hotkey_held(self) -> bool:
        if self.solo:
            return self.solo.held
        return shortcut_held(self.settings.hotkey)

    def solo_pressed(self):
        self.solo_state = "pending"
        self.solo_timer.start()

    def solo_chorded(self):
        if self.solo_state == "pending":
            self.solo_timer.stop()
            self.solo_state = "chord"

    def solo_long_press(self):
        if self.solo_state != "pending" or not self.solo:
            return
        self.solo_state = "active"
        if self.settings.hotkey == "RightAlt":
            mask_alt_release()
        self.on_hotkey(1)

    def solo_released(self):
        state, self.solo_state = self.solo_state, ""
        self.solo_timer.stop()
        # A swallowed CapsLock that turned out to be a tap (or part of a shortcut) still toggles.
        if state in ("pending", "chord") and self.solo and self.solo.swallow:
            tap_key(self.solo.key)
        # Hold-to-talk notices the release in tick() through hotkey_held().

    def on_hotkey(self, identifier: int):
        if identifier == 1:
            if self.state == "recording" and self.settings.hold_to_talk:
                return
            self.toggle_recording(True)
        elif identifier == 2 and self.state == "idle" and self.result.toPlainText().strip():
            target = current_target()
            if target and target.process != os.getpid():
                self.from_hotkey = True
                self.attempt_insert(self.gate.generation, target)
        elif identifier == 3:
            self.cancel()

    def begin(self, kind: str):
        job, cancel = self.gate.begin()
        self.state = kind
        self.set_notice()
        self.extra_warnings = []
        self.result.clear()
        if self.hotkeys:
            self.hotkeys.register(3, 0, 0x1B)
        self.refresh_controls()
        return job, cancel

    def toggle_recording(self, from_hotkey: bool):
        if self.state == "recording":
            self.stop_recording()
            return
        if self.state != "idle":
            return
        self.from_hotkey = from_hotkey
        if not models_ready(self.settings.models, model=self.settings.asr_model):
            self.find_dropped_models()
        model = get_model(self.settings.asr_model)
        if not models_ready(self.settings.models, model=model.id):
            if self.downloads.active == model.id:
                self.set_notice(f"{model.name} 正在下载，完成后即可录音。")
            else:
                self.set_notice(f"请先点击“下载语音模型”，下载 {model.name}（{model.size_label}）。")
            self.feedback("语音模型未就绪", "点击托盘图标，下载语音模型", tone="warn")
            return
        target = current_target() if from_hotkey else None
        self.target = target if target and target.process != os.getpid() else None
        self.begin("recording")
        self.original.clear()
        try:
            self.recorder.start(self.settings.microphone)
        except AudioError as exc:
            self.on_error(self.gate.generation, str(exc))
            return
        self.status.setText("正在听，慢慢说")
        self.hint.setText("最长 2 分钟；停顿不会自动结束。Esc 取消本次录音。")
        self.overlay.hint.setText(
            "松开快捷键结束，Esc 取消"
            if from_hotkey and self.settings.hold_to_talk
            else "再次按快捷键结束，Esc 取消"
        )
        self.overlay.label.setText("正在听  00:00")
        self.overlay.level.setValue(0)
        self.overlay.level.show()
        self.overlay.set_tone("rec")
        self.overlay.show_near_bottom()

    def stop_recording(self):
        if self.state != "recording":
            return
        try:
            samples = self.recorder.stop()
        except Exception:
            self.on_error(self.gate.generation, "录音设备已断开，请重新选择麦克风。")
            return
        if self.recorder.overflow:
            self.extra_warnings.append("录音期间设备报告丢帧，请检查原文是否完整。")
        self.state = "busy"
        self.status.setText("正在本地识别…")
        self.hint.setText("原始转写会保留，识别后再整理整段表达。")
        self.overlay.label.setText("正在识别和整理…")
        self.overlay.hint.setText("Esc 取消本次处理")
        self.overlay.set_tone("busy")
        self.refresh_controls()
        self.pipeline.start(self.gate.generation, self.gate.cancel_event, self.settings, samples, "audio")

    def tick(self):
        if self.state == "recording":
            seconds = int(time.monotonic() - self.recorder.started)
            self.overlay.label.setText(f"正在听  {seconds // 60:02}:{seconds % 60:02}")
            if self.recorder.limit_reached.is_set():
                self.stop_recording()
                self.set_notice("已达到 2 分钟上限，正在处理刚才的录音。")
            elif self.from_hotkey and self.settings.hold_to_talk and not self.hotkey_held():
                self.stop_recording()

    def on_level(self, level: float):
        if self.state == "recording":
            self.meter.setValue(int(level * 100))
            self.overlay.level.setValue(int(level * 100))

    def on_stage(self, job: int, message: str):
        if self.gate.accepts(job):
            self.status.setText(message)
            self.overlay.label.setText(message)

    def on_transcript(self, job: int, text: str):
        if self.gate.accepts(job):
            self.original.setPlainText(text)

    def finish_ui(self):
        self.state = "idle"
        self.overlay.dismiss()
        self.meter.setValue(0)
        if self.hotkeys:
            self.hotkeys.unregister(3)
        self.refresh_controls()

    def on_complete(self, job: int, data: dict):
        # A duplicate completion must not reset the insert guard via textChanged.
        if not self.gate.accepts(job) or self.state != "busy":
            return
        result = data["result"]
        self.original.setPlainText(result.original)
        self.result.setPlainText(result.text)
        if data.get("device") is not None and get_model(self.settings.asr_model).gpu:
            self.device = data.get("device") or "CPU"
        self.finish_ui()
        warnings = list(result.warnings) + self.extra_warnings
        if data.get("gpu_problem"):
            warnings.append(f"GPU 加速未能启用（{data['gpu_problem']}），已改用 CPU 识别。")
        model = get_model(self.settings.asr_model).name
        self.stats.setText(
            f"{model} 识别 {data['asr_seconds']:.1f} 秒    文字整理 {result.elapsed:.1f} 秒    "
            f"本次 {result.usage.get('total_tokens', 0)} tokens"
        )
        self.record_usage(data)
        meta = f"{time.strftime('%H:%M')} · 识别 {data['asr_seconds']:.1f} 秒 · 整理 {result.elapsed:.1f} 秒"
        self.overview.set_latest(result.text, meta)
        self.refresh_overview()
        if self.native:
            self.update_balance()
        if self.settings.save_history:
            try:
                HistoryStore().append(result.original, result.text)
            except Exception:
                warnings.append("本次历史记录保存失败，当前结果仍可使用。")
        if data.get("used_original"):
            self.status.setText("识别原文已就绪")
        elif result.translated_to:
            self.status.setText(f"整理好了，已翻译为{result.translated_to}")
        else:
            self.status.setText("整理好了")
        self.hint.setText(f"可以直接编辑结果，或回到输入框按 {INSERT_HOTKEY}。")
        self.set_notice("\n".join(warnings))
        if self.from_hotkey and self.target:
            self.attempt_insert(job, self.target)
        elif self.from_hotkey and self.settings.keep_in_clipboard:
            QApplication.clipboard().setText(single_line_text(result.text))
            self.feedback("整理好了，结果已复制", "打开输入框后按 Ctrl+V 粘贴")
        else:
            self.feedback("整理好了，结果已保留", f"在目标输入框按 {INSERT_HOTKEY} 输入")

    def feedback(self, title: str, hint: str, milliseconds: int = 4500, tone: str = "done"):
        """Global dictation never activates the main window, including failures."""
        if self.from_hotkey:
            self.overlay.show_message(title, hint, milliseconds, tone)
            self.tray.setToolTip(f"好好说 · {title}")
        else:
            self.reveal()

    def attempt_insert(self, job: int, target, tries: int = 0):
        if not self.gate.accepts(job) or self.gate.inserted or self.state != "idle":
            return
        if modifiers_held() and tries < 30:
            QTimer.singleShot(60, lambda: self.attempt_insert(job, target, tries + 1))
            return
        if not self.gate.claim_insert(job):
            return
        keep = self.settings.keep_in_clipboard
        try:
            paste_text(self.result.toPlainText(), target, QApplication.clipboard(), keep_result=keep)
            self.status.setText("已提交输入")
            self.hint.setText("请在目标应用查看文字。可继续按录音快捷键输入下一段。")
            self.refresh_controls()
            if self.from_hotkey:
                self.feedback("已输入，剪贴板里也有一份" if keep else "已输入", "可以继续按住快捷键说下一段", 1200)
        except PasteError as exc:
            self.gate.inserted = False
            self.set_notice(str(exc))
            self.refresh_controls()
            if keep:
                QApplication.clipboard().setText(single_line_text(self.result.toPlainText()))
                self.feedback("未能自动输入，结果已复制", "打开输入框后按 Ctrl+V 粘贴", tone="warn")
            else:
                self.feedback("未能自动输入，结果已保留", "点击托盘图标查看原因或复制结果", tone="warn")

    def schedule_manual_insert(self):
        if self.state != "idle" or not self.result.toPlainText().strip() or self.gate.inserted:
            return
        job = self.gate.generation
        self.from_hotkey = False
        self.state = "waiting"
        self.status.setText("请在 3 秒内点击目标输入框")
        self.refresh_controls()

        def insert():
            if not self.gate.accepts(job) or self.state != "waiting":
                return
            self.state = "idle"
            target = current_target()
            if target and target.process != os.getpid():
                self.attempt_insert(job, target)
            else:
                self.set_notice(f"未选中其他窗口，请切换到目标输入框后按 {INSERT_HOTKEY}。")
            self.refresh_controls()

        QTimer.singleShot(3000, insert)

    def fill_translate_menu(self):
        self.translate_menu.clear()
        title = self.translate_menu.addAction("把整理结果翻译成")
        title.setEnabled(False)
        for language in TRANSLATE_LANGUAGES:
            action = self.translate_menu.addAction(language)
            action.setCheckable(True)
            action.setChecked(language == self.settings.translate_language)
            action.triggered.connect(lambda _checked=False, language=language: self.translate_result(language))

    def translate_result(self, language: str):
        text = self.result.toPlainText().strip()
        if self.state != "idle" or not text or self.translating:
            return
        if language != self.settings.translate_language:
            self.settings = replace(self.settings, translate_language=language)
            try:
                self.settings.save()
            except OSError:
                pass  # Only the remembered choice is lost.
        settings, generation = self.settings, self.gate.generation
        self.translating = True
        self.translate_button.setText("正在翻译…")
        self.set_notice()
        self.refresh_controls()

        def work(_report):
            from .checks import CheckResult

            try:
                cleaner = ApiCleaner(
                    read_key(api_base_url=settings.api_base_url),
                    settings.model,
                    settings.api_timeout,
                    base_url=settings.api_base_url,
                )
                return cleaner.translate(text, language)
            except CleanupError as exc:
                return CheckResult(False, str(exc))

        def done(value):
            self.translating = False
            self.translate_button.setText("翻译")
            if not isinstance(value, CleanupResult):
                self.set_notice(f"翻译未完成：{value.message}")
            elif self.gate.generation == generation and self.result.toPlainText().strip() == text:
                # Replace through the cursor so Ctrl+Z brings the untranslated text back.
                cursor = self.result.textCursor()
                cursor.beginEditBlock()
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.insertText(value.text)
                cursor.endEditBlock()
                self.status.setText(f"已翻译为{language}")
                self.toast.show_text(f"已翻译为{language}，Ctrl+Z 可撤销")
                self.record_usage({"result": value})
                self.refresh_overview()
            self.refresh_controls()

        run_task(self, work, done)

    def clean_text(self):
        text = self.original.toPlainText().strip()
        if self.state != "idle" or not text:
            return
        self.target = None
        self.from_hotkey = False
        job, cancel = self.begin("busy")
        self.pipeline.start(job, cancel, self.settings, text, "text")

    def import_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择录音（最长 2 分钟）",
            "",
            "音频文件 (*.wav *.m4a *.mp3 *.flac *.ogg *.aac);;所有文件 (*)",
        )
        if path:
            self.process_file(path)

    def process_file(self, path: str):
        if self.state != "idle":
            return
        if not models_ready(self.settings.models, model=self.settings.asr_model):
            self.find_dropped_models()
        if not models_ready(self.settings.models, model=self.settings.asr_model):
            self.set_notice("请先在“语音模型”中下载当前选择的模型。")
            return
        self.target = None
        self.from_hotkey = False
        job, cancel = self.begin("busy")
        self.original.clear()
        self.pipeline.start(job, cancel, self.settings, path, "file")

    def open_models(self):
        if self.state != "idle":
            return
        dialog = ModelDialog(self.settings, self.downloads, self.apply_model_settings, self, self.release_model)
        self.model_dialog = dialog
        try:
            dialog.exec()
        finally:
            self.model_dialog = None
        self.refresh_controls()

    def find_dropped_models(self):
        """Use model folders copied into the download location by hand, such as from the cloud drive."""
        found = self.settings.with_found_models()
        if found != self.settings:
            self.apply_model_settings(found)

    def changeEvent(self, event):
        # Back from Explorer, where a model folder may have just been copied in.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self.state == "idle":
            self.find_dropped_models()
        super().changeEvent(event)

    def release_model(self) -> bool:
        """Unload the speech model so its files can be deleted; False while it is recognizing."""
        if not self.pipeline.release_recognizer():
            return False
        self.device = ""
        return True

    def apply_model_settings(self, new: Settings) -> bool:
        try:
            new.save()
        except OSError:
            return False
        previous = self.settings
        self.settings = new
        if (new.asr_model, new.models) != (previous.asr_model, previous.models):
            self.pipeline.release_recognizer()  # Loaded again on the next recognition.
            self.device = ""
        self.refresh_controls()
        return True

    def on_download_progress(self, model_id: str, percent: int):
        self.download_status.setText(f"正在{self.downloads.verb} {get_model(model_id).name}  {percent}%")
        self.download_progress.setValue(percent)
        self.download_status.show()
        self.download_progress.show()
        self.download_button.hide()

    def on_download_finished(self, model_id: str):
        self.download_status.hide()
        self.download_progress.hide()
        model, verb = get_model(model_id), self.downloads.verb
        current = get_model(self.settings.asr_model)
        # Nothing could be recorded with the chosen model yet: use the one that just arrived, so a
        # first download or an imported model pack works without another click.
        if (
            model_id != current.id
            and not models_ready(self.settings.models, model=current)
            and models_ready(self.settings.model_path(model_id), model=model)
        ):
            switched = replace(self.settings, asr_model=model_id)
            if self.apply_model_settings(switched) and self.model_dialog:
                self.model_dialog.settings = switched
        if model_id == self.settings.asr_model and self.state == "idle":
            self.status.setText("语音模型已就绪")
            self.hint.setText("现在可以开始录音，也可以导入已有录音。")
        else:
            self.stats.setText(f"{model.name} 已{verb}，可在“语音模型”中切换使用。")
        if not self.isVisible() and self.native:
            self.tray.showMessage(
                "好好说", f"{model.name} 已{verb}完成。", QSystemTrayIcon.MessageIcon.Information, 3000
            )
        self.refresh_controls()

    def on_download_failed(self, model_id: str, message: str):
        self.download_status.hide()
        self.download_progress.hide()
        if message:
            self.set_notice(f"{get_model(model_id).name}：{message}")
        self.refresh_controls()

    def on_error(self, job: int, message: str):
        if self.gate.accepts(job):
            self.finish_ui()
            self.status.setText("本次处理未完成")
            self.hint.setText("原始文字已保留，可以复制或重新整理。")
            self.set_notice(message)
            self.feedback("本次处理未完成", "点击托盘图标查看原因或重试", tone="warn")

    def cancel(self):
        if self.state == "idle":
            return
        self.gate.cancel()
        self.recorder.cancel()
        self.finish_ui()
        self.status.setText("已取消")
        self.hint.setText("本次结果不会输入到其他窗口。")

    def clear(self):
        if self.state != "idle":
            return
        self.gate.begin()
        self.original.clear()
        self.result.clear()
        self.set_notice()
        self.status.setText("准备好，慢慢说")
        self.stats.setText(IDLE_STATS)

    def copy_text(self, result: bool):
        text = single_line_text(self.result.toPlainText() if result else self.original.toPlainText())
        if text:
            QApplication.clipboard().setText(text)
            self.toast.show_text("已复制结果" if result else "已复制原文")
            buttons = (self.copy_button, self.overview.copy_button) if result else (self.copy_original_button,)
            for button in buttons:
                if button.isVisible():
                    button.flash("已复制")

    def open_settings(self):
        try:
            current = replace(self.settings, start_on_login=startup_enabled())
        except StartupError as exc:
            self.set_notice(str(exc))
            return
        dialog = SettingsDialog(current, self)
        # Typing a new shortcut, or holding right Ctrl in the dialog, must not start a recording.
        self.suspend_hotkey()
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        known = {item["id"] for item in current.lexicons} | getattr(dialog, "imported_ids", set())
        try:
            self.apply_settings_dialog(dialog, accepted)
        finally:
            # Lexicon files imported or removed in the dialog go unless the saved settings list them.
            delete_lexicons(known - {item["id"] for item in self.settings.lexicons})
            warm(self.settings.lexicons)
            self.configure_hotkeys()

    def apply_settings_dialog(self, dialog, accepted: bool):
        if getattr(dialog, "stats_cleared", False):
            (self.usage or StatsStore()).clear()
            if self.usage is not None:
                self.usage = StatsStore()
            self.refresh_overview()
        if accepted:
            new = dialog.updated
            if self.hotkeys and not self.activate_hotkey(new.hotkey):
                self.set_notice("所选快捷键已被占用，设置未修改，请选择其他组合。")
                return  # open_settings puts the saved shortcut back.
            try:
                save_settings_with_startup(new)
                if new.gpu_acceleration != self.settings.gpu_acceleration:
                    self.pipeline.release_recognizer()  # Free the card or the CPU copy; reloaded on next use.
                    self.device = ""
                self.settings = new
                if new.collect_stats != (self.usage is not None):
                    self.usage = StatsStore() if new.collect_stats else None
                self.configure_hotkeys()
                self.refresh_controls()
                self.refresh_overview()
                self.toast.show_text("设置已保存")
                if self.native:
                    self.update_balance(force=True)
                self.status.setText("设置已保存")
            except StartupError as exc:
                self.configure_hotkeys()
                self.set_notice(str(exc))
            except OSError:
                self.configure_hotkeys()
                self.set_notice("无法写入本地设置，请检查用户目录权限。")

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_history(self):
        store = HistoryStore()
        try:
            rows = store.read()
        except OSError:
            self.set_notice("历史记录无法读取，请检查本地目录权限。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("本机历史 · 好好说")
        dialog.resize(680, 500)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(dialog_header("本机历史", "默认不保存。开启后仅保留最近 5 条文字，超过 7 天自动清理。"))
        body = QVBoxLayout()
        body.setContentsMargins(30, 6, 30, 22)
        listing = QListWidget()
        listing.setWordWrap(True)
        for row in rows:
            date = time.strftime("%m-%d %H:%M", time.localtime(row["created"]))
            listing.addItem(f"{date}    {row['text'][:90].replace(chr(10), ' ')}")
        empty = label("还没有历史记录。可以在“设置 → 隐私与启动”中开启。", "muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setVisible(not rows)
        listing.setVisible(bool(rows))
        body.addWidget(listing, 1)
        body.addWidget(empty, 1)
        layout.addLayout(body, 1)
        footer, actions = footer_bar()
        clear_button = make_button("清空全部历史", Glyph.DELETE, "danger", "#D70015")
        clear_button.setEnabled(bool(rows))
        load_button = make_button("载入选中记录", None, "primary")
        load_button.setEnabled(bool(rows))
        actions.addWidget(clear_button)
        actions.addStretch()
        actions.addWidget(load_button)
        layout.addWidget(footer)

        def load():
            index = listing.currentRow()
            if index < 0:
                return
            self.gate.begin()
            self.target = None
            self.original.setPlainText(rows[index]["original"])
            self.result.setPlainText(single_line_text(rows[index]["text"]))
            self.status.setText("已载入历史，请核对后使用")
            self.set_notice("这是一条历史记录，请检查日期、人物和条件是否仍适用。")
            dialog.accept()

        def clear_history():
            try:
                store.clear()
                rows.clear()
                listing.clear()
                load_button.setEnabled(False)
                clear_button.setEnabled(False)
                listing.hide()
                empty.show()
            except OSError:
                QMessageBox.warning(dialog, "无法清空", "请检查本地目录权限。")

        load_button.clicked.connect(load)
        listing.itemDoubleClicked.connect(lambda _: load())
        clear_button.clicked.connect(clear_history)
        dialog.exec()

    def closeEvent(self, event):
        if self.native and not self.quitting and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            self.tray.showMessage(
                "好好说仍在运行",
                f"使用 {shortcut_label(self.settings.hotkey)} 录音；右键托盘图标可退出。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            event.ignore()
        else:
            self.shutdown()
            event.accept()

    def shutdown(self):
        self.downloads.cancel(wait=2, discard=False)  # The unfinished file is resumed next time.
        self.pipeline.release_recognizer()  # Stops a model engine running in its own process.
        self.gate.cancel()
        self.recorder.cancel()
        self.timer.stop()
        self.overlay.dismiss_timer.stop()
        self.overlay.close()
        self.tray.hide()
        if self.hotkeys:
            self.suspend_hotkey()
            self.hotkeys.close()
            QApplication.instance().removeNativeEventFilter(self.hotkeys)

    def quit_app(self):
        self.quitting = True
        self.shutdown()
        QApplication.instance().quit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="启动后自动退出，用于打包检查")
    parser.add_argument("--background", action="store_true", help="配置就绪时仅在托盘运行")
    args = parser.parse_args()
    if args.smoke:
        # Downloads, the text API and Qwen3-ASR all open httpx clients; a build whose Python
        # cannot create an HTTPS context must fail here, not on users' machines.
        import httpx

        httpx.Client().close()
        # Imported lexicons are matched by pinyin; the packaged pypinyin data must be there.
        from .vocabulary import TermIndex

        if TermIndex.build(["龚彧宸"]).lookup("龚玉晨") != ["龚彧宸"]:
            raise RuntimeError("Pinyin matching does not work in this build.")
        # The default shortcut, holding right Ctrl, needs the low-level keyboard hook.
        hook = SoloKeyHook(0xA3)
        if not hook.start():
            raise RuntimeError("The keyboard hook cannot be installed in this build.")
        hook.stop()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Saywell")
    app.setOrganizationName("Saywell")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(data_dir() / "application.lock"))
    lock.setStaleLockTime(0)
    if not args.smoke and not lock.tryLock(100):
        QMessageBox.information(None, "好好说", "程序已在运行，请点击系统托盘中的麦克风图标。")
        return 0
    if not args.smoke:
        migrate_legacy_entry()
    window = MainWindow(native=not args.smoke)
    warm(window.settings.lexicons)  # Ready before the first dictation needs it.
    try:
        HistoryStore().prune()
    except OSError:
        window.set_notice("旧历史清理失败，请在“历史”中清空或检查本地目录权限。")
    background_ready = (
        models_ready(window.settings.models, model=window.settings.asr_model)
        and bool(read_key(api_base_url=window.settings.api_base_url))
        and QSystemTrayIcon.isSystemTrayAvailable()
        and (args.smoke or window.hotkey_active())
    )
    if not args.background or not background_ready:
        window.show()
    if args.smoke:
        QTimer.singleShot(1500, window.quit_app)
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
