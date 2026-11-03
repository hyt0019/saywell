from itertools import pairwise
from types import SimpleNamespace

import numpy as np

from saywell import asr
from saywell.asr import PAD, SEGMENT_LIMIT, SpeechStream, quietest, split_long
from saywell.audio import SAMPLE_RATE

SECOND = SAMPLE_RATE


class EnergyVad:
    """Speech is any sample that is not zero; an utterance ends after SILENCE seconds of zeros."""

    SILENCE = 0.55

    def __init__(self, config, buffer_size_in_seconds):
        self.audio, self.items = [], []
        self.position, self.start, self.last = 0, None, 0

    def accept_waveform(self, window):
        window = np.asarray(window)
        self.audio.append(window)
        loud = np.flatnonzero(window)
        if loud.size:
            if self.start is None:
                self.start = self.position + int(loud[0])
            self.last = self.position + int(loud[-1]) + 1
        self.position += window.size
        if self.start is not None and self.position - self.last >= int(self.SILENCE * SECOND):
            self._emit()

    def _emit(self):
        heard = np.concatenate(self.audio)
        self.items.append(SimpleNamespace(start=self.start, samples=heard[self.start : self.last]))
        self.start = None

    def flush(self):
        if self.start is not None:
            self._emit()

    def is_speech_detected(self):
        return self.start is not None

    def empty(self):
        return not self.items

    @property
    def front(self):
        return self.items[0]

    def pop(self):
        self.items.pop(0)


def speech(*spans, total):
    """Noise where people speak, zeros elsewhere; spans in seconds."""
    rng = np.random.default_rng(3)
    samples = np.zeros(round(total * SECOND), dtype=np.float32)
    for start, end in spans:
        low, high = round(start * SECOND), round(end * SECOND)
        samples[low:high] = rng.uniform(0.2, 0.5, high - low)
    return samples


def run(stream, samples, sizes=(160,)):
    found, start, index = [], 0, 0
    while start < samples.size:
        size = sizes[index % len(sizes)]
        found += stream.feed(samples[start : start + size])
        start, index = start + size, index + 1
    return found + stream.finish()


def test_quietest_finds_the_pause():
    samples = np.full(4 * SECOND, 0.3, dtype=np.float32)
    samples[int(2.5 * SECOND) : int(2.53 * SECOND)] = 0.001
    cut = quietest(samples, SECOND, 4 * SECOND)
    assert int(2.5 * SECOND) <= cut <= int(2.53 * SECOND)


def test_split_long_cuts_long_speech_at_its_quiet_moments():
    samples = np.full(60 * SECOND, 0.3, dtype=np.float32)
    for pause in (19, 41):
        samples[pause * SECOND : pause * SECOND + 400] = 0.0
    pieces = split_long(samples, 0, samples.size)
    assert len(pieces) == 3
    assert pieces[0][0] == 0 and pieces[-1][1] == samples.size
    assert all(left[1] == right[0] for left, right in pairwise(pieces))
    assert all(end - start <= SEGMENT_LIMIT for start, end in pieces)
    assert abs(pieces[0][1] - 19 * SECOND) < 400 and abs(pieces[1][1] - 41 * SECOND) < 400
    assert split_long(samples, 0, 10 * SECOND) == [(0, 10 * SECOND)]


def test_transcribe_gives_the_model_no_piece_longer_than_the_limit(monkeypatch, tmp_path):
    lengths = []

    class Recognizer:
        def create_stream(self):
            return SimpleNamespace(
                accept_waveform=lambda _rate, clip: lengths.append(len(clip)), result=SimpleNamespace(text="好")
            )

        def decode_stream(self, stream):
            pass

    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", EnergyVad)
    recognizer = asr.LocalRecognizer.__new__(asr.LocalRecognizer)
    recognizer.directory, recognizer.recognizer, recognizer.uses_context = tmp_path, Recognizer(), False
    assert recognizer.transcribe(speech((1, 41), total=42)) == "好好"
    assert len(lengths) == 2 and max(lengths) <= SEGMENT_LIMIT + PAD


def test_speech_stream_cuts_and_pads_like_a_whole_recording(monkeypatch, tmp_path):
    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", EnergyVad)
    samples = speech((1, 3), (4, 5.2), (7, 7.5), total=8)
    found = run(SpeechStream(tmp_path), samples, sizes=(160, 1000, 37, 4096))
    assert [(start / SECOND, end / SECOND) for start, end, _ in found] == [(1, 3), (4, 5.2), (7, 7.5)]
    for start, end, clip in found:
        assert clip.size == end - start + 2 * PAD
        assert np.array_equal(clip, samples[start - PAD : end + PAD])
    # The same however the audio arrives.
    again = run(SpeechStream(tmp_path), samples, sizes=(samples.size,))
    assert all(np.array_equal(a[2], b[2]) for a, b in zip(found, again, strict=True))


def test_speech_stream_never_pads_into_the_next_utterance(monkeypatch, tmp_path):
    """After a forced cut the VAD may wait only briefly, so the next utterance can follow closely."""
    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", EnergyVad)
    monkeypatch.setattr(EnergyVad, "SILENCE", 0.05)
    # The next one ends before the first's padding is decided: pad to the middle of the gap.
    first, second = run(SpeechStream(tmp_path), speech((0.5, 1.5), (1.6, 1.8), total=3))
    assert first[2].size == round(1.2 * SECOND)  # 0.35 s to 1.55 s
    assert second[2].size == round(0.4 * SECOND)  # 1.55 s to 1.95 s
    # The next one is still going when the first has waited long enough: no padding after it.
    first, second = run(SpeechStream(tmp_path), speech((0.5, 1.5), (1.6, 3.0), total=4))
    assert first[2].size == round(1.15 * SECOND)
    assert second[0] == round(1.6 * SECOND)


def test_speech_stream_splits_long_speech_and_forgets_old_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", EnergyVad)
    samples = speech((1, 61), total=62)
    stream = SpeechStream(tmp_path, keep=30 * SECOND)
    found = run(stream, samples, sizes=(1600,))
    assert len(found) == 3
    assert all(end - start <= SEGMENT_LIMIT for start, end, _ in found)
    assert all(a[1] == b[0] for a, b in pairwise(found))
    assert stream.total == samples.size
    assert stream.total - stream.first <= 30 * SECOND + 1600
    assert stream.audio(0, SECOND).size == 0  # Long gone.
    assert np.array_equal(stream.audio(stream.total - 100, stream.total), samples[-100:])


def test_speech_stream_carries_on_after_a_skipped_gap(monkeypatch, tmp_path):
    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", EnergyVad)
    stream = SpeechStream(tmp_path)
    found, before = [], speech((1, 2), total=2.0)
    for start in range(0, 2 * SECOND, 160):
        found += stream.feed(before[start : start + 160])
    found += stream.skip(3600 * SECOND)  # An hour asleep, mid-sentence.
    later = speech((0.5, 1.5), total=3)
    for start in range(0, later.size, 160):
        found += stream.feed(later[start : start + 160])
    found += stream.finish()
    assert [(round(start / SECOND, 2), round(end / SECOND, 2)) for start, end, _ in found] == [
        (1.0, 2.0), (3602.5, 3603.5)
    ]
    assert found[1][2].size == SECOND + 2 * PAD
