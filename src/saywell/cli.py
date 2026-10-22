from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .cleanup import ApiCleaner, CleanupError
from .models import ALL_MODELS
from .settings import Settings, read_key
from .vocabulary import Vocabulary


def main() -> int:
    parser = argparse.ArgumentParser(description="Saywell diagnostics")
    sub = parser.add_subparsers(dest="command", required=True)
    download = sub.add_parser("download-models", help="下载并校验本地语音模型")
    download.add_argument("--directory", type=Path)
    clean = sub.add_parser("clean", help="整理 UTF-8 文本文件")
    clean.add_argument("input", type=Path)
    transcribe = sub.add_parser("transcribe", help="本地识别音频文件")
    transcribe.add_argument("input", type=Path)
    transcribe.add_argument("--clean", action="store_true")
    transcribe.add_argument("--directory", type=Path)
    for command in (download, transcribe):
        command.add_argument(
            "--asr-model", choices=[model.id for model in ALL_MODELS], help="本地语音模型，默认使用设置中的选择"
        )
    for command in (clean, transcribe):
        command.add_argument("--key-file", type=Path)
        command.add_argument("--model", default=None)
        command.add_argument("--base-url", default=None, help="兼容 Chat Completions 的 API 地址")
        command.add_argument("--output", type=Path, help="保存结果 JSON（可能包含个人内容）")
    args = parser.parse_args()
    settings = Settings.load()
    try:
        asr_model = getattr(args, "asr_model", None) or settings.asr_model
        model_dir = getattr(args, "directory", None) or settings.model_path(asr_model)
        if args.command == "download-models":
            from .models import download_model, get_model

            last = [-1]

            def progress(percent, name):
                if percent // 10 != last[0]:
                    print(f"{percent}% {name}", flush=True)
                    last[0] = percent // 10

            download_model(get_model(asr_model), model_dir, progress=progress)
            return 0
        data = {}
        if args.command == "transcribe":
            from .asr import LocalRecognizer
            from .audio import decode_audio

            started = time.monotonic()
            audio = decode_audio(args.input)
            data["duration"] = round(audio.size / 16000, 2)
            recognizer = LocalRecognizer(model_dir, asr_model)
            text = recognizer.transcribe(audio, vocabulary=Vocabulary.for_settings(settings))
            del audio
            data.update(original=text, asr_seconds=round(time.monotonic() - started, 2))
        else:
            text = args.input.read_text(encoding="utf-8-sig")
        if args.command == "clean" or args.clean:
            base_url = args.base_url or settings.api_base_url
            result = ApiCleaner(
                read_key(args.key_file, api_base_url=base_url),
                args.model or settings.model,
                settings.api_timeout,
                base_url=base_url,
            ).clean(text, Vocabulary.for_settings(settings).glossary_for(text))
            data.update(
                text=result.text,
                warnings=result.warnings,
                elapsed=round(result.elapsed, 2),
                usage=result.usage,
                edits=[edit.model_dump() for edit in result.edits],
            )
        rendered = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        print(rendered)
        return 0
    except (CleanupError, OSError, RuntimeError) as exc:  # ModelError is a RuntimeError.
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
