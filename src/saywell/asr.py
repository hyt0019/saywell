from __future__ import annotations

import itertools
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


# Audio kept either side of an utterance so its first and last sounds are whole.
PAD = 2400
# The longest piece a model is given. Past max_speech_duration the VAD only waits for a weaker
# pause, so continuous speech can run past 30 s, more than Whisper takes; such a stretch is split
# at its quietest moment, where cutting costs the recognizer least.
SEGMENT_LIMIT = 25 * SAMPLE_RATE
FRAME = SAMPLE_RATE // 50
# The VAD cuts at every pause of half a second, but a pause of a few seconds is as often someone
# thinking mid-sentence as the end of one. Recognized apart, every piece would end in a full stop;
# so pieces with no longer pause between them are recognized together, each pause in the clip cut
# down to PAUSE so that the model punctuates by meaning rather than by how long the silence was.
JOIN = 3 * SAMPLE_RATE
PAUSE = SAMPLE_RATE * 4 // 5


def vad_config(directory: Path) -> sherpa_onnx.VadModelConfig:
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(directory / "silero_vad.onnx")
    config.silero_vad.threshold = 0.35
    config.silero_vad.min_silence_duration = 0.55
    config.silero_vad.min_speech_duration = 0.15
    config.silero_vad.max_speech_duration = 18
    config.sample_rate = SAMPLE_RATE
    return config


def quietest(samples: np.ndarray, low: int, high: int) -> int:
    """The middle of the quietest 20 ms in samples[low:high]."""
    low, high = max(0, low), min(samples.size, high)
    if high - low <= FRAME:
        return (low + high) // 2
    energy = np.concatenate(([0.0], np.cumsum(np.square(samples[low:high], dtype=np.float64))))
    starts = np.arange(0, high - low - FRAME + 1, FRAME // 2)
    best = int(starts[np.argmin(energy[starts + FRAME] - energy[starts])])
    return low + best + FRAME // 2


def split_long(samples: np.ndarray, start: int, end: int, limit: int = SEGMENT_LIMIT) -> list[tuple[int, int]]:
    """A stretch of speech in pieces no longer than limit, each cut at the quietest moment in the
    second half of what it may hold. `samples` holds the stretch at the same indices."""
    pieces = []
    while end - start > limit:
        cut = quietest(samples, start + limit // 2, start + limit)
        pieces.append((start, cut))
        start = cut
    pieces.append((start, end))
    return pieces


def join_close(
    pieces: list[tuple[int, int]], join: int = JOIN, limit: int = SEGMENT_LIMIT, pause: int = PAUSE
) -> list[list[tuple[int, int]]]:
    """The pieces in groups to recognize as one: each pause within a group at most `join`, and
    the group no longer than limit once its pauses are cut down to `pause`."""
    groups: list[list[tuple[int, int]]] = []
    length = 0
    for start, end in pieces:
        if groups:
            gap = start - groups[-1][-1][1]
            grown = length + min(gap, pause) + end - start
            if gap <= join and grown <= limit:
                groups[-1].append((start, end))
                length = grown
                continue
        groups.append([(start, end)])
        length = end - start
    return groups


def shorten_pauses(clip: np.ndarray, pieces: list[tuple[int, int]], pause: int = PAUSE) -> np.ndarray:
    """The clip with each pause between pieces (given as indices into it) cut down to `pause`,
    keeping its two ends, where the sounds before and after it fade."""
    parts, position = [], 0
    for (_, end), (start, _) in itertools.pairwise(pieces):
        if start - end > pause:
            parts.append(clip[position : end + pause // 2])
            position = start - pause // 2
    if not parts:
        return clip
    parts.append(clip[position:])
    return np.concatenate(parts)


def tidy_spacing(text: str) -> str:
    """Drop spaces some models emit around Chinese punctuation and between Chinese characters."""
    return _SPACING.sub("", text)


def create_recognizer(directory: Path, model_id: str, gpu: bool = False):
    """Build the recognizer from the catalog entry: kind picks the sherpa-onnx loader,
    or the local llama.cpp server for GGUF models (on the graphics card when gpu is set)."""
    model = get_model(model_id)
    if model.kind == "llama_server":
        from .llama import LlamaServerRecognizer, server_threads

        files = {key: directory / name for key, name in model.inputs}
        return LlamaServerRecognizer(**files, threads=server_threads(), use_gpu=gpu and model.gpu)
    threads = min(4, max(1, (os.cpu_count() or 2) // 2))
    loader = getattr(sherpa_onnx.OfflineRecognizer, f"from_{model.kind}")
    files = {key: str(directory / name) for key, name in model.inputs}
    return loader(**files, **dict(model.options), num_threads=threads)


class LocalRecognizer:
    def __init__(self, directory: Path, model: str = DEFAULT_MODEL, gpu: bool = False):
        spec = get_model(model)
        if not models_ready(directory, verify=True, model=spec):
            raise ModelError(f"{spec.name} 未就绪或校验失败，请在“语音模型”中重新下载。")
        self.directory = directory
        self.model = spec.id
        # GPU acceleration only applies to models that support it; remember what was asked for
        # so turning the switch reloads the model.
        self.gpu = gpu and spec.gpu
        self.recognizer = create_recognizer(directory, spec.id, self.gpu)
        self.device = getattr(self.recognizer, "device", "")
        self.gpu_problem = getattr(self.recognizer, "gpu_problem", "")
        # Qwen3-ASR takes glossary terms as text context; sherpa-onnx models here do not.
        self.uses_context = spec.kind == "llama_server"

    def close(self) -> None:
        """Stop a model that runs in its own process; in-process models are freed with this object."""
        close = getattr(self.recognizer, "close", None)
        if close:
            close()

    def _decode(self, clip: np.ndarray, context: str = "") -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, clip)
        if context:
            stream.context = context
        self.recognizer.decode_stream(stream)
        return tidy_spacing(stream.result.text.strip())

    def recognize(self, clip: np.ndarray, vocabulary=None, cancel: Event | None = None) -> str:
        """One utterance. One that sounds like glossary or lexicon terms is heard once more with
        them as context, so the audio decides whether they fit."""
        text = self._decode(clip)
        terms = vocabulary.matches(text) if vocabulary and self.uses_context and text else []
        if terms:
            if cancel and cancel.is_set():
                raise Cancelled("已取消识别。")
            text = self._decode(clip, vocabulary.context(terms)) or text
        return text

    def transcribe(
        self,
        samples: np.ndarray,
        cancel: Event | None = None,
        partial: Callable[[str], None] | None = None,
        vocabulary=None,
    ) -> str:
        if samples.size == 0 or float(np.max(np.abs(samples))) < 0.0001:
            return ""
        config = vad_config(self.directory)
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
        pieces = [piece for start, end in segments for piece in split_long(samples, start, end)]
        groups = join_close(pieces)
        texts = []
        for index, group in enumerate(groups):
            if cancel and cancel.is_set():
                raise Cancelled("已取消识别。")
            # Add context without ever overlapping adjacent recognized groups; pieces of one
            # split stretch meet at the cut and get none across it.
            start, end = group[0][0], group[-1][1]
            previous = groups[index - 1][-1][1] if index else 0
            following = groups[index + 1][0][0] if index + 1 < len(groups) else samples.size
            left = max(0, start - PAD, (previous + start) // 2)
            right = min(samples.size, end + PAD, (following + end) // 2)
            clip = shorten_pauses(samples[left:right], [(low - left, high - left) for low, high in group])
            text = self.recognize(clip, vocabulary, cancel)
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


class SpeechStream:
    """The live counterpart of transcribe's cutting, for audio that keeps arriving: feed it chunks
    and get back what was said once it has ended, as (start, end, clip), with start and end
    counted in samples from the first chunk and the clip made the way transcribe makes it: speech
    with no pause longer than JOIN in it comes as one, its pauses shortened and both ends padded.
    Only the last `keep` samples are held, for padding and for comparing sources."""

    # Speech up to this long comes as one: less than for a recording, so that the transcript does
    # not fall far behind someone who talks on and on.
    LIMIT = 20 * SAMPLE_RATE
    # What `judge` may say of a piece of speech: leave it out, or hand it out on its own.
    DROP, ALONE = "drop", "alone"

    def __init__(self, directory: Path, keep: int = 120 * SAMPLE_RATE):
        self.config = config = vad_config(directory)
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=180)
        self.base = 0  # Where the current VAD's own count of samples starts.
        self.window = config.silero_vad.window_size
        self.keep = keep
        self.chunks: list[np.ndarray] = []
        self.first = 0  # Index of the first sample still held.
        self.total = 0  # Samples received.
        self.fed = 0  # Samples the VAD has heard; it decides as of here, however much is waiting.
        self.waiting = np.empty(0, dtype=np.float32)  # Less than a VAD window, not yet given to it.
        self.previous_end = 0  # Where the last utterance the VAD found ends.
        self.released = 0  # Where the speech in the last clip handed out ends.
        self.group: list[tuple[int, int]] = []  # Pieces of speech gathered to hand out as one.
        self.length = 0  # Their length with the pauses shortened.
        self.alone = False  # The group is a piece that must not be joined.
        # Called with each piece's start and end before it is gathered; returns DROP, ALONE or "".
        self.judge: Callable[[int, int], str] | None = None

    def feed(self, samples: np.ndarray) -> list[tuple[int, int, np.ndarray]]:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.size:
            self.chunks.append(samples)
            self.total += samples.size
            self._trim()
        waiting = np.concatenate((self.waiting, samples)) if self.waiting.size else samples
        whole = waiting.size // self.window * self.window
        found = []
        for start in range(0, whole, self.window):
            self.vad.accept_waveform(waiting[start : start + self.window])
            self.fed += self.window
            if self.group or not self.vad.empty():
                found += self._collect()
        self.waiting = waiting[whole:].copy()
        return found

    def finish(self) -> list[tuple[int, int, np.ndarray]]:
        """Whatever is still being said when the meeting stops."""
        if self.waiting.size:
            self.vad.accept_waveform(np.pad(self.waiting, (0, self.window - self.waiting.size)))
            self.waiting = np.empty(0, dtype=np.float32)
        self.vad.flush()
        return self._collect(final=True)

    def skip(self, count: int) -> list[tuple[int, int, np.ndarray]]:
        """A stretch the source never delivered (the computer slept): what was being said ends
        here, and counting goes on after the gap with a fresh VAD instead of minutes of zeros."""
        found = self.finish()
        self.total += count
        self.base = self.fed = self.first = self.previous_end = self.released = self.total
        self.chunks.clear()
        self.vad = sherpa_onnx.VoiceActivityDetector(self.config, buffer_size_in_seconds=180)
        return found

    @property
    def speaking(self) -> bool:
        return self.vad.is_speech_detected()

    @property
    def settled(self) -> int:
        """Everything said before this sample has been handed out. While someone speaks that is
        only up to the last utterance; in a pause, up to what the VAD heard, less the speech it
        may not have confirmed yet."""
        if self.group:
            return self.group[0][0]
        if self.vad.is_speech_detected():
            return self.previous_end
        unconfirmed = int(self.config.silero_vad.min_speech_duration * SAMPLE_RATE) + 2 * self.window
        return max(self.previous_end, self.fed - unconfirmed)

    def audio(self, start: int, end: int) -> np.ndarray:
        """Samples [start, end) of what is still held; parts no longer held are left out."""
        parts, position = [], self.first
        for chunk in self.chunks:
            low, high = max(start, position), min(end, position + chunk.size)
            if low < high:
                parts.append(chunk[low - position : high - position])
            position += chunk.size
            if position >= end:
                break
        return np.concatenate(parts) if parts else np.empty(0, dtype=np.float32)

    def _trim(self) -> None:
        while self.chunks and self.total - self.first - self.chunks[0].size >= self.keep:
            self.first += self.chunks.pop(0).size

    def _collect(self, final: bool = False) -> list[tuple[int, int, np.ndarray]]:
        found = []
        while not self.vad.empty():
            item = self.vad.front
            start, speech = self.base + item.start, np.asarray(item.samples, dtype=np.float32)
            self.vad.pop()
            for low, high in split_long(speech, 0, speech.size):
                found += self._gather(start + low, start + high)
            self.previous_end = start + speech.size
        # Handed out once nobody has spoken for longer than a pause that would join on. In that
        # much quiet a start the VAD has not confirmed yet lies well past the padding.
        if self.group and (final or not self.vad.is_speech_detected() and self.fed - self.group[-1][1] > JOIN):
            found.append(self._release(self.total if final else self.fed))
        return found

    def _gather(self, start: int, end: int) -> list[tuple[int, int, np.ndarray]]:
        """Add a piece of speech to the group, handing the group out first if it cannot join."""
        verdict = self.judge(start, end) if self.judge else ""
        if verdict == self.DROP:
            return []
        found = []
        if self.group:
            gap = start - self.group[-1][1]
            grown = self.length + min(gap, PAUSE) + end - start
            if (gap <= JOIN and grown <= self.LIMIT and end - self.group[0][0] <= self.keep // 2
                    and not self.alone and verdict != self.ALONE):
                self.group.append((start, end))
                self.length = grown
                return found
            found.append(self._release(start))
        self.group, self.length, self.alone = [(start, end)], end - start, verdict == self.ALONE
        return found

    def _release(self, following: int) -> tuple[int, int, np.ndarray]:
        """The group as one clip: padded up to the middle of the gaps around it, so it never
        overlaps the clips before and after it (pieces of one split stretch meet at the cut and
        get no padding across it), with its pauses shortened."""
        group, self.group = self.group, []
        start, end = group[0][0], group[-1][1]
        left = max(start - PAD, (self.released + start) // 2)
        right = min(end + PAD, (following + end) // 2)
        self.released = end
        clip = shorten_pauses(self.audio(left, right), [(low - left, high - left) for low, high in group])
        return start, end, clip
