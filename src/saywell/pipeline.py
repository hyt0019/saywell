from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .audio import decode_audio
from .cleanup import ApiCleaner, Cancelled, CleanupError
from .core import CleanupResult
from .models import ModelError, download_model, get_model
from .settings import Settings, read_key


class Events(QObject):
    stage = Signal(int, str)
    transcript = Signal(int, str)
    completed = Signal(int, object)
    failed = Signal(int, str)
    level = Signal(float)


class ModelDownloads(QObject):
    """Runs one model download at a time, independent of dictation jobs."""

    progress = Signal(str, int)
    finished = Signal(str)
    failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.active: str | None = None
        self.percent = 0
        self.cancel_event: threading.Event | None = None
        self.thread: threading.Thread | None = None

    def start(self, model_id: str, directory: Path) -> bool:
        if self.active:
            return False
        model = get_model(model_id)
        cancel = threading.Event()
        self.active, self.percent, self.cancel_event = model.id, 0, cancel

        def report(value: int, _name: str):
            if value != self.percent and not cancel.is_set():
                self.percent = value
                self.progress.emit(model.id, value)

        def run():
            try:
                download_model(model, directory, report, cancel)
            except Cancelled:
                message = ""
            except ModelError as exc:
                message = str(exc)
            except Exception:
                message = "模型下载失败，请检查网络或代理后重试。"
            else:
                message = None
            self.active = None
            if message is None:
                self.finished.emit(model.id)
            else:
                self.failed.emit(model.id, message)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        return True

    def cancel(self, wait: float = 0):
        if self.cancel_event:
            self.cancel_event.set()
        if wait and self.thread:
            self.thread.join(wait)


class Pipeline:
    def __init__(self, events: Events):
        self.events = events
        self.recognizer = None
        self.lock = threading.Lock()

    def release_recognizer(self) -> bool:
        """Free the loaded model unless a recognition is using it right now."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            self._close_recognizer()
            return True
        finally:
            self.lock.release()

    def _close_recognizer(self) -> None:
        current, self.recognizer = self.recognizer, None
        if current is not None and hasattr(current, "close"):
            current.close()

    def start(self, job: int, cancel: threading.Event, settings: Settings, source, kind: str):
        snapshot = Settings(**vars(settings))
        # Thread.args would retain the recording until the later API call finishes.
        # Transfer ownership out of this container as soon as the worker starts.
        pending = [source]

        def run():
            self._run(job, cancel, snapshot, pending.pop(), kind)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def _run(self, job: int, cancel: threading.Event, settings: Settings, source, kind: str):
        try:
            started = time.monotonic()
            if kind == "text":
                text = source
                duration = 0
            else:
                samples = decode_audio(Path(source)) if kind == "file" else source
                source = None
                duration = samples.size / 16000
                try:
                    self.events.stage.emit(job, "正在本地识别…")
                    with self.lock:
                        if cancel.is_set():
                            raise Cancelled("已取消。")
                        model = get_model(settings.asr_model)
                        current = self.recognizer
                        if (
                            current is None
                            or current.directory != settings.models
                            or getattr(current, "model", model.id) != model.id
                        ):
                            from .asr import LocalRecognizer

                            self._close_recognizer()  # Release the previous model before loading.
                            self.events.stage.emit(job, f"正在加载 {model.name}…")
                            self.recognizer = LocalRecognizer(settings.models, model.id)
                            self.events.stage.emit(job, "正在本地识别…")
                        text = self.recognizer.transcribe(
                            samples, cancel, lambda value: self.events.transcript.emit(job, value)
                        )
                finally:
                    samples = None  # Audio is no longer needed while waiting for the text API.
                self.events.transcript.emit(job, text)
                if not text.strip():
                    raise RuntimeError("没有检测到清晰语音，请检查麦克风或靠近一些重试。")
            asr_seconds = time.monotonic() - started
            if cancel.is_set():
                raise Cancelled("已取消。")
            self.events.stage.emit(job, "正在整理你的表达…")
            used_original = False
            cleanup_started = time.monotonic()
            try:
                result = ApiCleaner(
                    read_key(api_base_url=settings.api_base_url),
                    settings.model,
                    settings.api_timeout,
                    base_url=settings.api_base_url,
                ).clean(text, settings.glossary, cancel)
            except Cancelled:
                raise
            except CleanupError as exc:
                if kind == "text":
                    raise
                used_original = True
                result = CleanupResult(
                    text,
                    text,
                    (f"API 整理未完成，已使用识别原文。{exc}",),
                    elapsed=time.monotonic() - cleanup_started,
                )
            if cancel.is_set():
                raise Cancelled("已取消。")
            self.events.completed.emit(
                job,
                {
                    "result": result,
                    "asr_seconds": asr_seconds,
                    "duration": duration,
                    "used_original": used_original,
                },
            )
        except Cancelled:
            return
        except Exception as exc:
            if not cancel.is_set():
                from .audio import AudioError
                from .models import ModelError

                message = (
                    str(exc)
                    if isinstance(exc, (AudioError, CleanupError, ModelError, OSError))
                    else "处理未完成，请检查模型、音频文件或设备后重试。"
                )
                if isinstance(exc, RuntimeError) and str(exc).startswith("没有检测"):
                    message = str(exc)
                self.events.failed.emit(job, message)
