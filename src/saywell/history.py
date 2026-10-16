"""Opt-in, expiring text history encrypted for the current Windows user."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from .settings import data_dir

MAX_HISTORY_ENTRIES = 5


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "history.json"

    def _entries(self) -> list[dict]:
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
            now = time.time()
            return [
                row for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("created"), (int, float))
                and 0 <= now - row["created"] < 7 * 86400
            ][-MAX_HISTORY_ENTRIES:]
        except (OSError, ValueError, TypeError):
            return []

    def _write(self, rows: list[dict]):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(rows[-MAX_HISTORY_ENTRIES:], ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def prune(self):
        """Apply the cap to older installations, even when saving history is disabled."""
        if self.path.exists():
            self._write(self._entries())
        else:
            self.path.with_suffix(".tmp").unlink(missing_ok=True)

    def append(self, original: str, text: str):
        import win32crypt

        payload = json.dumps({"original": original, "text": text}, ensure_ascii=False).encode("utf-8")
        encrypted = win32crypt.CryptProtectData(payload, "Saywell history", None, None, None, 1)
        rows = self._entries()
        rows.append({"created": time.time(), "data": base64.b64encode(encrypted).decode("ascii")})
        self._write(rows)

    def read(self) -> list[dict]:
        import win32crypt

        result = []
        rows = self._entries()
        if self.path.exists():
            self._write(rows)
        for row in reversed(rows):
            try:
                _, decrypted = win32crypt.CryptUnprotectData(
                    base64.b64decode(row["data"]), None, None, None, 1
                )
                item = json.loads(decrypted.decode("utf-8"))
                result.append({**item, "created": row["created"]})
            except Exception:
                continue
        return result

    def clear(self):
        self.path.unlink(missing_ok=True)
        self.path.with_suffix(".tmp").unlink(missing_ok=True)
