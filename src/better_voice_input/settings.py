from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .api_config import DEFAULT_API_BASE_URL, chat_completion_url, credential_account, is_deepseek_api
from .models import ALL_MODELS, DEFAULT_MODEL, get_model, models_ready
from .shortcuts import DEFAULT_HOTKEY, HOTKEYS

MODEL_IDS = {model.id for model in ALL_MODELS}

SERVICE = "better-voice-input"


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BetterVoiceInput"
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_model_dir() -> Path:
    root = project_root()
    candidates = [root / "models"]
    # The local build lives in <project>/dist/BetterVoiceInput; reuse the
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
    start_on_login: bool = False
    glossary: list[str] = field(default_factory=lambda: ["DeepSeek", "API", "Windows", "Ctrl", "Shift"])
    api_timeout: float = 25.0
    asr_model: str = DEFAULT_MODEL
    # Where new model downloads go. Models downloaded before a location change
    # are pinned in model_paths so they never need to be downloaded again.
    download_dir: str = ""
    model_paths: dict[str, str] = field(default_factory=dict)

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

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        path = path or data_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
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
                elif key == "asr_model":
                    if value in MODEL_IDS:
                        values[key] = value
                elif key == "model_paths":
                    if isinstance(value, dict):
                        values[key] = {
                            name: path
                            for name, path in value.items()
                            if name in MODEL_IDS and isinstance(path, str) and path
                        }
                elif type(value) is type(getattr(defaults, key)):
                    values[key] = value
            if "hotkey" in values and values["hotkey"] not in HOTKEYS:
                values.pop("hotkey")
            if not 1 <= values.get("api_timeout", 25) <= 120:
                values.pop("api_timeout", None)
            return cls(**values)
        except (OSError, ValueError, TypeError):
            return cls()

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
    env_url = os.environ.get("BVI_API_BASE_URL", "")
    try:
        if env_url and chat_completion_url(env_url) == chat_completion_url(api_base_url):
            value = os.environ.get("BVI_API_KEY", "").strip()
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
    except Exception:
        pass
    local = project_root() / "deepseek api key.txt"
    if legacy and local.is_file():
        return local.read_text(encoding="utf-8-sig").strip()
    return ""


def save_key(value: str, *, api_base_url: str = DEFAULT_API_BASE_URL) -> None:
    import keyring

    keyring.set_password(SERVICE, credential_account(api_base_url), value.strip())
