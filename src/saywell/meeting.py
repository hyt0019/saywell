"""Meetings: the chosen sources transcribed live, the transcript written to disk as it grows.

Audio lives only until it is recognized: each source is cut at its longer pauses
(asr.SpeechStream), the pieces wait in a bounded queue and are recognized one at a time, oldest
first. Only text is saved, encrypted for the current Windows user like the history.
"""

from __future__ import annotations

import base64
import heapq
import itertools
import json
import queue
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .audio import SAMPLE_RATE, AudioError
from .settings import data_dir

# Who said a line. With the microphone and an app both recorded, the microphone is "me" and the
# app the other participants; a microphone alone hears everyone in the room.
ME, OTHERS, ROOM = "me", "others", "room"
# A stretch recognition could not keep up with and had to drop.
SKIPPED = "skipped"
LABELS = {ME: "我", OTHERS: "其他参会人"}

# How far recognition may fall behind before the oldest waiting speech is dropped, and when to
# say it is falling behind.
MAX_BACKLOG = 10 * 60 * SAMPLE_RATE
SLOW_BACKLOG = 60 * SAMPLE_RATE
# The microphone hearing the meeting from the speakers: a line of "mine" that mostly repeats what
# the others said around the same time is that echo. Loudness alone cannot tell it apart from the
# user talking over them, which must never be lost, so the recognized text decides.
ECHO = 0.6
ECHO_MARGIN = 2.0
# A microphone "utterance" whose loudest 100 ms stays below this is room noise the VAD took for
# speech, on which a model makes up words; speech, even quiet, is 20 dB and more above it. Not
# applied to an app, whose level follows its volume slider.
QUIET = -50.0
# Speech on the microphone while the app played sound for at least this share of it may be its
# echo. Recognized on its own, so that it is dropped (or kept) whole, never joined to the user's
# own words before or after it.
OVERLAP = 0.5


@dataclass(frozen=True)
class Line:
    start: float  # Seconds since the meeting began.
    end: float
    who: str
    text: str


def clock(seconds: float) -> str:
    """00:12:05, or 1:02:05 past the hour."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60:02d}:{rest % 60:02d}"


# --- Paragraphs --------------------------------------------------------------------------------

# The transcript is read in paragraphs, each one speaker's words. A new one starts when someone
# else speaks, after a silence this long, or once the paragraph has gone on for TURN seconds, so
# that a time is never far away.
TURN = 60.0
SILENCE = 10.0


@dataclass(frozen=True)
class Paragraph:
    start: float
    who: str
    text: str
    # The lines it holds, as indices into the list it was made from.
    first: int = field(default=0, compare=False)
    last: int = field(default=0, compare=False)


def skipped_text(line: Line) -> str:
    return f"（约 {max(1, round(line.end - line.start))} 秒没来得及识别）"


def paragraphs(lines: list[Line], labels: bool) -> list[Paragraph]:
    """The lines, in time order, as paragraphs. Without labels (a room) all speakers are one; a
    stretch that could not be recognized stands on its own."""
    result: list[Paragraph] = []
    previous: Line | None = None
    for index, line in enumerate(lines):
        if line.who == SKIPPED:
            result.append(Paragraph(line.start, SKIPPED, skipped_text(line), index, index))
            previous = None
        elif (
            previous is not None
            and (line.who == previous.who or not labels)
            and line.start - previous.end < SILENCE
            and line.start - result[-1].start < TURN
        ):
            last = result[-1]
            result[-1] = Paragraph(last.start, last.who, run_on(last.text, line.text), last.first, index)
            previous = line
        else:
            result.append(Paragraph(line.start, line.who, line.text, index, index))
            previous = line
    return result


def run_on(text: str, more: str) -> str:
    """Two pieces of speech as one: English needs a space between them, Chinese does not."""
    if text and more and text[-1].isascii() and text[-1].isalnum() and more[0].isascii() and more[0].isalnum():
        return f"{text} {more}"
    return text + more


# --- Echo --------------------------------------------------------------------------------------


def plain(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def repeated(mine: str, theirs: str) -> float:
    """How much of `mine` also appears in `theirs`, from 0 to 1, in runs of two or more characters
    so scattered single characters do not count."""
    mine, theirs = plain(mine), plain(theirs)
    if not mine or not theirs:
        return 0.0
    blocks = SequenceMatcher(None, mine, theirs, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks if block.size >= 2) / len(mine)


def peak_level(samples: np.ndarray) -> float:
    """The loudest 100 ms, in dB below full scale."""
    frame = SAMPLE_RATE // 10
    if samples.size < frame:
        return float(20 * np.log10(np.sqrt(np.mean(np.square(samples, dtype=np.float64))) + 1e-9)) if samples.size else -200.0
    frames = samples[: samples.size // frame * frame].reshape(-1, frame)
    return float(20 * np.log10(np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1)).max() + 1e-9))


# --- Storage -----------------------------------------------------------------------------------


def seal(value) -> str:
    import win32crypt

    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(win32crypt.CryptProtectData(data, "Saywell meeting", None, None, None, 1)).decode("ascii")


def unseal(text: str):
    import win32crypt

    _, data = win32crypt.CryptUnprotectData(base64.b64decode(text), None, None, None, 1)
    return json.loads(data.decode("utf-8"))


def meetings_dir() -> Path:
    return data_dir() / "meetings"


class MeetingStore:
    """One meeting's folder: info.dat (title, times, sources), transcript.dat with one encrypted
    line per recognized utterance, appended as the meeting goes, and minutes.dat and parts.dat
    once minutes are written. Everything is encrypted for the current Windows user."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.lock = threading.Lock()

    @classmethod
    def create(cls, info: dict, root: Path | None = None) -> MeetingStore:
        root = root or meetings_dir()
        name = datetime.fromtimestamp(info["started"]).strftime("%Y%m%d-%H%M%S")
        folder = root / name
        for number in itertools.count(2):
            if not folder.exists():
                break
            folder = root / f"{name}-{number}"
        folder.mkdir(parents=True)
        store = cls(folder)
        store.save_info(info)
        return store

    @property
    def id(self) -> str:
        return self.folder.name

    def append(self, line: Line) -> None:
        with self.lock, open(self.folder / "transcript.dat", "a", encoding="ascii") as file:
            file.write(seal(asdict(line)) + "\n")

    def replace_lines(self, lines: list[Line]) -> None:
        """The whole transcript, written anew: after the user edited it."""
        rows = "".join(seal(asdict(line)) + "\n" for line in lines)
        with self.lock:
            temp = self.folder / "transcript.tmp"
            temp.write_text(rows, encoding="ascii")
            temp.replace(self.folder / "transcript.dat")

    def lines(self) -> list[Line]:
        try:
            rows = (self.folder / "transcript.dat").read_text(encoding="ascii").splitlines()
        except OSError:
            return []
        lines = []
        for row in rows:
            try:
                lines.append(Line(**unseal(row)))
            except Exception:
                continue  # A line cut short when the computer lost power.
        return sorted(lines, key=lambda line: line.start)

    def save_info(self, info: dict) -> None:
        self.save("info", info)

    def info(self) -> dict:
        return self.load("info") or {}

    def save(self, name: str, value) -> None:
        """Keep `value` (anything JSON can hold) encrypted as <name>.dat: minutes, cached notes."""
        with self.lock:
            temp = self.folder / f"{name}.tmp"
            temp.write_text(seal(value), encoding="ascii")
            temp.replace(self.folder / f"{name}.dat")

    def load(self, name: str):
        try:
            return unseal((self.folder / f"{name}.dat").read_text(encoding="ascii"))
        except Exception:
            return None


def list_meetings(root: Path | None = None) -> list[MeetingStore]:
    """The meetings kept on this computer, newest first."""
    root = root or meetings_dir()
    try:
        folders = [folder for folder in root.iterdir() if (folder / "info.dat").is_file()]
    except OSError:
        return []
    # Folders are named after the start time, so their names sort by it.
    return [MeetingStore(folder) for folder in sorted(folders, key=lambda folder: folder.name, reverse=True)]


# --- Recording ---------------------------------------------------------------------------------


@dataclass
class Source:
    who: str
    stream: object  # asr.SpeechStream
    timeline: object  # capture.Timeline
    capture: object = None  # capture.MicAudio or capture.AppAudio
    chunks: queue.SimpleQueue = field(default_factory=queue.SimpleQueue)
    level: float = 0.0
    # Any sound at all so far; an app muted in the volume mixer records as silence.
    heard: bool = False
    # Someone is speaking, or what was said is still being gathered (set by the listener thread,
    # which alone may touch the VAD).
    hearing: bool = False
    # For an app: its executable, to find it again after it was restarted, and whether it is gone.
    exe: str = ""
    gone: bool = False
    # Everything said before this sample has been handed out (see SpeechStream.settled).
    settled: int = 0


@dataclass(order=True)
class Waiting:
    start: int
    order: int
    end: int = field(compare=False)
    who: str = field(compare=False)
    clip: np.ndarray = field(compare=False, repr=False)


class MeetingRecorder:
    """Runs a meeting. Captures feed each source's queue; a listener thread cuts them into
    utterances (dropping the microphone's echo of the app); a recognizer thread turns the oldest
    waiting utterance into a Line, saves it and hands it to `on_line`. `on_problem` hears about
    trouble worth telling the user, `on_finished` when the last line is in after stop()."""

    def __init__(
        self,
        store: MeetingStore,
        vad_directory: Path,
        recognize: Callable[[np.ndarray], str],
        on_line: Callable[[Line], None],
        on_problem: Callable[[str], None] = lambda message: None,
        on_finished: Callable[[], None] = lambda: None,
    ):
        self.store = store
        self.vad_directory = vad_directory
        self.recognize = recognize
        self.on_line = on_line
        self.on_problem = on_problem
        self.on_finished = on_finished
        self.sources: list[Source] = []
        self.started = time.monotonic()
        self.waiting: list[Waiting] = []
        self.waiting_samples = 0
        self.order = itertools.count()
        self.condition = threading.Condition()
        self.stopping = threading.Event()
        self.listened = threading.Event()
        self.threads: list[threading.Thread] = []
        self.slow = False
        self.finished = False
        # Lines of mine wait here until the others' speech from the same moment is recognized,
        # to compare against it; the others' recent lines are kept for that.
        self.mine: list[Line] = []
        self.theirs: deque[Line] = deque(maxlen=200)
        # Quitting mid-meeting: recognize no more of what is waiting.
        self.abandoned = False
        self.recognizing = False

    # Sources

    def _source(self, who: str, exe: str = "") -> Source:
        from .asr import SpeechStream
        from .capture import Timeline

        chunks: queue.SimpleQueue = queue.SimpleQueue()
        timeline = Timeline(chunks.put, self.started, skip=chunks.put)
        source = Source(who, SpeechStream(self.vad_directory), timeline, chunks=chunks, exe=exe)
        source.stream.judge = lambda start, end: self._judge(source, start, end)
        self.sources.append(source)
        return source

    def _judge(self, source: Source, start: int, end: int) -> str:
        """Room noise on the microphone is left out; speech on it that may be the app's echo is
        recognized on its own (see SpeechStream.judge)."""
        if source.who not in (ME, ROOM):
            return ""
        if peak_level(source.stream.audio(start, end)) < QUIET:
            return source.stream.DROP
        if source.who == ME and self._app_sounding(start, end) >= OVERLAP:
            return source.stream.ALONE
        return ""

    def _app_sounding(self, start: int, end: int) -> float:
        """The share of 100 ms frames in [start, end) in which a recorded app played sound."""
        frame = SAMPLE_RATE // 10
        count = max(1, (end - start) // frame)
        sounding = np.zeros(count, dtype=bool)
        for other in self.sources:
            if other.who != OTHERS:
                continue
            audio = other.stream.audio(start, start + count * frame)
            frames = audio[: audio.size // frame * frame].reshape(-1, frame)
            if frames.size:
                levels = 20 * np.log10(np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1)) + 1e-9)
                sounding[: levels.size] |= levels > QUIET
        return float(sounding.mean())

    def add_microphone(self, device: int | None, who: str) -> None:
        from .capture import MicAudio

        source = self._source(who)
        source.capture = MicAudio(source.timeline, device)
        source.capture.start()

    def add_app(self, pids: tuple[int, ...], exe: str) -> None:
        """Every root of the app is its own source, so their audio never needs mixing."""
        from .capture import AppAudio

        for pid in pids:
            source = self._source(OTHERS, exe)
            source.capture = AppAudio(pid, source.timeline)
            source.capture.start()

    def start(self) -> None:
        for target, name in ((self._listen, "meeting-listen"), (self._recognize_all, "meeting-recognize")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self) -> None:
        """Stop recording; what was already heard is still recognized, then on_finished."""
        for source in self.sources:
            if source.capture is not None:
                source.capture.stop()
        self.stopping.set()

    def close(self, timeout: float = 5.0) -> None:
        """Stop and wait for the threads, as when quitting; speech still waiting is not recognized."""
        self.abandoned = True
        self.stop()
        with self.condition:
            self.condition.notify_all()
        for thread in self.threads:
            thread.join(timeout)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def backlog(self) -> float:
        """Seconds of speech waiting to be recognized."""
        return self.waiting_samples / SAMPLE_RATE

    def level(self, who: str) -> float:
        return max((source.level for source in self.sources if source.who == who), default=0.0)

    def heard(self, who: str) -> bool:
        return any(source.heard for source in self.sources if source.who == who)

    @property
    def pending(self) -> bool:
        """Words are on their way: being said, waiting to be recognized, or being recognized."""
        return self.recognizing or bool(self.waiting) or any(source.hearing for source in self.sources)

    # Listening

    def _listen(self) -> None:
        watched = time.monotonic()
        try:
            while True:
                stopping = self.stopping.is_set()
                # The app first: whether speech on the microphone may be its echo is judged by
                # the app's sound at the same moment (see _judge).
                ordered = sorted(self.sources, key=lambda source: source.who == ME)
                for source in ordered:
                    self._drain(source)
                if stopping:
                    break
                if time.monotonic() - watched > 3:
                    watched = time.monotonic()
                    try:
                        self._watch_apps()
                    except Exception:
                        pass  # Only a convenience; the meeting goes on without it.
                time.sleep(0.02)
            for source in ordered:
                for found in source.stream.finish():
                    self._heard(source, *found)
                source.hearing = False
                source.settled = source.stream.total
        except Exception:
            # Never leave the recognizer waiting: stop, and keep what was already recognized.
            self.on_problem("处理声音时出错，已停止记录。已经记下的文字都保留着。")
            self.stop()
        finally:
            self.listened.set()
            with self.condition:
                self.condition.notify_all()

    def _drain(self, source: Source) -> None:
        loudest = 0.0
        while True:
            try:
                item = source.chunks.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, int):
                found = source.stream.skip(item)
            else:
                if item.size:
                    loudest = max(loudest, float(np.sqrt(np.mean(np.square(item)))))
                found = source.stream.feed(item)
            for utterance in found:
                self._heard(source, *utterance)
        source.settled = source.stream.settled
        # Shown as a meter: jumps up, falls back gently.
        source.level = max(min(1.0, loudest * 8), source.level * 0.7)
        source.heard = source.heard or loudest > 1e-4
        source.hearing = source.stream.speaking or bool(source.stream.group)

    def _heard(self, source: Source, start: int, end: int, clip: np.ndarray) -> None:
        with self.condition:
            heapq.heappush(self.waiting, Waiting(start, next(self.order), end, source.who, clip))
            self.waiting_samples += clip.size
            dropped = []
            while self.waiting_samples > MAX_BACKLOG and len(self.waiting) > 1:
                oldest = heapq.heappop(self.waiting)
                self.waiting_samples -= oldest.clip.size
                dropped.append(oldest)
            slow = self.waiting_samples > SLOW_BACKLOG
            self.condition.notify()
        if dropped:
            line = Line(dropped[0].start / SAMPLE_RATE, max(item.end for item in dropped) / SAMPLE_RATE, SKIPPED, "")
            self._publish(line)
        if slow and not self.slow:
            self.on_problem("识别跟不上说话的速度，文字会晚一些出来。可以在“语音模型”里换更快的模型，或开启显卡加速。")
        self.slow = slow

    def _watch_apps(self) -> None:
        """An app that quit leaves its capture recording silence. Once all of it is gone, say so;
        when it runs again (the meeting app restarted for a new call) carry on with the new
        process on the same timeline."""
        from .capture import AppAudio, app_root, process_running, processes

        apps = [source for source in self.sources if source.exe and source.capture is not None]
        if not apps:
            return
        dead = [source for source in apps if not process_running(source.capture.pid)]
        if not dead:
            return
        running = processes()
        exe = apps[0].exe.lower()
        captured = {source.capture.pid for source in apps if source not in dead}
        roots = sorted({app_root(pid, running) for pid, process in running.items() if process.exe.lower() == exe} - captured)
        for source in dead:
            if not roots:
                break
            replacement = AppAudio(roots.pop(0), source.timeline)
            try:
                replacement.start()
            except AudioError:
                continue
            source.capture.stop()
            source.capture = replacement
        gone = all(not process_running(source.capture.pid) for source in apps)
        if gone and not apps[0].gone:
            self.on_problem("所选应用已关闭。重新打开后会接着记录它的声音，麦克风照常记录。")
        elif not gone and apps[0].gone:
            self.on_problem("")
        for source in apps:
            source.gone = gone

    # Recognizing

    def _recognize_all(self) -> None:
        failures = 0
        while True:
            with self.condition:
                while not self.waiting and not self.listened.is_set() and not self.abandoned:
                    self.condition.wait(0.5)
                    self._compare_mine()
                if not self.waiting or self.abandoned:
                    break
                item = heapq.heappop(self.waiting)
                self.waiting_samples -= item.clip.size
            self.recognizing = True
            try:
                text = self.recognize(item.clip).strip()
                failures = 0
            except Exception as error:  # A model that failed to load or a crashed engine.
                failures += 1
                if failures == 1:
                    self.on_problem(f"识别出错：{error}" if str(error) else "识别出错，正在重试。")
                if failures == 3:
                    self.on_problem("识别连续出错，已停止记录。已经记下的文字都保留着。")
                    self.stop()
                continue
            finally:
                item.clip = None
                self.recognizing = False
            if text:
                line = Line(item.start / SAMPLE_RATE, item.end / SAMPLE_RATE, item.who, text)
                if item.who == ME:
                    self.mine.append(line)
                else:
                    if item.who == OTHERS:
                        self.theirs.append(line)
                    self._publish(line)
            with self.condition:
                self._compare_mine()
        with self.condition:
            self._compare_mine(final=True)
        self.finished = True
        self.on_finished()

    def _compare_mine(self, final: bool = False) -> None:
        """Publish each waiting line of mine once the others' speech up to its end is recognized,
        unless it only repeats them. Called with the condition held."""
        # An app whose capture broke off delivers nothing more; it cannot hold anything up.
        others = [
            source for source in self.sources
            if source.who == OTHERS and getattr(source.capture, "running", True)
        ]
        for line in list(self.mine):
            end = int(line.end * SAMPLE_RATE)
            if not final and (
                any(source.settled < end for source in others)
                or any(item.who == OTHERS and item.start < end for item in self.waiting)
            ):
                continue
            self.mine.remove(line)
            around = "".join(
                other.text for other in self.theirs
                if other.end >= line.start - ECHO_MARGIN and other.start <= line.end + ECHO_MARGIN
            )
            if repeated(line.text, around) < ECHO:
                self._publish(line)

    def _publish(self, line: Line) -> None:
        try:
            self.store.append(line)
        except OSError:
            self.on_problem("文字没能写入磁盘，请检查磁盘空间。")
        self.on_line(line)
