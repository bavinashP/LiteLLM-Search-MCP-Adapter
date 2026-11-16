import pytest

from adapter.config import ConfigurationError, Settings, _base_url


def test_base_url_rejects_embedded_credentials():
    with pytest.raises(ConfigurationError):
        _base_url("https://user:password@example.test")


def test_base_url_rejects_non_root_path():
    with pytest.raises(ConfigurationError):
        _base_url("https://example.test/litellm")


def test_base_url_rejects_query_and_fragment():
    with pytest.raises(ConfigurationError):
        _base_url("https://example.test?token=x")


def test_settings_requires_server_side_secrets(monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    with pytest.raises(ConfigurationError):
        Settings.from_env()


def test_settings_defaults_and_empty_allowlist(monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm.internal:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "server-secret")
    monkeypatch.setenv("MCP_BEARER_TOKEN", "c" * 64)
    monkeypatch.delenv("LITELLM_ALLOWED_SEARCH_TOOLS", raising=False)
    settings = Settings.from_env()
    assert settings.refresh_seconds == 30
    assert settings.allowed_search_tools == frozenset()
