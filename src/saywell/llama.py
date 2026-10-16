"""Speech recognition with GGUF models served by a private, local llama-server process.

The recognizer mimics the small part of sherpa-onnx's OfflineRecognizer that LocalRecognizer
uses (create_stream / decode_stream / stream.result.text), so voice-activity splitting,
partial results and cancellation work the same for every engine."""

from __future__ import annotations

import base64
import io
import os
import secrets
import socket
import subprocess
import time
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np

from .models import ENGINE_DIR, ModelError
from .settings import data_dir

SERVER = "llama-server.exe"
START_TIMEOUT = 120
_job = None


def _kill_with_app(process: subprocess.Popen) -> None:
    """Put the server in a job object so Windows ends it even if the app crashes."""
    global _job
    try:
        import win32api
        import win32con
        import win32job

        if _job is None:
            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
            _job = job
        handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, process.pid)
        win32job.AssignProcessToJobObject(_job, handle)
    except Exception:  # noqa: BLE001 - close() still stops the server on a normal exit
        pass


def unpack_engine(archive: Path, target: Path) -> Path:
    """Extract llama-server and its libraries once; the archive itself stays for verification."""
    server = target / SERVER
    marker = target / ".source"
    stamp = f"{archive.name} {archive.stat().st_size}"
    if server.is_file() and marker.is_file() and marker.read_text(encoding="utf-8") == stamp:
        return server
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            name = Path(entry.filename).name
            # Only the server, the shared DLLs and their licenses (other tools' *-impl.dll are
            # skipped); entries are flattened, so nothing lands outside the folder.
            library = name.lower().endswith(".dll") and (
                not name.lower().endswith("-impl.dll") or name == "llama-server-impl.dll"
            )
            if name == SERVER or library or name.startswith("LICENSE"):
                (target / name).write_bytes(bundle.read(entry))
    marker.write_text(stamp, encoding="utf-8")
    return server


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm.tobytes())
    return buffer.getvalue()


def asr_text(content: str) -> str:
    """Qwen3-ASR answers "language Chinese<asr_text>…"; keep only the transcript."""
    if "<asr_text>" in content:
        content = content.split("<asr_text>", 1)[1]
    return content.replace("</asr_text>", "").strip()


class _Stream:
    def __init__(self):
        self.samples = np.zeros(0, dtype=np.float32)
        self.rate = 16000
        self.result = SimpleNamespace(text="")

    def accept_waveform(self, rate: int, samples: np.ndarray) -> None:
        self.rate, self.samples = rate, np.asarray(samples, dtype=np.float32)


class LlamaServerRecognizer:
    def __init__(self, model: Path, mmproj: Path, engine: Path, threads: int):
        server = unpack_engine(engine, engine.parent / ENGINE_DIR)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.key = secrets.token_urlsafe(24)
        self.url = f"http://127.0.0.1:{port}"
        log = data_dir() / "llama-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        self.log = log
        with log.open("wb") as output:
            self.process = subprocess.Popen(
                [
                    str(server), "-m", str(model), "--mmproj", str(mmproj),
                    "--host", "127.0.0.1", "--port", str(port), "--api-key", self.key,
                    "-t", str(threads), "-c", "4096", "-np", "1", "--no-webui",
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(server.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        _kill_with_app(self.process)
        self.client = httpx.Client(
            base_url=self.url, headers={"Authorization": f"Bearer {self.key}"}, timeout=httpx.Timeout(60, connect=5)
        )
        try:
            self._wait_ready()
        except BaseException:
            self.close()
            raise

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ModelError(f"语音引擎启动失败（退出码 {self.process.returncode}），详情见 {self.log}。")
            try:
                if self.client.get("/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise ModelError("语音引擎启动超时，请重试；如果一直失败，可以先换用其他模型。")

    def create_stream(self) -> _Stream:
        return _Stream()

    def decode_stream(self, stream: _Stream) -> None:
        audio = base64.b64encode(wav_bytes(stream.samples, stream.rate)).decode("ascii")
        body = {
            "messages": [
                {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}]}
            ],
            "temperature": 0,
            "max_tokens": 512,
        }
        try:
            response = self.client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            if self.process.poll() is not None:
                raise ModelError("语音引擎意外退出，请重新录音。") from None
            raise ModelError("语音引擎没有返回结果，请重新录音。") from None
        stream.result.text = asr_text(content)

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
        client = getattr(self, "client", None)
        if client:
            client.close()

    def __del__(self):
        self.close()


def server_threads() -> int:
    return min(8, max(1, (os.cpu_count() or 2) // 2))
