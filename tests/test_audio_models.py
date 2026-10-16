import hashlib
from pathlib import Path
import wave
import weakref

import numpy as np
import pytest

from saywell.audio import AudioError, Recorder, decode_audio, resample_audio
from saywell.models import ModelFile, verify_file


def make_wav(path: Path, seconds: float, rate: int = 48000):
    signal = (np.sin(np.arange(int(seconds * rate)) * 2 * np.pi * 440 / rate) * 16000).astype("int16")
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(signal.tobytes())


def test_audio_resampling_preserves_duration_and_signal(tmp_path):
    path = tmp_path / "sample.wav"
    make_wav(path, 1)
    samples = decode_audio(path)
    assert samples.dtype == np.float32
    assert abs(samples.size - 16000) < 20
    assert 0.2 < np.sqrt(np.mean(samples**2)) < 0.5


def test_long_audio_rejected_without_truncating(tmp_path):
    path = tmp_path / "sample.wav"
    make_wav(path, 2)
    with pytest.raises(AudioError, match="超过"):
        decode_audio(path, max_seconds=1)


def test_missing_audio_has_actionable_error(tmp_path):
    with pytest.raises(AudioError, match="无法读取"):
        decode_audio(tmp_path / "missing.wav")


def test_recording_resampling():
    samples = np.sin(np.arange(48000) / 50).astype(np.float32)
    assert abs(resample_audio(samples, 48000).size - 16000) < 20


def test_model_hash_detects_corruption(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"abc")
    spec = ModelFile("model.onnx", "https://example.invalid", 3, hashlib.sha256(b"abc").hexdigest())
    assert verify_file(path, spec)
    path.write_bytes(b"abd")
    assert not verify_file(path, spec)


def test_token_git_blob_hash(tmp_path):
    path = tmp_path / "tokens.txt"
    path.write_bytes(b"a b c")
    spec = ModelFile(
        "tokens.txt", "https://example.invalid", 5, hashlib.sha1(b"blob 5\0a b c").hexdigest(), True
    )
    assert verify_file(path, spec)


@pytest.mark.parametrize("operation", ["stop", "cancel"])
@pytest.mark.parametrize("failure", [None, "stop", "close"])
def test_recorder_releases_buffers_even_when_device_disconnects(operation, failure):
    calls = []

    class Stream:
        def stop(self):
            calls.append("stop")
            if failure == "stop":
                raise RuntimeError("disconnected")

        abort = stop

        def close(self):
            calls.append("close")
            if failure == "close":
                raise RuntimeError("disconnected")

    recorder = Recorder()
    recorder.stream = Stream()
    recorder.chunks = [np.ones(48000, dtype=np.float32)]
    recorder.count = 48000
    recording = weakref.ref(recorder.chunks[0])
    if operation == "stop" and failure:
        with pytest.raises(RuntimeError, match="disconnected"):
            recorder.stop()
    else:
        result = getattr(recorder, operation)()
        if operation == "stop":
            assert abs(result.size - 16000) < 20
    assert calls == ["stop", "close"]
    assert recorder.stream is None
    assert recorder.chunks == []
    assert recorder.count == 0
    assert recording() is None
    # Late device callbacks cannot start retaining audio again.
    recorder._callback(np.ones((100, 1), dtype=np.float32), 100, None, None)
    assert recorder.chunks == []
