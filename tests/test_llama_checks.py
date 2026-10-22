import io
import wave
import zipfile

import httpx
import numpy as np
import pytest

from saywell import checks
from saywell.llama import SERVER, asr_text, unpack_engine, wav_bytes
from saywell.models import QWEN3_ASR, ModelFile, AsrModel, VAD


def test_qwen3_answer_keeps_only_the_transcript():
    assert asr_text("language Chinese<asr_text>你好，世界。") == "你好，世界。"
    assert asr_text("  plain text ") == "plain text"
    assert asr_text("language English<asr_text>Hi.</asr_text>") == "Hi."


def test_engine_unpacks_only_the_server_and_its_libraries_once(tmp_path):
    archive = tmp_path / "llama.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(SERVER, b"exe")
        bundle.writestr("ggml-base.dll", b"dll")
        bundle.writestr("llama-server-impl.dll", b"server code")
        bundle.writestr("llama-cli-impl.dll", b"other tool code")
        bundle.writestr("LICENSE-LLVM-OpenMP", b"license")
        bundle.writestr("llama-cli.exe", b"other tool")
        bundle.writestr("../escape.dll", b"outside")
    target = tmp_path / "engine"
    server = unpack_engine(archive, target)
    assert server == target / SERVER
    assert sorted(p.name for p in target.iterdir()) == [
        ".source", "LICENSE-LLVM-OpenMP", "escape.dll", "ggml-base.dll", "llama-server-impl.dll", SERVER
    ]
    assert not (tmp_path / "escape.dll").exists()
    (target / SERVER).write_bytes(b"kept")
    unpack_engine(archive, target)  # Already unpacked from this archive: left alone.
    assert (target / SERVER).read_bytes() == b"kept"


def test_audio_is_sent_as_16_bit_mono_wav():
    data = wav_bytes(np.array([0.0, 0.5, -1.0, 2.0], dtype=np.float32), 16000)
    with wave.open(io.BytesIO(data)) as file:
        assert (file.getnchannels(), file.getsampwidth(), file.getframerate(), file.getnframes()) == (1, 2, 16000, 4)
        assert np.frombuffer(file.readframes(4), "<i2").tolist() == [0, 16383, -32767, 32767]


def test_qwen3_runs_on_llama_cpp_with_pinned_gguf_files():
    names = {spec.name for spec in QWEN3_ASR.files}
    assert QWEN3_ASR.kind == "llama_server"
    assert {"Qwen3-ASR-0.6B-Q8_0.gguf", "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf", VAD.name} <= names
    assert dict(QWEN3_ASR.inputs)["engine"].endswith("-bin-win-cpu-x64.zip")
    assert all("/resolve/main/" not in spec.url and "/latest/" not in spec.url for spec in QWEN3_ASR.files)


def respond(status, payload=None):
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    return httpx.Response(status, json=payload or {}, request=request)


@pytest.mark.parametrize(
    ("status", "payload", "message"),
    [
        (401, {}, "API Key 不正确"),
        (402, {}, "余额不足"),
        (400, {"error": {"message": "Model Not Exist"}}, "模型名称不存在"),
        (404, {}, "地址不对"),
        (503, {}, "暂时不可用"),
    ],
)
def test_api_check_explains_failures_in_plain_words(monkeypatch, status, payload, message):
    monkeypatch.setattr(checks.httpx, "post", lambda *args, **kwargs: respond(status, payload))
    result = checks.check_api("https://api.example.com/v1", "key", "model")
    assert not result.ok and message in result.message


def test_api_check_reports_latency_and_deepseek_balance(monkeypatch):
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return respond(200, {"choices": [{"message": {"content": "好"}}]})

    monkeypatch.setattr(checks.httpx, "post", post)
    monkeypatch.setattr(checks, "fetch_balance", lambda base, key: "¥18.62")
    result = checks.check_api("https://api.deepseek.com", " key ", "deepseek-flash")
    assert result.ok and "连接正常" in result.message and "余额 ¥18.62" in result.message
    assert sent["url"] == "https://api.deepseek.com/chat/completions" and sent["auth"] == "Bearer key"
    assert sent["body"]["max_tokens"] <= 8


def test_api_check_needs_address_key_and_network(monkeypatch):
    assert "有效" in checks.check_api("not a url", "k", "m").message
    assert "API Key" in checks.check_api("https://api.example.com", " ", "m").message

    def offline(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(checks.httpx, "post", offline)
    assert "无法连接" in checks.check_api("https://api.example.com", "k", "m").message


def test_balance_only_for_deepseek(monkeypatch):
    def get(url, headers, timeout):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"balance_infos": [{"currency": "CNY", "total_balance": "18.6"}]}, request=request)

    monkeypatch.setattr(checks.httpx, "get", get)
    assert checks.fetch_balance("https://api.deepseek.com", "k") == "¥18.60"
    assert checks.fetch_balance("https://api.example.com/v1", "k") is None
    assert checks.fetch_balance("https://api.deepseek.com", "") is None


def test_file_check_finds_damaged_files(tmp_path):
    import hashlib

    good = b"good"
    spec = ModelFile("a.bin", "https://example.com/a", len(good), hashlib.sha256(good).hexdigest())
    model = AsrModel("t", "T", "x", "t", "", (), "", "", (spec,))
    (tmp_path / "a.bin").write_bytes(good)
    assert checks.check_files(model, tmp_path).ok
    (tmp_path / "a.bin").write_bytes(b"bad!")
    result = checks.check_files(model, tmp_path)
    assert not result.ok and "修复" in result.message


def test_try_model_needs_sound():
    result = checks.try_model(QWEN3_ASR, None, np.zeros(16000, dtype=np.float32))
    assert not result.ok and "没有听到声音" in result.message


class FakeSoundDevice:
    def __init__(self):
        self.stopped = False

    def rec(self, frames, samplerate, channels, dtype, device):
        return np.ones((frames, 1), dtype=np.float32)

    def stop(self):
        self.stopped = True

    def wait(self):
        pass


def test_try_recording_counts_down_and_can_finish_early(monkeypatch):
    import sys
    import threading

    fake = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    ticks = []
    clip = checks.record_clip(0.3, None, tick=ticks.append)
    assert clip.size == int(0.3 * 16000) and ticks == [1] and not fake.stopped
    stop = threading.Event()
    stop.set()
    early = checks.record_clip(10, None, stop)
    assert fake.stopped and early.size < 16000  # Only what was recorded before "说完了".
    assert checks.TRY_SECONDS == 10


def _fake_server(monkeypatch, fail_gpu=False, device="NVIDIA GeForce RTX 5080"):
    """Record which engine starts, without launching llama-server."""
    from saywell import llama

    started = []
    monkeypatch.setattr(llama, "unpack_engine", lambda archive, target: target / SERVER)

    def start(self, server, model, mmproj, threads, gpu):
        started.append((server.parent.name, gpu))
        if gpu and fail_gpu:
            raise llama.ModelError("Vulkan device lost")

    monkeypatch.setattr(llama.LlamaServerRecognizer, "_start", start)
    monkeypatch.setattr(llama.LlamaServerRecognizer, "_gpu_device", lambda self: device)
    monkeypatch.setattr(llama.LlamaServerRecognizer, "close", lambda self: None)
    return llama, started


def _recognizer(llama, tmp_path, use_gpu):
    return llama.LlamaServerRecognizer(
        tmp_path / "m.gguf", tmp_path / "p.gguf", tmp_path / "cpu.zip", 4, gpu_engine=tmp_path / "vk.zip", use_gpu=use_gpu
    )


def test_gpu_acceleration_runs_the_vulkan_engine(monkeypatch, tmp_path):
    llama, started = _fake_server(monkeypatch)
    recognizer = _recognizer(llama, tmp_path, use_gpu=True)
    assert started == [("llama.cpp-vulkan", True)]
    assert recognizer.device == "NVIDIA GeForce RTX 5080" and not recognizer.gpu_problem
    started.clear()
    _recognizer(llama, tmp_path, use_gpu=False)
    assert started == [("llama.cpp", False)]


@pytest.mark.parametrize("fail_gpu,device,problem", [(True, "GPU", "显卡驱动"), (False, "", "没有找到")])
def test_gpu_problems_fall_back_to_the_cpu_engine(monkeypatch, tmp_path, fail_gpu, device, problem):
    llama, started = _fake_server(monkeypatch, fail_gpu=fail_gpu, device=device)
    recognizer = _recognizer(llama, tmp_path, use_gpu=True)
    assert started == [("llama.cpp-vulkan", True), ("llama.cpp", False)]
    assert recognizer.device == "" and problem in recognizer.gpu_problem


def test_gpu_device_is_read_from_the_server_log(tmp_path):
    from saywell.llama import LlamaServerRecognizer

    recognizer = LlamaServerRecognizer.__new__(LlamaServerRecognizer)
    recognizer.log = tmp_path / "llama-server.log"
    # Lines as llama.cpp b11696 writes them at log level 4.
    recognizer.log.write_text(
        "llama_prepare_model_devices: using device Vulkan1 (NVIDIA GeForce RTX 5080) (0000:01:00.0) - 15205 MiB free\n"
        "load_tensors: offloaded 29/29 layers to GPU\n",
        encoding="utf-8",
    )
    assert recognizer._gpu_device() == "NVIDIA GeForce RTX 5080"
    # With no usable card it still claims the layers were offloaded, but names no device.
    recognizer.log.write_text("load_tensors: offloaded 29/29 layers to GPU\nCLIP using CPU backend\n", encoding="utf-8")
    assert recognizer._gpu_device() == ""


def test_qwen3_ships_both_engines_and_supports_gpu():
    from saywell.models import LLAMA_CPP, LLAMA_CPP_VULKAN

    assert QWEN3_ASR.gpu and LLAMA_CPP in QWEN3_ASR.files and LLAMA_CPP_VULKAN in QWEN3_ASR.files
    assert dict(QWEN3_ASR.inputs)["gpu_engine"] == LLAMA_CPP_VULKAN.name
    assert LLAMA_CPP_VULKAN.url.endswith("/b11696/llama-b11696-bin-win-vulkan-x64.zip")