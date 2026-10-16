from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

DEFAULT_API_BASE_URL = "https://api.deepseek.com"


def chat_completion_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or any(c.isspace() for c in value)
        ):
            raise ValueError
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            host += f":{port}"
        path = parts.path.rstrip("/")
        if not path.endswith("/chat/completions"):
            path += "/chat/completions"
        return urlunsplit((parts.scheme, host, path, "", ""))
    except (ValueError, UnicodeError):
        raise ValueError("请填写有效的 HTTP(S) API 地址，密钥请放在 API Key 栏。") from None


def is_deepseek_api(value: str) -> bool:
    endpoint = urlsplit(chat_completion_url(value))
    return (
        endpoint.scheme == "https"
        and endpoint.netloc == "api.deepseek.com"
        and endpoint.path in ("/chat/completions", "/v1/chat/completions")
    )


def credential_account(value: str) -> str:
    endpoint = chat_completion_url(value)
    # Preserve credentials saved by earlier releases, only for DeepSeek's API.
    if is_deepseek_api(endpoint):
        return "deepseek"
    return "api-" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
