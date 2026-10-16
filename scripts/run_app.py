import os
import sys
import traceback
import tempfile
from pathlib import Path

IS_CLI = "--cli" in sys.argv


def launch():
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        from saywell.cli import main
    else:
        from saywell.app import main
    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(launch())
    except Exception:
        diagnostic = traceback.format_exc()
        log = None
        for candidate in (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Saywell" / "startup-error.log",
            Path(tempfile.gettempdir()) / "Saywell-startup-error.log",
        ):
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text(diagnostic, encoding="utf-8")
                log = candidate
                break
            except OSError:
                continue
        if "--smoke" not in sys.argv and not IS_CLI:
            import ctypes

            detail = f"诊断日志：{log}" if log else "无法写入诊断日志，请检查用户目录权限。"
            ctypes.windll.user32.MessageBoxW(None, f"程序未能启动。{detail}", "好好说", 0x10)
        raise SystemExit(1) from None
