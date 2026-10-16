"""One-click checks shown in the settings and model windows."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

import httpx
import numpy as np

from .api_config import chat_completion_url, is_deepseek_api
from .audio import SAMPLE_RATE
from .models import AsrModel, verify_file

TRY_SECONDS = 10


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


def balance_url(base_url: str) -> str | None:
    """DeepSeek reports the account balance; other services have no common endpoint."""
    try:
        return "https://api.deepseek.com/user/balance" if is_deepseek_api(base_url) else None
    except ValueError:
        return None


def fetch_balance(base_url: str, key: str, timeout: float = 8) -> str | None:
    """Formatted balance such as "¥18.62", or None when unknown."""
    url = balance_url(base_url)
    if not url or not key:
        return None
    try:
        response = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        response.raise_for_status()
        infos = response.json().get("balance_infos") or []
        info = next((i for i in infos if i.get("currency") == "CNY"), infos[0] if infos else None)
        if not info:
            return None
        symbol = {"CNY": "¥", "USD": "$"}.get(info.get("currency"), "")
        return f"{symbol}{float(info.get('total_balance', 0)):.2f}"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def _api_error(response: httpx.Response) -> str:
    try:
        detail = str(response.json().get("error", {}).get("message", ""))
    except (ValueError, AttributeError):
        detail = ""
    status = response.status_code
    if status in (401, 403):
        return "API Key 不正确，或没有使用权限"
    if status == 402:
        return "账户余额不足，请先充值"
    if status == 429:
        return "请求太频繁或额度已用完，请稍后再试"
    if status >= 500:
        return "服务暂时不可用，请稍后再试"
    if "model" in detail.lower():
        return "模型名称不存在，请核对服务商提供的模型 ID"
    if status == 404:
        return "地址不对，请核对 API 地址"
    return f"请求被拒绝（{status}），请核对地址和模型名称"


def check_api(base_url: str, key: str, model: str, timeout: float = 20) -> CheckResult:
    try:
        endpoint = chat_completion_url(base_url)
    except ValueError as exc:
        return CheckResult(False, str(exc))
    if not key.strip():
        return CheckResult(False, "请先填写 API Key")
    if not model.strip():
        return CheckResult(False, "请先填写模型名称")
    body = {"model": model.strip(), "messages": [{"role": "user", "content": "只回复一个字：好"}], "max_tokens": 8}
    started = time.monotonic()
    try:
        response = httpx.post(
            endpoint, json=body, headers={"Authorization": f"Bearer {key.strip()}"}, timeout=timeout
        )
    except httpx.TimeoutException:
        return CheckResult(False, "连接超时，请检查网络或代理")
    except httpx.HTTPError:
        return CheckResult(False, "无法连接，请检查 API 地址、网络或代理")
    if response.status_code != 200:
        return CheckResult(False, _api_error(response))
    milliseconds = (time.monotonic() - started) * 1000
    message = f"连接正常 · 响应 {milliseconds:.0f} 毫秒"
    balance = fetch_balance(base_url, key.strip())
    if balance:
        message += f" · 余额 {balance}"
    return CheckResult(True, message)


def check_files(model: AsrModel, directory: Path) -> CheckResult:
    damaged = [spec.name for spec in model.files if not verify_file(directory / spec.name, spec)]
    if damaged:
        return CheckResult(False, f"{len(damaged)} 个文件不完整，点“修复”重新下载它们")
    return CheckResult(True, f"{len(model.files)} 个文件全部完整 · 共 {model.size_label.removeprefix('约 ')}")


def record_clip(
    seconds: float, device, stop: Event | None = None, tick: Callable[[int], None] | None = None
) -> np.ndarray:
    """Record up to `seconds`; `stop` ends early, `tick` gets the whole seconds left."""
    import sounddevice as sd

    clip = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
    started, shown = time.monotonic(), None
    while (elapsed := time.monotonic() - started) < seconds:
        if stop and stop.is_set():
            sd.stop()
            return clip.reshape(-1)[: int(elapsed * SAMPLE_RATE)]
        left = math.ceil(seconds - elapsed)
        if tick and left != shown:
            tick(left)
            shown = left
        time.sleep(0.05)
    sd.wait()
    return clip.reshape(-1)


def try_model(model: AsrModel, directory: Path, samples: np.ndarray) -> CheckResult:
    if samples.size == 0 or float(np.max(np.abs(samples))) < 0.003:
        return CheckResult(False, "没有听到声音，请检查麦克风后再试")
    from .asr import LocalRecognizer

    recognizer = LocalRecognizer(directory, model.id)
    try:
        started = time.monotonic()
        text = recognizer.transcribe(samples)
        spent = time.monotonic() - started
    finally:
        recognizer.close()
    if not text.strip():
        return CheckResult(False, "没有识别出文字，请靠近麦克风、说清楚一些再试")
    return CheckResult(True, f"“{text.strip()}” · 识别用时 {spent:.1f} 秒")
