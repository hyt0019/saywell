import threading
import weakref
from types import SimpleNamespace

import numpy as np
import pytest

from saywell.audio import AudioError
from saywell.cleanup import Cancelled
from saywell.core import CleanupResult
from saywell.pipeline import Pipeline
from saywell.settings import Settings


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel"])
def test_recording_is_released_after_asr_and_on_failure_or_cancel(monkeypatch, outcome):
    emitted = []
    events = SimpleNamespace(**{
        name: SimpleNamespace(emit=lambda *args, name=name: emitted.append((name, args)))
        for name in ("stage", "transcript", "completed", "failed")
    })
    pipeline = Pipeline(events)
    settings = Settings()
    cancel = threading.Event()
    entered_asr = threading.Event()
    allow_asr = threading.Event()
    samples = np.ones(16000, dtype=np.float32)
    recording = weakref.ref(samples)
    api_retained_audio = []

    def transcribe(audio, cancel, partial):
        entered_asr.set()
        if not allow_asr.wait(5):
            raise RuntimeError("Test worker timed out")
        if outcome == "failure":
            raise AudioError("识别失败")
        if outcome == "cancel":
            cancel.set()
            raise Cancelled("已取消")
        return "完整识别文字。"

    def clean(text, *_args):
        api_retained_audio.append(recording() is not None)
        return CleanupResult(text, text)

    pipeline.recognizer = SimpleNamespace(directory=settings.models, transcribe=transcribe)
    monkeypatch.setattr("saywell.pipeline.read_key", lambda **kwargs: "test-key")
    monkeypatch.setattr(
        "saywell.pipeline.ApiCleaner", lambda *args, **kwargs: SimpleNamespace(clean=clean)
    )
    worker = pipeline.start(1, cancel, settings, samples, "audio")
    try:
        assert entered_asr.wait(3)
        del samples
    finally:
        allow_asr.set()
        worker.join(5)
    assert not worker.is_alive()
    assert recording() is None
    assert api_retained_audio == ([False] if outcome == "success" else [])
    assert any(name == "completed" for name, _args in emitted) == (outcome == "success")
    assert any(name == "failed" for name, _args in emitted) == (outcome == "failure")
