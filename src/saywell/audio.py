from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import av
import numpy as np

SAMPLE_RATE = 16000
MAX_SECONDS = 120


class AudioError(RuntimeError):
    pass


def decode_audio(path: Path, max_seconds: int = MAX_SECONDS) -> np.ndarray:
    chunks = []
    count = 0
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise AudioError("文件中没有音轨。")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    data = converted.to_ndarray().reshape(-1)
                    count += data.size
                    if count > max_seconds * SAMPLE_RATE:
                        raise AudioError(f"音频超过 {max_seconds} 秒，请拆成较短片段。")
                    chunks.append(data)
            for converted in resampler.resample(None):
                chunks.append(converted.to_ndarray().reshape(-1))
    except (av.error.FFmpegError, OSError):
        raise AudioError("无法读取音频，请使用 WAV、M4A、MP3 或 FLAC 文件。") from None
    return np.concatenate(chunks).astype(np.float32) if chunks else np.empty(0, dtype=np.float32)


def resample_audio(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == SAMPLE_RATE:
        return samples.astype(np.float32)
    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    frame = av.AudioFrame.from_ndarray(samples.astype(np.float32).reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = sample_rate
    frames = resampler.resample(frame) + resampler.resample(None)
    return np.concatenate([part.to_ndarray().reshape(-1) for part in frames]).astype(np.float32)


class Recorder:
    def __init__(self, level: Callable[[float], None] | None = None):
        self.level_callback = level
        self.stream = None
        self.chunks: list[np.ndarray] = []
        self.rate = 48000
        self.count = 0
        self.started = 0.0
        self.limit_reached = threading.Event()
        self.overflow = False

    def start(self, device: int | None = None):
        import sounddevice as sd

        self.chunks = []
        self.count = 0
        self.overflow = False
        self.limit_reached.clear()
        try:
            info = sd.query_devices(device, "input")
            self.rate = int(info["default_samplerate"])
            self.stream = sd.InputStream(
                device=device,
                samplerate=self.rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
                blocksize=0,
            )
            self.stream.start()
            self.started = time.monotonic()
        except (sd.PortAudioError, ValueError):
            self.cancel()
            raise AudioError("无法打开麦克风，请检查 Windows 麦克风权限及所选设备。") from None

    def _callback(self, data, frames, time_info, status):
        if self.stream is None:
            return
        if status:
            self.overflow = True
        remaining = max(0, self.rate * MAX_SECONDS - self.count)
        if remaining:
            chunk = data[:remaining, 0].copy()
            self.chunks.append(chunk)
            self.count += chunk.size
            if self.level_callback:
                self.level_callback(min(1.0, float(np.sqrt(np.mean(chunk * chunk))) * 8))
        if self.count >= self.rate * MAX_SECONDS:
            self.limit_reached.set()

    def _close_stream(self, *, abort: bool = False):
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort() if abort else stream.stop()
            finally:
                stream.close()

    def stop(self) -> np.ndarray:
        try:
            self._close_stream()
            samples = np.concatenate(self.chunks) if self.chunks else np.empty(0, dtype=np.float32)
            return resample_audio(samples, self.rate) if samples.size else samples
        finally:
            self.chunks.clear()
            self.count = 0

    def cancel(self):
        try:
            self._close_stream(abort=True)
        except Exception:
            # A disconnected device must not prevent cancellation or buffer cleanup.
            pass
        finally:
            self.chunks.clear()
            self.count = 0
