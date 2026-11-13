from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .api_config import DEFAULT_API_BASE_URL, chat_completion_url, credential_account, is_deepseek_api
from .models import ALL_MODELS, DEFAULT_MODEL, SENSE_VOICE, find_models, get_model, models_ready
from .shortcuts import DEFAULT_HOTKEY, parse_shortcut

MODEL_IDS = {model.id for model in ALL_MODELS}

SERVICE = "saywell"
# Names used before the app was called Saywell; existing data moves over on first use.
LEGACY_SERVICE = "better-voice-input"
DATA_FOLDER = "Saywell"
LEGACY_DATA_FOLDER = "BetterVoiceInput"


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def _app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home())))


def data_dir() -> Path:
    root = _app_data() / DATA_FOLDER
    legacy = _app_data() / LEGACY_DATA_FOLDER
    if not root.exists() and legacy.is_dir():
        try:
            legacy.rename(root)
        except OSError:
            # An older copy may still be running and holding files open; keep using
            # the old folder this time and move it on a later start.
            return legacy
    root.mkdir(parents=True, exist_ok=True)
    return root


def relocated(value: str) -> str:
    """Point a saved path inside the old data folder at the moved folder."""
    legacy = _app_data() / LEGACY_DATA_FOLDER
    path = Path(value)
    try:
        inside = path.relative_to(legacy)
    except ValueError:
        return value
    if path.exists() or not (_app_data() / DATA_FOLDER).is_dir():
        return value
    return str(_app_data() / DATA_FOLDER / inside)


def default_model_dir() -> Path:
    root = project_root()
    candidates = [root / "models"]
    # The local build lives in <project>/dist/Saywell; reuse the
    # project's models without bundling or downloading a second copy.
    if getattr(sys, "frozen", False) and root.parent.name.lower() == "dist":
        candidates.append(root.parent.parent / "models")
    for local in candidates:
        if local.is_dir():
            return local
    return data_dir() / "models"


@dataclass
class Settings:
    api_base_url: str = DEFAULT_API_BASE_URL
    model: str = "deepseek-flash"
    model_dir: str = ""
    microphone: int | None = None
    hotkey: str = DEFAULT_HOTKEY
    hold_to_talk: bool = True
    save_history: bool = False
    # Daily counts and token totals for the overview page; never any text.
    collect_stats: bool = True
    achievement_popups: bool = True  # A small card at the bottom right when one is reached.
    start_on_login: bool = False
    glossary: list[str] = field(default_factory=lambda: ["DeepSeek", "API", "Windows", "Ctrl", "Shift"])
    # Imported professional lexicons: {"id", "name", "enabled", "count"}; the terms live in
    # lexicons/<id>.txt under the data folder (see vocabulary.py).
    lexicons: list[dict] = field(default_factory=list)
    api_timeout: float = 25.0
    asr_model: str = DEFAULT_MODEL
    # Where new model downloads go. Models downloaded before a location change
    # are pinned in model_paths so they never need to be downloaded again.
    download_dir: str = ""
    model_paths: dict[str, str] = field(default_factory=dict)
    # Run models that support it (Qwen3-ASR) on the graphics card.
    gpu_acceleration: bool = False
    # Saying "翻译成英文" at the end inserts the translation instead.
    voice_translation: bool = True
    # One unpunctuated paragraph: Chinese punctuation and modal particles such as 啊、呢、吧 go.
    no_punctuation: bool = False
    # English punctuation, chosen apart from the Chinese: some people leave out only one of them.
    no_english_punctuation: bool = False
    # Leave each result on the clipboard instead of restoring the previous content, for games
    # and other places where no text box can be opened while speaking.
    keep_in_clipboard: bool = False
    # Last language chosen for the 翻译 button on the text page.
    translate_language: str = "英语"
    # Look for a newer version once a day; only the signed version file is downloaded.
    check_updates: bool = True
    # A version whose announcement the user turned off ("这个版本不再提醒"); newer ones still show.
    skipped_release: str = ""
    # The version that ran last, so the first start of a newer one shows what is new.
    last_version: str = ""
    # Meetings: the app last recorded (its executable; "-" for the microphone alone) and
    # whether the user's own microphone is recorded too.
    meeting_app: str = ""
    meeting_microphone: bool = True
    # The template minutes were last written with (minutes.TEMPLATES).
    meeting_template: str = "general"
    # Exporting a meeting: the folder last saved to, and whether the transcript goes along
    # with the minutes.
    meeting_export_dir: str = ""
    meeting_export_transcript: bool = False
    meeting_export_format: str = "docx"
    # Selection translation, off until turned on. How it starts: "icon" shows a small button
    # after a mouse selection, "direct" translates at once, "hotkey" only on the shortcut (which
    # works in every mode). Text in selection_native goes to selection_foreign and anything else
    # comes to selection_native. The style is a translation.STYLES key; programs in
    # selection_excluded (executables, lower case) are left alone.
    selection_translate: bool = False
    selection_trigger: str = "icon"
    selection_hotkey: str = "RightAlt"
    selection_native: str = "简体中文"
    selection_foreign: str = "英语"
    selection_style: str = "natural"
    selection_excluded: list[str] = field(default_factory=list)
    # The translation popup as last resized by hand: its width, and the most its text may take
    # before it scrolls. 0 means the built-in size.
    selection_popup_width: int = 0
    selection_popup_height: int = 0
    # The button shown beside a selection: "small", "medium" or "large", and how opaque it is
    # until the mouse comes over it, in percent.
    selection_icon_size: str = "medium"
    selection_icon_opacity: int = 100
    # The look of every window: "light", "dark", or "system" to follow Windows.
    appearance: str = "light"
    # The overlay shown while dictating: "standard", the dark card with words, or "minimal", a
    # small pill with the voice level only; how opaque its background is, in percent; and where it
    # was dragged to, as the middle of its bottom edge on the desktop ([] for the bottom middle).
    overlay_style: str = "standard"
    overlay_opacity: int = 100
    overlay_position: list[int] = field(default_factory=list)

    @property
    def download_root(self) -> Path:
        if self.download_dir:
            return Path(self.download_dir)
        return Path(self.model_dir) if self.model_dir else default_model_dir()

    def model_path(self, model_id: str | None = None) -> Path:
        model = get_model(model_id or self.asr_model)
        if model.id in self.model_paths:
            return Path(self.model_paths[model.id])
        return self.download_root / model.folder if model.folder else self.download_root

    @property
    def models(self) -> Path:
        """Directory of the selected speech model."""
        return self.model_path()

    def with_download_root(self, directory: Path) -> Settings:
        """Change the download location while keeping downloaded models where they are."""
        pinned = dict(self.model_paths)
        for model in ALL_MODELS:
            current = self.model_path(model.id)
            if model.id not in pinned and models_ready(current, model=model):
                pinned[model.id] = str(current)
        return replace(self, download_dir=str(directory), model_paths=pinned)

    def with_found_models(self) -> Settings:
        """Use model folders put into the download location by hand under any name, such as one
        from the cloud-drive model pack; switch to one when the chosen model is not downloaded."""
        missing = [model for model in ALL_MODELS if not models_ready(self.model_path(model.id), model=model)]
        if not missing:
            return self
        found = find_models(self.download_root)
        arrived = [model.id for model in missing if model.id in found]
        if not arrived:
            return self
        pinned = dict(self.model_paths) | {model_id: str(found[model_id]) for model_id in arrived}
        changed = replace(self, model_paths=pinned)
        if not models_ready(changed.models, model=changed.asr_model):
            changed = replace(changed, asr_model=arrived[0])
        return changed

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        loaded, chosen = cls._read(path or data_dir() / "settings.json")
        return loaded if chosen else loaded.keeping_old_default()

    def keeping_old_default(self) -> Settings:
        """Someone who never chose a model and already has SenseVoice, the default before
        Qwen3-ASR, goes on using it rather than being asked to download another."""
        if (
            self.asr_model == DEFAULT_MODEL
            and not models_ready(self.model_path(DEFAULT_MODEL), model=DEFAULT_MODEL)
            and models_ready(self.model_path(SENSE_VOICE.id), model=SENSE_VOICE)
        ):
            return replace(self, asr_model=SENSE_VOICE.id)
        return self

    @classmethod
    def _read(cls, path: Path) -> tuple[Settings, bool]:
        """The settings in the file, and whether a model was chosen in them."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls(), False
            defaults = cls()
            values = {}
            for key, value in raw.items():
                if key not in cls.__dataclass_fields__:
                    continue
                if key == "microphone":
                    if value is None or type(value) is int and value >= 0:
                        values[key] = value
                elif key == "glossary":
                    if isinstance(value, list) and all(isinstance(word, str) for word in value):
                        values[key] = [word[:80] for word in value[:100]]
                elif key == "selection_excluded":
                    if isinstance(value, list):
                        values[key] = [name.strip().lower()[:80] for name in value[:100] if isinstance(name, str) and name.strip()]
                elif key == "lexicons":
                    if isinstance(value, list):
                        values[key] = [
                            {"id": item["id"], "name": item["name"][:80], "enabled": item["enabled"], "count": item["count"]}
                            for item in value
                            if isinstance(item, dict)
                            and isinstance(item.get("id"), str)
                            and re.fullmatch(r"[0-9a-f]{32}", item["id"])
                            and isinstance(item.get("name"), str)
                            and type(item.get("enabled")) is bool
                            and type(item.get("count")) is int
                        ]
                elif key == "asr_model":
                    if value in MODEL_IDS:
                        values[key] = value
                elif key == "model_paths":
                    if isinstance(value, dict):
                        values[key] = {
                            name: relocated(path)
                            for name, path in value.items()
                            if name in MODEL_IDS and isinstance(path, str) and path
                        }
                elif key == "overlay_position":
                    if isinstance(value, list) and len(value) == 2 and all(type(item) is int for item in value):
                        values[key] = value
                elif key in ("model_dir", "download_dir"):
                    if isinstance(value, str):
                        values[key] = relocated(value) if value else value
                elif type(value) is type(getattr(defaults, key)):
                    values[key] = value
            if "hotkey" in values and parse_shortcut(values["hotkey"]) is None:
                values.pop("hotkey")
            if "selection_hotkey" in values and parse_shortcut(values["selection_hotkey"]) is None:
                values.pop("selection_hotkey")
            from .translation import LANGUAGES, STYLES

            for name, allowed in (("selection_trigger", ("icon", "direct", "hotkey")), ("selection_style", STYLES),
                                  ("selection_native", LANGUAGES), ("selection_foreign", LANGUAGES)):
                if name in values and values[name] not in allowed:
                    values.pop(name)
            if values.get("overlay_style", "standard") not in ("standard", "minimal"):
                values.pop("overlay_style")
            if not 0 <= values.get("overlay_opacity", 100) <= 100:
                values.pop("overlay_opacity")
            if values.get("selection_icon_size", "medium") not in ("small", "medium", "large"):
                values.pop("selection_icon_size")
            if not 30 <= values.get("selection_icon_opacity", 100) <= 100:
                values.pop("selection_icon_opacity")
            if values.get("appearance", "light") not in ("light", "dark", "system"):
                values.pop("appearance")
            if not 1 <= values.get("api_timeout", 25) <= 120:
                values.pop("api_timeout", None)
            return cls(**values), "asr_model" in values
        except (OSError, ValueError, TypeError):
            return cls(), False

    def save(self, path: Path | None = None) -> None:
        path = path or data_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)


def read_key(key_file: Path | None = None, *, api_base_url: str = DEFAULT_API_BASE_URL) -> str:
    if key_file is not None:
        return key_file.read_text(encoding="utf-8-sig").strip()
    try:
        account = credential_account(api_base_url)
    except ValueError:
        return ""
    # A generic environment key must be paired with its intended endpoint.
    env_url = os.environ.get("SAYWELL_API_BASE_URL", "")
    try:
        if env_url and chat_completion_url(env_url) == chat_completion_url(api_base_url):
            value = os.environ.get("SAYWELL_API_KEY", "").strip()
            if value:
                return value
    except ValueError:
        pass
    legacy = is_deepseek_api(api_base_url)
    if legacy:
        value = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if value:
            return value
    try:
        import keyring

        value = keyring.get_password(SERVICE, account)
        if value:
            return value
        value = keyring.get_password(LEGACY_SERVICE, account)
        if value:
            _move_legacy_key(keyring, account, value)
            return value
    except Exception:
        pass
    local = project_root() / "deepseek api key.txt"
    if legacy and local.is_file():
        return local.read_text(encoding="utf-8-sig").strip()
    return ""


def _move_legacy_key(keyring, account: str, value: str) -> None:
    """Copy a key saved under the old app name to the new one, then drop the old entry."""
    try:
        keyring.set_password(SERVICE, account, value)
    except Exception:
        return  # Still readable from the old entry next time.
    try:
        keyring.delete_password(LEGACY_SERVICE, account)
    except Exception:
        pass


def save_key(value: str, *, api_base_url: str = DEFAULT_API_BASE_URL) -> None:
    import keyring

    keyring.set_password(SERVICE, credential_account(api_base_url), value.strip())
