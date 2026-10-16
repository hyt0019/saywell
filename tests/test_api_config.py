import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from saywell.api_config import chat_completion_url, credential_account
from saywell.cleanup import ApiCleaner, CleanupError
from saywell.settings import Settings, read_key, save_key


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://provider.example/v1/", "https://provider.example/v1/chat/completions"),
        ("https://provider.example/v1/chat/completions", "https://provider.example/v1/chat/completions"),
        ("http://localhost:1234/v1", "http://localhost:1234/v1/chat/completions"),
        ("https://PROVIDER.example:443", "https://provider.example/chat/completions"),
    ],
)
def test_api_base_and_full_endpoint(url, expected):
    assert chat_completion_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "provider.example",
        "ftp://provider.example",
        "https://user:secret@provider.example",
        "https://provider.example?api_key=secret",
        "https://provider.example/#fragment",
    ],
)
def test_invalid_urls_do_not_include_sensitive_input_in_errors(url):
    with pytest.raises(ValueError) as error:
        chat_completion_url(url)
    assert "secret" not in str(error.value)


def api_reply(content=None):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": content or json.dumps({"text": "明天去。", "edits": [], "warnings": []})
                    },
                }
            ]
        },
    )


@pytest.mark.parametrize(
    "base", ["https://provider-a.example/v1", "https://provider-b.example/api/v4/chat/completions"]
)
def test_custom_provider_uses_selected_model_key_and_no_vendor_extension(base):
    def handler(request):
        assert str(request.url) == chat_completion_url(base)
        assert request.headers["Authorization"] == "Bearer custom-test-key"
        body = json.loads(request.content)
        assert body["model"] == "custom-model"
        assert "thinking" not in body
        return api_reply()

    result = ApiCleaner(
        "custom-test-key", "custom-model", base_url=base, transport=httpx.MockTransport(handler)
    ).clean("嗯，明天去")
    assert result.text == "明天去。"


def test_gateway_rejecting_optional_parameters_is_retried_with_basic_request():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "Unsupported response_format"})
        if "max_tokens" in body:
            return httpx.Response(422, json={"error": "Use another field instead of max_tokens"})
        return api_reply('```json\n{"text":"明天去。","edits":[],"warnings":[]}\n```')

    result = ApiCleaner(
        "test", base_url="https://provider.example/v1", transport=httpx.MockTransport(handler)
    ).clean("明天去")
    assert result.text == "明天去。"
    assert len(bodies) == 3
    assert "response_format" not in bodies[-1] and "max_tokens" not in bodies[-1]


def test_wrong_model_is_not_retried_as_a_format_error():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "Unknown model"})

    with pytest.raises(CleanupError, match="HTTP 400"):
        ApiCleaner("test", transport=httpx.MockTransport(handler)).clean("你好")
    assert len(calls) == 1


def test_credentials_are_isolated_and_old_deepseek_credentials_still_work(monkeypatch, tmp_path):
    # Saved under the app's former name ("better-voice-input") and the original "deepseek" account.
    store = {("better-voice-input", "deepseek"): "old-deepseek-key"}
    monkeypatch.setitem(
        sys.modules,
        "keyring",
        SimpleNamespace(
            get_password=lambda service, account: store.get((service, account)),
            set_password=lambda service, account, key: store.update({(service, account): key}),
            delete_password=lambda service, account: store.pop((service, account)),
        ),
    )
    for name in ("DEEPSEEK_API_KEY", "SAYWELL_API_KEY", "SAYWELL_API_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("saywell.settings.project_root", lambda: tmp_path)
    assert read_key() == "old-deepseek-key"
    assert store == {("saywell", "deepseek"): "old-deepseek-key"}  # Moved to the new name.
    assert read_key(api_base_url="https://api.deepseek.com/v1") == "old-deepseek-key"
    custom = "https://provider.example/v1"
    assert read_key(api_base_url=custom) == ""
    save_key("new-key", api_base_url=custom)
    assert read_key(api_base_url=custom + "/chat/completions") == "new-key"
    assert read_key() == "old-deepseek-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-deepseek-key")
    assert read_key(api_base_url="https://api.deepseek.com.evil.example") == ""
    assert read_key(api_base_url="http://api.deepseek.com") == ""
    assert credential_account(custom) != credential_account("https://another.example/v1")


def test_generic_environment_key_is_bound_to_its_endpoint(monkeypatch):
    monkeypatch.setenv("SAYWELL_API_KEY", "paired-key")
    monkeypatch.setenv("SAYWELL_API_BASE_URL", "https://provider.example/v1")
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=lambda *args: None))
    assert read_key(api_base_url="https://provider.example/v1/chat/completions") == "paired-key"
    assert read_key(api_base_url="https://other.example/v1") == ""


def test_custom_api_settings_round_trip_without_key(tmp_path):
    value = Settings(api_base_url="https://provider.example/v1", model="custom-model", api_timeout=60.0)
    path = tmp_path / "settings.json"
    value.save(path)
    assert Settings.load(path) == value
    assert "api_key" not in path.read_text(encoding="utf-8")
