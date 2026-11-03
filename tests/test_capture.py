import os
import subprocess
import sys
import time

import numpy as np
import pytest

from saywell.audio import SAMPLE_RATE, resample_audio
from saywell.capture import (
    AppAudio,
    Process,
    StreamResampler,
    Timeline,
    app_root,
    audio_apps,
    group_apps,
    process_running,
)


def tree(*rows):
    """Processes from (pid, parent, exe, created) rows; every one has a path unless it is a service."""
    return {
        pid: Process(pid, parent, exe, created, "" if exe == "service.exe" else f"C:\\Apps\\{exe}")
        for pid, parent, exe, created in rows
    }


def test_app_root_climbs_to_the_app_below_the_desktop():
    processes = tree(
        (1, 0, "explorer.exe", 1),
        (10, 1, "chrome.exe", 5),
        (11, 10, "chrome.exe", 6),  # The browser's audio service plays every tab.
        (20, 1, "ms-teams.exe", 5),
        (21, 20, "msedgewebview2.exe", 6),
        (22, 21, "msedgewebview2.exe", 7),
    )
    assert app_root(11, processes) == 10
    assert app_root(22, processes) == 20


def test_app_root_stops_at_terminals_and_reused_parent_ids():
    processes = tree(
        (2, 0, "WindowsTerminal.exe", 1),
        (3, 2, "pwsh.exe", 2),
        (30, 3, "player.exe", 3),  # Started from a terminal: the terminal is not part of it.
        (40, 0, "late.exe", 90),  # Took the id of a parent that has gone.
        (41, 40, "orphan.exe", 50),
    )
    assert app_root(30, processes) == 30
    assert app_root(41, processes) == 41


def test_group_apps_puts_meeting_apps_first_even_when_silent():
    processes = tree(
        (1, 0, "explorer.exe", 1),
        (10, 1, "Spotify.exe", 2),
        (20, 1, "wemeetapp.exe", 3),
        (21, 20, "wemeetapp.exe", 4),
        (30, 1, "msedge.exe", 5),
        (40, 1, "Feishu.exe", 6),
        (50, 1, "audiodg.exe", 1),
        (60, 1, "service.exe", 1),
    )
    sessions = {10: True, 21: True, 30: False, 50: True, 60: True}
    apps = group_apps(processes, sessions)
    assert [app.name for app in apps] == ["腾讯会议", "飞书", "Spotify", "Microsoft Edge"]
    meeting, feishu, spotify, edge = apps
    assert meeting.pids == (20,) and meeting.playing and meeting.meeting
    assert not feishu.playing  # Running, in no call yet.
    assert spotify.playing and not spotify.meeting
    assert edge.browser and not edge.playing


def test_group_apps_merges_instances_and_leaves_out_saywell():
    processes = tree(
        (1, 0, "explorer.exe", 1),
        (10, 1, "chrome.exe", 9),
        (11, 1, "chrome.exe", 4),  # A second profile, started earlier.
        (12, 11, "chrome.exe", 5),
        (70, 1, "Saywell.exe", 2),
        (71, 70, "llama-server.exe", 3),
    )
    apps = group_apps(processes, {10: False, 12: True, 71: True}, own=70)
    assert [(app.name, app.pids, app.playing) for app in apps] == [("Google Chrome", (11, 10), True)]


def test_timeline_fills_silence_where_a_source_paused():
    received = []
    timeline = Timeline(received.append, started=100.0)
    timeline.deliver(np.ones(1600, dtype=np.float32), now=100.1)
    timeline.deliver(np.ones(1600, dtype=np.float32), now=100.2)  # On time: nothing added.
    timeline.deliver(np.ones(1600, dtype=np.float32), now=101.2)  # A second late.
    assert [chunk.size for chunk in received] == [1600, 1600, 14400, 1600]
    assert not received[2].any()
    assert timeline.count == int(1.2 * SAMPLE_RATE)


def test_timeline_skips_long_gaps_when_it_can():
    received, skipped = [], []
    timeline = Timeline(received.append, started=0.0, skip=skipped.append)
    timeline.deliver(np.ones(1600, dtype=np.float32), now=0.1)
    timeline.deliver(np.ones(1600, dtype=np.float32), now=60.0)  # The computer slept for a minute.
    assert [chunk.size for chunk in received] == [1600, 1600]
    assert skipped == [60 * SAMPLE_RATE - 3200]
    assert timeline.count == 60 * SAMPLE_RATE


def test_timeline_ignores_small_delivery_jitter():
    received = []
    timeline = Timeline(received.append, started=0.0)
    timeline.deliver(np.ones(1600, dtype=np.float32), now=0.35)  # 0.25 s late: within the margin.
    assert [chunk.size for chunk in received] == [1600]


def test_stream_resampler_matches_resampling_in_one_go():
    t = np.arange(48000) / 48000
    signal = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    resample = StreamResampler(48000)
    sizes = [480, 1003, 77, 2400, 9000]
    parts, start = [], 0
    while start < signal.size:
        size = sizes[len(parts) % len(sizes)]
        parts.append(resample(signal[start : start + size]))
        start += size
    streamed = np.concatenate(parts)
    whole = resample_audio(signal, 48000)
    common = min(streamed.size, whole.size) - 100
    assert abs(streamed.size - whole.size) < 40
    assert np.allclose(streamed[100:common], whole[100:common], atol=1e-5)


def test_stream_resampler_passes_16k_through():
    samples = np.arange(10, dtype=np.float32)
    out = StreamResampler(SAMPLE_RATE)(samples)
    assert np.array_equal(out, samples) and out is not samples


windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows audio")


@windows
def test_audio_apps_lists_without_saywell_itself():
    apps = audio_apps()
    assert all(os.getpid() not in app.pids for app in apps)
    assert all(app.path for app in apps)


@windows
def test_audio_apps_work_on_a_thread_that_already_runs_com():
    """Qt's main thread is a single-threaded COM apartment before the picker ever lists apps."""
    import ctypes
    import threading

    from saywell.capture import audio_sessions

    result = {}

    def run():
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        try:
            result["sessions"] = audio_sessions()
        except Exception as error:
            result["error"] = error
        finally:
            ole32.CoUninitialize()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(10)
    assert "error" not in result and isinstance(result.get("sessions"), dict)


@windows
def test_process_running_notices_an_exit():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_running(child.pid)
    finally:
        child.kill()
        child.wait()
    assert not process_running(child.pid)


@windows
def test_app_audio_runs_in_step_with_the_clock():
    """Process loopback for a silent process: it starts, and silence keeps arriving on time."""
    if sys.getwindowsversion().build < 19041:
        pytest.skip("process loopback needs Windows 10 2004")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    received = []
    capture = AppAudio(child.pid, Timeline(received.append))
    try:
        try:
            capture.start()
        except Exception as error:  # No audio service on this machine.
            pytest.skip(str(error))
        time.sleep(1.0)
    finally:
        capture.stop()
        child.kill()
        child.wait()
    total = sum(chunk.size for chunk in received)
    assert not capture.error
    assert 0.7 * SAMPLE_RATE < total < 1.4 * SAMPLE_RATE
