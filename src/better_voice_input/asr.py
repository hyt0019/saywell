from __future__ import annotations

import os
import re
from pathlib import Path
from threading import Event
from typing import Callable

import numpy as np
import sherpa_onnx

from .audio import SAMPLE_RATE
from .cleanup import Cancelled
from .models import DEFAULT_MODEL, ModelError, get_model, models_ready


_CJK_PUNCT = "\u3000-\u303f\uff00-\uffef"
_SPACING = re.compile(
    rf"\s+(?=[{_CJK_PUNCT}])|(?<=[{_CJK_PUNCT}])\s+|(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])"
)


def tidy_spacing(text: str) -> str:
    """Drop spaces some models emit around Chinese punctuation and between Chinese characters."""
    return _SPACING.sub("", text)


def create_recognizer(directory: Path, model_id: str):
    """Build the recognizer from the catalog entry: kind picks the sherpa-onnx loader."""
    model = get_model(model_id)
    threads = min(4, max(1, (os.cpu_count() or 2) // 2))
    loader = getattr(sherpa_onnx.OfflineRecognizer, f"from_{model.kind}")
    files = {key: str(directory / name) for key, name in model.inputs}
    return loader(**files, **dict(model.options), num_threads=threads)


class LocalRecognizer:
    def __init__(self, directory: Path, model: str = DEFAULT_MODEL):
        spec = get_model(model)
        if not models_ready(directory, verify=True, model=spec):
            raise ModelError(f"{spec.name} 未就绪或校验失败，请在“语音模型”中重新下载。")
        self.directory = directory
        self.model = spec.id
        self.recognizer = create_recognizer(directory, spec.id)

    def transcribe(
        self, samples: np.ndarray, cancel: Event | None = None, partial: Callable[[str], None] | None = None
    ) -> str:
        if samples.size == 0 or float(np.max(np.abs(samples))) < 0.0001:
            return ""
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(self.directory / "silero_vad.onnx")
        config.silero_vad.threshold = 0.35
        config.silero_vad.min_silence_duration = 0.55
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.max_speech_duration = 18
        config.sample_rate = SAMPLE_RATE
        vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=130)
        segments = []
        step = config.silero_vad.window_size
        for start in range(0, samples.size, step):
            if cancel and cancel.is_set():
                raise Cancelled("已取消识别。")
            window = samples[start : start + step]
            if len(window) < step:
                window = np.pad(window, (0, step - len(window)))
            vad.accept_waveform(window)
            while not vad.empty():
                item = vad.front
                segments.append((item.start, item.start + len(item.samples)))
                vad.pop()
        vad.flush()
        while not vad.empty():
            item = vad.front
            segments.append((item.start, item.start + len(item.samples)))
            vad.pop()
        texts = []
        for index, (start, end) in enumerate(segments):
            if cancel and cancel.is_set():
                raise Cancelled("已取消识别。")
            # Add context without ever overlapping adjacent recognized segments.
            previous = segments[index - 1][1] if index else 0
            following = segments[index + 1][0] if index + 1 < len(segments) else samples.size
            left = max(0, start - 2400, (previous + start) // 2)
            right = min(samples.size, end + 2400, (following + end) // 2)
            stream = self.recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples[left:right])
            self.recognizer.decode_stream(stream)
            text = tidy_spacing(stream.result.text.strip())
            if text:
                texts.append(text)
                if partial:
                    partial(join_segments(texts))
        if cancel and cancel.is_set():
            raise Cancelled("已取消识别。")
        return join_segments(texts)


def join_segments(texts: list[str]) -> str:
    joined = ""
    for text in texts:
        # English segments need a separating space; Chinese ones do not.
        if joined and joined[-1].isascii() and not joined[-1].isspace() and text[0].isascii() and text[0].isalnum():
            joined += " "
        joined += text
    return joined
