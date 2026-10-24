import hashlib
import json
import os
import threading
from dataclasses import replace
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from saywell import models, settings as settings_module, ui
from saywell.asr import join_segments, tidy_spacing
from saywell.cleanup import Cancelled
from saywell.models import ALL_MODELS, CATALOG, SENSE_VOICE, VAD, AsrModel, ModelFile, download_model, get_model
from saywell.pipeline import ModelDownloads, Pipeline
from saywell.settings import Settings
from saywell.ui import STYLE, ModelDialog


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    yield app
    app.clipboard().clear()


def test_catalog_keeps_sensevoice_in_place_and_every_model_is_self_contained():
    assert [model.id for model in CATALOG] == ["sensevoice", "xasr", "qwen3-asr", "canary"]
    assert SENSE_VOICE.folder == "" and Settings().asr_model == "sensevoice"
    assert models.FILES == SENSE_VOICE.files
    folders = [model.folder for model in CATALOG]
    assert len(set(folders)) == len(folders)
    for model in CATALOG:
        assert VAD in model.files
        assert len(model.strengths) == 3 and model.tagline and model.caveat
        assert all(spec.url.startswith("https://") and spec.size > 0 for spec in model.files)
        # Pinned revisions keep the verified digests valid.
        assert all("/resolve/main/" not in spec.url for spec in model.files)
    assert get_model("missing") is SENSE_VOICE


def test_changing_download_location_keeps_downloaded_models_in_place(tmp_path, monkeypatch):
    old, new = tmp_path / "old", tmp_path / "new"
    monkeypatch.setattr(settings_module, "models_ready", lambda directory, model: model.id in ("sensevoice", "xasr"))
    value = Settings(model_dir=str(old), asr_model="xasr")
    assert value.models == old / "x-asr-zh-en"
    moved = value.with_download_root(new)
    assert moved.download_root == new
    assert moved.model_path("sensevoice") == old
    assert moved.models == old / "x-asr-zh-en"
    assert moved.model_path("canary") == new / "canary-180m-flash"


def test_model_settings_roundtrip_and_reject_unknown_values(tmp_path):
    path = tmp_path / "settings.json"
    value = Settings(asr_model="canary", download_dir="D:/models", model_paths={"xasr": "E:/x"})
    value.save(path)
    assert Settings.load(path) == value
    path.write_text(
        json.dumps({"asr_model": "qwen3", "model_paths": {"canary": 5, "qwen3": "x", "xasr": "E:/x", "sensevoice": ""}}),
        encoding="utf-8",
    )
    loaded = Settings.load(path)
    assert loaded.asr_model == "sensevoice"
    assert loaded.model_paths == {"xasr": "E:/x"}


def test_transcript_spacing_and_segment_joining():
    assert tidy_spacing("嗯， 我想安排， 先做 Windows 桌面版本 。 测试 API 还有 （备注） 好 的") == (
        "嗯，我想安排，先做 Windows 桌面版本。测试 API 还有（备注）好的"
    )
    assert join_segments(["Hello world.", "Next", "你好。", "下一句"]) == "Hello world. Next你好。下一句"


def fake_model(files: dict[str, bytes], base: str = "https://models.example/") -> AsrModel:
    specs = tuple(
        ModelFile(name, f"{base}{name}", len(data), hashlib.sha256(data).hexdigest())
        for name, data in files.items()
    )
    return AsrModel("fake", "Fake", "sense_voice", "fake", "", ("a", "b", "c"), "", "", specs)


def mock_client(monkeypatch, files: dict[str, bytes], requests: list, hold: threading.Event | None = None):
    real = httpx.Client

    def handler(request):
        requests.append(request.url.path)
        name = request.url.path.lstrip("/")
        if hold:
            hold.wait(5)
        return httpx.Response(200, content=files[name])

    monkeypatch.setattr(models.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))


def test_download_creates_nested_files_verifies_and_reuses(tmp_path, monkeypatch):
    files = {"model.onnx": b"weights" * 100, "tokenizer/vocab.json": b'{"a": 1}'}
    requests, progress = [], []
    mock_client(monkeypatch, files, requests)
    model = fake_model(files)
    download_model(model, tmp_path / "m", lambda value, name: progress.append(value))
    assert (tmp_path / "m" / "tokenizer" / "vocab.json").read_bytes() == files["tokenizer/vocab.json"]
    assert models.models_ready(tmp_path / "m", verify=True, model=model)
    assert progress[-1] == 100
    assert not list((tmp_path / "m").rglob("*.part"))
    requests.clear()
    download_model(model, tmp_path / "m")
    assert requests == []  # Verified files are reused, not downloaded again.


def test_corrupt_download_is_rejected_and_cleaned(tmp_path, monkeypatch):
    files = {"model.onnx": b"weights"}
    model = fake_model(files)
    mock_client(monkeypatch, {"model.onnx": b"tampers"}, [])
    with pytest.raises(models.ModelError, match="校验失败"):
        download_model(model, tmp_path)
    assert not list(tmp_path.iterdir())


def test_cancelled_download_leaves_no_partial_file(tmp_path, monkeypatch):
    files = {"model.onnx": b"x" * 1000}
    cancel = threading.Event()
    cancel.set()
    mock_client(monkeypatch, files, [])
    with pytest.raises(Cancelled):
        download_model(fake_model(files), tmp_path, cancel=cancel)
    assert not list(tmp_path.rglob("*.part"))


HF_BASE = "https://huggingface.co/org/repo/resolve/abc123/"
REAL_CLIENT = httpx.Client


def use_transport(monkeypatch, handler):
    monkeypatch.setattr(
        models.httpx, "Client", lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handler), **kwargs)
    )


def test_hugging_face_files_try_the_china_mirror_first_and_github_files_fall_back_to_proxies():
    weights = SENSE_VOICE.files[0]
    assert models.file_urls(weights) == (
        weights.url.replace("https://huggingface.co/", "https://hf-mirror.com/"),
        weights.url,
    )
    urls = models.file_urls(VAD)
    assert urls[0] == VAD.url and len(urls) > 1
    assert all(url.endswith(VAD.url) for url in urls)  # Proxies fetch the same pinned release file.
    every = [url for model in ALL_MODELS for spec in model.files for url in models.file_urls(spec)]
    assert all(url.startswith("https://") for url in every)


def test_download_moves_on_when_an_address_cannot_be_reached(tmp_path, monkeypatch):
    data = b"weights" * 100
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "hf-mirror.com":
            raise httpx.ConnectError("blocked", request=request)
        return httpx.Response(200, content=data)

    use_transport(monkeypatch, handler)
    download_model(fake_model({"model.onnx": data}, HF_BASE), tmp_path)
    assert hosts == ["hf-mirror.com", "huggingface.co"]
    assert (tmp_path / "model.onnx").read_bytes() == data


@pytest.mark.parametrize("honours_range", [True, False])
def test_interrupted_download_continues_from_the_saved_part(tmp_path, monkeypatch, honours_range):
    data = bytes(range(256)) * 4000
    requests = []

    def handler(request):
        requests.append((request.headers.get("range"), request.headers.get("accept-encoding")))
        if len(requests) == 1:

            def broken():
                yield data[: models.CHUNK]
                raise httpx.ReadError("connection reset", request=request)

            return httpx.Response(200, content=broken())
        if not honours_range:
            return httpx.Response(200, content=data)
        start = int(request.headers["range"].removeprefix("bytes=").removesuffix("-"))
        return httpx.Response(206, content=data[start:])

    use_transport(monkeypatch, handler)
    progress = []
    download_model(fake_model({"model.onnx": data}), tmp_path, lambda value, name: progress.append(value))
    assert requests[1] == (f"bytes={models.CHUNK}-", "identity")
    assert (tmp_path / "model.onnx").read_bytes() == data
    assert not list(tmp_path.glob("*.part"))
    assert progress[-1] == 100


def test_failed_download_says_why_and_keeps_the_part_for_next_time(tmp_path, monkeypatch):
    data = b"x" * (models.CHUNK * 2)
    model = fake_model({"model.onnx": data})

    use_transport(monkeypatch, lambda request: httpx.Response(429))
    with pytest.raises(models.ModelError, match="繁忙"):
        download_model(model, tmp_path)

    def unreachable(request):
        raise httpx.ConnectError("refused", request=request)

    use_transport(monkeypatch, unreachable)
    with pytest.raises(models.ModelError, match="连不上.*导入"):
        download_model(model, tmp_path)

    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:

            def broken():
                yield data[: models.CHUNK]
                raise httpx.ReadError("reset", request=request)

            return httpx.Response(200, content=broken())
        raise httpx.ReadTimeout("stalled", request=request)

    use_transport(monkeypatch, flaky)
    with pytest.raises(models.ModelError, match="已下载的部分会保留"):
        download_model(model, tmp_path)
    assert len(calls) == 1 + models.RETRIES  # Gives up after attempts that add nothing.
    assert (tmp_path / "model.onnx.part").stat().st_size == models.CHUNK


def test_files_are_copied_from_a_local_pack_and_damaged_copies_are_downloaded(tmp_path, monkeypatch):
    files = {"model.onnx": b"weights" * 100, "tokens.txt": b"a 1\nb 2\n"}
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "model.onnx").write_bytes(files["model.onnx"])
    (pack / "tokens.txt").write_bytes(b"x 1\nb 2\n")  # Right size, wrong content.
    requests = []
    mock_client(monkeypatch, files, requests)
    model = fake_model(files)
    download_model(model, tmp_path / "m", sources=(tmp_path / "missing", pack))
    assert requests == ["/tokens.txt"]
    assert models.models_ready(tmp_path / "m", verify=True, model=model)
    assert not list((tmp_path / "m").glob("*.part"))


def test_download_removes_unfinished_files_left_by_older_versions(tmp_path, monkeypatch):
    files = {"model.onnx": b"weights"}
    mock_client(monkeypatch, files, [])
    stale = tmp_path / f"model.onnx.{'a' * 32}.part"
    stale.write_bytes(b"old")
    other = tmp_path / "model.onnx.notes.part"
    other.write_bytes(b"keep")
    download_model(fake_model(files), tmp_path)
    assert not stale.exists() and other.exists()


def test_offline_pack_folders_are_found_by_file_names_and_sizes(tmp_path, monkeypatch):
    one = fake_model({"a.onnx": b"1" * 10, "tokens.txt": b"t"})
    two = replace(fake_model({"b.onnx": b"2" * 20, "tokens.txt": b"t"}), id="other")
    monkeypatch.setattr(models, "ALL_MODELS", (one, two))
    pack = tmp_path / "好好说 Saywell" / "语音模型"
    for folder, model in ((pack / "One", one), (pack / "Two", two)):
        folder.mkdir(parents=True)
        for spec in model.files:
            (folder / spec.name).write_bytes(b"z" * spec.size)
    (pack / "Broken").mkdir()
    (pack / "Broken" / "tokens.txt").write_bytes(b"t")
    assert models.find_models(tmp_path / "好好说 Saywell") == {"fake": pack / "One", "other": pack / "Two"}
    assert models.find_models(pack / "Two") == {"other": pack / "Two"}
    assert models.find_models(tmp_path / "missing") == {}


@pytest.mark.parametrize("discard", [True, False])
def test_queued_imports_run_in_turn_and_cancelling_stops_the_rest(qt_app, tmp_path, monkeypatch, discard):
    calls, events = [], []
    waiting = threading.Event()
    part = models.part_path(tmp_path / "c" / get_model("canary").files[0].name)

    def fake_download(model, directory, progress, cancel, sources):
        calls.append((model.id, sources))
        if model.id == "canary":
            part.parent.mkdir(parents=True)
            part.write_bytes(b"x")
            waiting.set()
            cancel.wait(5)
            raise Cancelled("已取消下载。")

    monkeypatch.setattr("saywell.pipeline.download_model", fake_download)
    downloads = ModelDownloads()
    downloads.finished.connect(lambda *args: events.append(("finished", args)))
    downloads.failed.connect(lambda *args: events.append(("failed", args)))
    pack = (tmp_path / "pack",)
    jobs = [("xasr", tmp_path / "x", pack), ("canary", tmp_path / "c", pack), ("sensevoice", tmp_path, pack)]
    assert downloads.start_many(jobs, "导入")
    assert downloads.verb == "导入"
    assert waiting.wait(5)
    downloads.cancel(discard=discard)
    downloads.thread.join(5)
    qt_app.processEvents()
    assert calls == [("xasr", pack), ("canary", pack)]
    assert events == [("finished", ("xasr",)), ("failed", ("canary", ""))]
    assert downloads.active is None
    # Cancelling by hand deletes the unfinished file; quitting keeps it to continue next time.
    assert part.exists() is not discard


def test_background_download_reports_success_and_allows_one_at_a_time(qt_app, tmp_path, monkeypatch):
    calls, events = [], []
    release = threading.Event()

    def fake_download(model, directory, progress, cancel, sources):
        calls.append((model.id, directory))
        progress(50, "file")
        release.wait(5)

    monkeypatch.setattr("saywell.pipeline.download_model", fake_download)
    downloads = ModelDownloads()
    downloads.progress.connect(lambda *args: events.append(("progress", args)))
    downloads.finished.connect(lambda *args: events.append(("finished", args)))
    assert downloads.start("canary", tmp_path)
    assert not downloads.start("xasr", tmp_path)
    release.set()
    downloads.thread.join(5)
    qt_app.processEvents()
    assert calls == [("canary", tmp_path)]
    assert downloads.active is None
    assert ("progress", ("canary", 50)) in events and ("finished", ("canary",)) in events


@pytest.fixture
def model_dialog(qt_app, tmp_path, monkeypatch):
    ready = {"sensevoice"}
    applied = []
    starts = []
    monkeypatch.setattr(ui, "models_ready", lambda directory, model: model.id in ready)
    downloads = ModelDownloads()
    monkeypatch.setattr(
        downloads, "start", lambda model_id, directory, sources=(): starts.append((model_id, directory)) or True
    )
    value = Settings(download_dir=str(tmp_path), model_paths={"sensevoice": str(tmp_path / "legacy")})
    dialog = ModelDialog(value, downloads, lambda new: applied.append(new) or True)
    yield SimpleNamespace(dialog=dialog, ready=ready, applied=applied, starts=starts, root=tmp_path)
    dialog.close()


def test_model_dialog_downloads_into_chosen_location_then_switches(model_dialog):
    view = model_dialog
    card = view.dialog.cards["xasr"]
    assert "下载" in card.action.text()
    card.action.click()
    assert view.starts == [("xasr", view.root / "x-asr-zh-en")]
    assert view.applied == []  # Downloading never switches the active model by itself.
    view.ready.add("xasr")
    view.dialog.refresh()
    assert card.action.text() == "使用此模型"
    card.action.click()
    assert view.applied[-1].asr_model == "xasr"
    assert view.dialog.cards["xasr"].property("active")
    assert not view.dialog.cards["sensevoice"].property("active")


def test_model_dialog_deletes_only_model_files(model_dialog, monkeypatch):
    view = model_dialog
    folder = view.root / "canary-180m-flash"
    folder.mkdir(parents=True)
    for spec in get_model("canary").files:
        (folder / spec.name).write_bytes(b"x")
    keep = view.root / "notes.txt"
    keep.write_text("keep", encoding="utf-8")
    view.ready.add("canary")
    view.dialog.refresh()
    assert view.dialog.cards["canary"].delete_button.isVisibleTo(view.dialog)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    view.dialog.cards["canary"].delete_button.click()
    assert not folder.exists()
    assert keep.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("model_id", ["sensevoice", "qwen3-asr", "firered-aed"])
def test_every_downloaded_model_can_be_deleted_including_the_one_in_use(model_dialog, monkeypatch, model_id):
    view = model_dialog
    released = []
    view.dialog.release = lambda: released.append(True) or True
    view.dialog.settings = replace(view.dialog.settings, asr_model=model_id)
    view.ready.update({model.id for model in ALL_MODELS})
    view.dialog.refresh()
    assert not any(card.delete_button.isHidden() for card in view.dialog.cards.values())
    deleted = []
    monkeypatch.setattr(ui, "delete_model_files", lambda model, directory: deleted.append(model.id))
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: questions.append(args[2]) or QMessageBox.StandardButton.Yes)
    view.dialog.cards[model_id].delete_button.click()
    assert deleted == [model_id]
    assert released == [True]  # The model in use is unloaded before its files go.
    assert "正在使用" in questions[0]
    assert "已删除" in view.dialog.message.text()


def test_model_in_use_is_kept_while_recognizing_and_failed_delete_is_reported(model_dialog, monkeypatch):
    view = model_dialog
    view.dialog.release = lambda: False
    view.dialog.refresh()
    deleted, warnings = [], []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(ui, "delete_model_files", lambda model, directory: deleted.append(model.id))
    view.dialog.cards["sensevoice"].delete_button.click()
    assert deleted == [] and "正在识别" in warnings[0]

    def locked(model, directory):
        raise PermissionError("in use")

    view.dialog.release = lambda: True
    monkeypatch.setattr(ui, "delete_model_files", locked)
    view.dialog.cards["sensevoice"].delete_button.click()
    assert "正在使用或没有权限" in warnings[1]
    assert "已删除" not in view.dialog.message.text()
    assert view.dialog.settings.model_paths == {"sensevoice": str(view.root / "legacy")}  # Pin kept.


def test_model_dialog_warns_before_download_without_disk_space(model_dialog, monkeypatch):
    warnings = []
    monkeypatch.setattr(ui, "free_space", lambda path: 10_000_000)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1]))
    model_dialog.dialog.cards["canary"].action.click()
    assert warnings == ["磁盘空间不足"]
    assert model_dialog.starts == []


def test_model_dialog_imports_the_models_found_in_a_pack(model_dialog, monkeypatch):
    view = model_dialog
    pack = view.root / "pack"
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *args: str(pack))
    monkeypatch.setattr(ui, "free_space", lambda path: None)
    notes = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: notes.append(args[1]))
    started = []
    monkeypatch.setattr(view.dialog.downloads, "start_many", lambda jobs, verb: started.append((jobs, verb)) or True)
    monkeypatch.setattr(ui, "find_models", lambda folder: {})
    view.dialog.import_button.click()
    assert notes == ["没有找到模型"] and started == []

    monkeypatch.setattr(ui, "find_models", lambda folder: {"sensevoice": folder / "s", "qwen3-asr": folder / "q"})
    view.dialog.import_button.click()
    # SenseVoice is already downloaded, so only Qwen3-ASR is copied into the download location.
    assert started == [([("qwen3-asr", view.root / "qwen3-asr-0.6b-q8", (pack / "q",))], "导入")]
    assert "正在导入 Qwen3-ASR 0.6B" in view.dialog.message.text()


def test_main_window_switches_to_a_new_model_only_when_the_chosen_one_is_missing(qt_app, monkeypatch, tmp_path):
    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    ready = {"qwen3-asr"}
    monkeypatch.setattr(
        "saywell.app.models_ready",
        lambda directory, model: (model if isinstance(model, str) else model.id) in ready,
    )
    window = MainWindow(Settings(download_dir=str(tmp_path)), native=False)
    try:
        window.downloads.verb = "导入"
        window.on_download_finished("qwen3-asr")  # SenseVoice, the default, was never downloaded.
        assert window.settings.asr_model == "qwen3-asr"
        assert window.status.text() == "语音模型已就绪"
        ready.add("canary")
        window.on_download_finished("canary")  # A usable model is never replaced.
        assert window.settings.asr_model == "qwen3-asr"
        assert "Canary 180M Flash 已导入" in window.stats.text()
    finally:
        window.shutdown()
        window.close()


def test_pipeline_loads_selected_model_and_reloads_after_switch(monkeypatch):
    created = []

    class Recognizer:
        def __init__(self, directory, model, gpu=False):
            created.append(model if not gpu else f"{model}+gpu")
            self.directory, self.model, self.gpu = directory, model, gpu

        def transcribe(self, *args):
            return "识别文字"

    emitted = []
    events = SimpleNamespace(**{
        name: SimpleNamespace(emit=lambda *args, name=name: emitted.append((name, args)))
        for name in ("stage", "transcript", "completed", "failed")
    })
    monkeypatch.setattr("saywell.asr.LocalRecognizer", Recognizer)
    monkeypatch.setattr("saywell.pipeline.read_key", lambda **kwargs: "")
    pipeline = Pipeline(events)
    pipeline.recognizer = Recognizer(Settings().models, "sensevoice")
    created.clear()
    samples = SimpleNamespace(size=16000)
    pipeline._run(1, threading.Event(), Settings(), samples, "audio")
    assert created == []  # Same model stays loaded.
    pipeline._run(2, threading.Event(), Settings(asr_model="xasr"), samples, "audio")
    assert created == ["xasr"]
    assert ("stage", (2, "正在加载 X-ASR 中英…")) in emitted
    # GPU acceleration only matters for models that support it, and switching it reloads them.
    pipeline._run(3, threading.Event(), Settings(asr_model="xasr", gpu_acceleration=True), samples, "audio")
    assert created == ["xasr"]
    pipeline._run(4, threading.Event(), Settings(asr_model="qwen3-asr", gpu_acceleration=True), samples, "audio")
    pipeline._run(5, threading.Event(), Settings(asr_model="qwen3-asr"), samples, "audio")
    assert created == ["xasr", "qwen3-asr+gpu", "qwen3-asr"]
    assert pipeline.release_recognizer() and pipeline.recognizer is None


def test_main_window_applies_model_choice_and_shows_download_progress(qt_app, monkeypatch):
    from saywell.app import MainWindow

    monkeypatch.setattr("saywell.ui.read_key", lambda *args, **kwargs: "")
    saved = []
    monkeypatch.setattr(Settings, "save", lambda self, path=None: saved.append(self))
    window = MainWindow(Settings(), native=False)
    try:
        window.pipeline.recognizer = object()
        assert window.apply_model_settings(replace(window.settings, asr_model="canary"))
        assert saved[-1].asr_model == "canary" and window.settings.asr_model == "canary"
        assert window.pipeline.recognizer is None
        assert "Canary" in window.model_chip.text()
        window.on_download_progress("canary", 40)
        assert "40%" in window.download_status.text()
        assert window.download_progress.value() == 40
        window.on_download_failed("canary", "模型下载失败，请检查网络或代理后重试。")
        assert "Canary 180M Flash" in window.notice.text()
        assert window.download_progress.isHidden()
    finally:
        window.shutdown()
        window.close()


def test_each_model_uses_its_sherpa_onnx_loader_with_catalog_files(tmp_path, monkeypatch):
    from saywell import asr

    calls = {}
    for name in ("from_sense_voice", "from_transducer", "from_nemo_canary"):
        monkeypatch.setattr(
            asr.sherpa_onnx.OfflineRecognizer, name, staticmethod(lambda name=name, **kw: calls.update({name: kw}))
        )
    # GGUF models run in a local llama-server process instead of sherpa-onnx.
    monkeypatch.setattr(
        "saywell.llama.LlamaServerRecognizer", lambda **kw: calls.update({"llama_server": kw})
    )
    for model in CATALOG:
        asr.create_recognizer(tmp_path, model.id)
    names = {spec.name for model in CATALOG for spec in model.files}
    for kwargs in calls.values():
        for key, value in kwargs.items():
            value = str(value)
            if value.startswith(str(tmp_path)):
                relative = value[len(str(tmp_path)) + 1:]
                # Files, or a folder of downloaded files (Qwen3's tokenizer).
                assert relative in names or any(n.startswith(f"{relative}/") for n in names), (key, value)
    assert calls["from_nemo_canary"]["src_lang"] == calls["from_nemo_canary"]["tgt_lang"] == "en"
    assert calls["from_sense_voice"]["use_itn"]
    assert calls["llama_server"]["mmproj"] == tmp_path / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
    assert calls["llama_server"]["threads"] >= 1
    assert set(calls) == {"from_sense_voice", "from_transducer", "llama_server", "from_nemo_canary"}


def test_plaza_models_are_pinned_documented_and_separate_from_the_curated_models():
    from saywell.models import ALL_MODELS, PLAZA, PLAZA_CATEGORIES

    assert len(PLAZA) == 9 and not set(PLAZA) & set(CATALOG)
    assert ALL_MODELS == CATALOG + PLAZA
    ids, folders = [m.id for m in ALL_MODELS], [m.folder for m in ALL_MODELS]
    assert len(set(ids)) == len(ids) and len(set(folders)) == len(folders)
    for model in PLAZA:
        assert model.folder and VAD in model.files
        assert model.tagline and model.caveat
        assert model.category in PLAZA_CATEGORIES and model.homepage.startswith("https://")
        assert len(model.strengths) == 3
        assert all("/resolve/main/" not in spec.url for spec in model.files)
        names = {spec.name for spec in model.files}
        for _key, name in model.inputs:
            # Every loader input is a downloaded file, or a folder of downloaded files (tokenizers).
            assert name in names or any(n.startswith(f"{name}/") for n in names), (model.id, name)
    assert {m.category for m in PLAZA} == set(PLAZA_CATEGORIES)


def test_plaza_models_use_their_sherpa_onnx_loaders(tmp_path, monkeypatch):
    from saywell import asr
    from saywell.models import PLAZA

    calls = {}
    for model in PLAZA:
        name = f"from_{model.kind}"
        monkeypatch.setattr(
            asr.sherpa_onnx.OfflineRecognizer, name, staticmethod(lambda name=name, **kw: calls.setdefault(name, []).append(kw))
        )
    for model in PLAZA:
        asr.create_recognizer(tmp_path, model.id)
    assert sum(len(v) for v in calls.values()) == len(PLAZA)
    parakeet = next(kw for kw in calls["from_transducer"] if kw["encoder"].endswith("encoder.int8.onnx"))
    assert parakeet["model_type"] == "nemo_transducer"
    assert calls["from_whisper"][0]["language"] == "" and calls["from_whisper"][0]["task"] == "transcribe"
    assert all(kw["num_threads"] >= 1 for group in calls.values() for kw in group)


def test_plaza_model_choice_survives_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    value = Settings(asr_model="firered-aed", model_paths={"qwen3-asr": "E:/q"})
    value.save(path)
    assert Settings.load(path) == value
    assert value.models == value.download_root / "firered-asr2-aed"


def test_model_dialog_has_a_separate_plaza_tab_with_filters(model_dialog):
    from saywell.models import PLAZA

    dialog = model_dialog.dialog
    assert dialog.pages.count() == 2 and dialog.pages.currentIndex() == 0
    assert set(dialog.cards) == {m.id for m in CATALOG} | {m.id for m in PLAZA}
    dialog.tab_buttons[1].click()
    assert dialog.pages.currentIndex() == 1
    assert dialog.cards["parakeet-v3"].homepage.toolTip().startswith("https://huggingface.co/nvidia/")
    dialog.chips["方言"].click()
    visible = {m.id for m in PLAZA if dialog.cards[m.id].isVisibleTo(dialog)}
    assert visible == {m.id for m in PLAZA if m.category == "方言"}
    dialog.chips["全部"].click()
    assert all(dialog.cards[m.id].isVisibleTo(dialog) for m in PLAZA)
    card = dialog.cards["paraformer-zh"]
    card.action.click()
    assert model_dialog.starts == [("paraformer-zh", model_dialog.root / "paraformer-zh")]


def test_model_dialog_opens_on_plaza_when_a_plaza_model_is_active(qt_app, tmp_path):
    dialog = ModelDialog(Settings(download_dir=str(tmp_path), asr_model="whisper-turbo"), ModelDownloads(), lambda new: True)
    try:
        assert dialog.pages.currentIndex() == 1 and dialog.tab_buttons[1].isChecked()
    finally:
        dialog.close()


def test_tray_uses_white_glyph_on_dark_taskbar(qt_app, monkeypatch):
    used = []
    monkeypatch.setattr(ui, "image_icon", lambda name: used.append(name) or name)
    monkeypatch.setattr(ui, "taskbar_is_light", lambda: False)
    assert ui.tray_icon() == "tray-white.png"
    monkeypatch.setattr(ui, "taskbar_is_light", lambda: True)
    assert ui.tray_icon() == "icon.png"
    assert (ui.ASSETS / "icon.png").is_file() and (ui.ASSETS / "tray-white.png").is_file()


def test_every_card_states_speed_the_same_way():
    assert replace(SENSE_VOICE, seconds_per_minute=0.88).speed_label == "60 秒语音约 0.9 秒"
    assert replace(SENSE_VOICE, seconds_per_minute=14.4).speed_label == "60 秒语音约 14 秒"
    for model in models.ALL_MODELS:
        # Every card shows a measured speed and a link to the model's homepage.
        assert model.seconds_per_minute > 0, model.id
        assert model.homepage.startswith("https://"), model.id


def test_try_button_listens_until_finished_then_shows_the_text(model_dialog, monkeypatch, qt_app):
    import threading
    import time

    from saywell import checks

    listening = threading.Event()

    def record(seconds, device, stop, tick):
        tick(seconds)
        listening.set()
        assert stop.wait(5)  # Until the user clicks "说完了".
        return np.ones(16000, dtype=np.float32)

    monkeypatch.setattr(checks, "record_clip", record)
    monkeypatch.setattr(
        checks, "try_model", lambda model, directory, samples, gpu=False: checks.CheckResult(True, "“你好” · 识别用时 0.2 秒")
    )
    card = model_dialog.dialog.cards["sensevoice"]
    card.try_button.click()
    assert listening.wait(5)
    deadline = time.monotonic() + 5
    while "还剩 10 秒" not in card.check_status.text.text() and time.monotonic() < deadline:
        qt_app.processEvents()
    assert card.try_button.text().strip() == "说完了" and card.try_button.isEnabled()
    assert not card.verify_button.isEnabled()
    card.try_button.click()
    while card.stop_recording is not None or not card.verify_button.isEnabled():
        qt_app.processEvents()
        assert time.monotonic() < deadline
    assert card.try_button.text().strip() == "试一试" and "你好" in card.check_status.text.text()
