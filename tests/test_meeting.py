import threading
import time

import numpy as np
import pytest

from saywell import asr, meeting
from saywell.audio import SAMPLE_RATE
from saywell.meeting import ME, OTHERS, ROOM, SKIPPED, Line, MeetingRecorder, MeetingStore, clock, repeated

from test_segments import EnergyVad

SECOND = SAMPLE_RATE
# What each utterance "says": the fake recognizer reads the level of the speech.
WORDS = {0.3: "大家好，今天我们讨论一下预算的问题", 0.4: "我同意这个方案", 0.5: "下周三之前给出初稿"}


def recognize_level(clip):
    loud = np.abs(clip[clip != 0])
    level = round(float(np.median(loud)), 1) if loud.size else 0.0
    return WORDS.get(level, "")


def track(*spans, total):
    """(start, end, level) spans in seconds: what one source delivers."""
    samples = np.zeros(round(total * SECOND), dtype=np.float32)
    for start, end, level in spans:
        samples[round(start * SECOND) : round(end * SECOND)] = level
    return samples


class Recording:
    def __init__(self, tmp_path, recognize=recognize_level):
        self.lines, self.problems, self.done = [], [], threading.Event()
        self.store = MeetingStore.create({"started": 1_700_000_000.0, "title": "周会"}, root=tmp_path)
        self.recorder = MeetingRecorder(
            self.store, tmp_path, recognize, self.lines.append, self.problems.append, self.done.set
        )
        self.recorder.started = 0.0

    def play(self, tracks: dict[str, np.ndarray], step: float = 0.1):
        sources = {who: self.recorder._source(who, "wemeetapp.exe" if who == OTHERS else "") for who in tracks}
        self.recorder.start()
        size = round(step * SECOND)
        length = max(samples.size for samples in tracks.values())
        for start in range(0, length, size):
            for who, samples in tracks.items():
                sources[who].timeline.deliver(samples[start : start + size], now=(start + size) / SECOND)
        self.recorder.stop()
        assert self.done.wait(10)
        return self


@pytest.fixture(autouse=True)
def energy_vad(monkeypatch):
    monkeypatch.setattr(asr.sherpa_onnx, "VoiceActivityDetector", EnergyVad)


def test_repeated_measures_how_much_of_a_line_echoes_the_others():
    theirs = "大家好，今天我们讨论一下预算的问题。"
    assert repeated("大家好今天我们讨论一下预算", theirs) == 1.0
    assert repeated("大家好，今天我们讨论一下鱼酸的问题", theirs) > 0.8  # Misheard through the speakers.
    assert repeated("我觉得预算可以再压一压，主要是人力成本", theirs) < 0.3
    assert repeated("好的", "") == 0.0


def test_clock():
    assert clock(65) == "01:05" and clock(3725) == "1:02:05" and clock(-3) == "00:00"


def test_lines_come_in_with_times_and_are_saved(tmp_path):
    run = Recording(tmp_path).play({ROOM: track((1, 3, 0.3), (4, 5, 0.5), total=7)})
    assert [(line.start, line.end, line.who, line.text) for line in run.lines] == [
        (1.0, 3.0, ROOM, WORDS[0.3]),
        (4.0, 5.0, ROOM, WORDS[0.5]),
    ]
    assert run.store.lines() == run.lines
    assert run.store.info()["title"] == "周会"
    # Encrypted on disk: no text readable in the files.
    assert all("预算" not in path.read_text(encoding="ascii") for path in run.store.folder.iterdir())


def test_the_microphone_hearing_the_speakers_is_dropped_and_the_user_kept(tmp_path):
    others = track((1, 3, 0.3), total=8)
    mine = track((1.15, 3.15, 0.3), (5, 6, 0.4), total=8)  # Echo of the meeting, then the user.
    run = Recording(tmp_path).play({ME: mine, OTHERS: others})
    assert [(line.who, line.text) for line in run.lines] == [(OTHERS, WORDS[0.3]), (ME, WORDS[0.4])]


def test_a_line_of_mine_waits_for_the_others_still_speaking(tmp_path):
    # The others speak on past the end of the echo; it must not slip through before they finish.
    others = track((1, 6, 0.3), total=9)
    mine = track((1.1, 2.5, 0.3), total=9)
    run = Recording(tmp_path).play({ME: mine, OTHERS: others})
    assert [line.who for line in run.lines] == [OTHERS]


def test_falling_far_behind_drops_the_oldest_speech(tmp_path, monkeypatch):
    monkeypatch.setattr(meeting, "MAX_BACKLOG", 3 * SECOND)
    monkeypatch.setattr(meeting, "SLOW_BACKLOG", 2 * SECOND)
    release = threading.Event()

    def slow(clip):
        release.wait(5)
        return recognize_level(clip)

    run = Recording(tmp_path, slow)
    sources = {ROOM: track((1, 2, 0.3), (3, 4, 0.4), (5, 6, 0.5), (7, 8, 0.5), total=10)}
    player = threading.Thread(target=run.play, args=(sources,))
    player.start()
    time.sleep(0.5)
    release.set()
    player.join(10)
    whos = [line.who for line in run.lines]
    assert SKIPPED in whos and whos.count(ROOM) >= 2
    assert any("跟不上" in problem for problem in run.problems)


def test_recognition_that_keeps_failing_stops_the_meeting(tmp_path):
    def broken(clip):
        raise RuntimeError("引擎没有响应")

    run = Recording(tmp_path, broken).play({ROOM: track((1, 2, 0.3), (3, 4, 0.3), (5, 6, 0.3), total=8)})
    assert run.lines == []
    assert run.problems[0] == "识别出错：引擎没有响应"
    assert "已停止记录" in run.problems[-1]
    assert run.recorder.stopping.is_set()


def test_store_reads_lines_in_time_order_and_skips_damaged_ones(tmp_path):
    store = MeetingStore.create({"started": 1_700_000_000.0}, root=tmp_path)
    store.append(Line(5.0, 6.0, OTHERS, "后说的"))
    store.append(Line(1.0, 2.0, ME, "先说的"))
    with open(store.folder / "transcript.dat", "a", encoding="ascii") as file:
        file.write("not-a-line\n")
    assert [line.text for line in store.lines()] == ["先说的", "后说的"]
    again = MeetingStore.create({"started": 1_700_000_000.0}, root=tmp_path)
    assert again.folder != store.folder  # Same second: a second folder, not a shared one.


def test_trouble_while_listening_ends_the_meeting_instead_of_hanging(tmp_path, monkeypatch):
    def broken(self, source):
        raise ValueError("bad chunk")

    monkeypatch.setattr(MeetingRecorder, "_drain", broken)
    run = Recording(tmp_path).play({ROOM: track((1, 2, 0.3), total=3)})
    assert run.recorder.finished
    assert "已停止记录" in run.problems[-1]


def test_room_noise_the_vad_took_for_speech_is_not_recognized(tmp_path):
    heard = []

    def recognize(clip):
        heard.append(clip.size)
        return recognize_level(clip)

    faint = track((1, 2, 0.002), total=5)  # -54 dB: the room after the speakers stopped.
    run = Recording(tmp_path, recognize).play({ROOM: faint + track((3, 3.7, 0.3), total=5)})
    assert [line.text for line in run.lines] == [WORDS[0.3]] and len(heard) == 1


def test_meetings_are_listed_newest_first(tmp_path):
    from saywell.meeting import list_meetings

    assert list_meetings(tmp_path / "none") == []
    older = MeetingStore.create({"started": 1_700_000_000.0}, root=tmp_path)
    newer = MeetingStore.create({"started": 1_700_090_000.0}, root=tmp_path)
    (tmp_path / "not-a-meeting").mkdir()
    assert [store.folder for store in list_meetings(tmp_path)] == [newer.folder, older.folder]
