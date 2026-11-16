from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


class ConfigurationError(ValueError):
    """Raised when required or unsafe configuration is supplied."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable is missing: {name}")
    return value


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("LITELLM_BASE_URL must be an absolute HTTP(S) URL")
    if parsed.path not in {"", "/"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("LITELLM_BASE_URL must not contain a path, credentials, query, or fragment")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class Settings:
    litellm_base_url: str
    litellm_api_key: str
    mcp_bearer_token: str
    allowed_search_tools: frozenset[str]
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    refresh_seconds: int
    request_timeout_seconds: int
    max_response_bytes: int
    host: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        token = _required("MCP_BEARER_TOKEN")
        if len(token) < 32 or not token.isascii():
            raise ConfigurationError("MCP_BEARER_TOKEN must contain at least 32 ASCII characters")
        api_key = _required("LITELLM_API_KEY")
        if token == api_key:
            raise ConfigurationError("MCP_BEARER_TOKEN and LITELLM_API_KEY must be different secrets")
        allowed_hosts = _csv("ADAPTER_ALLOWED_HOSTS", "localhost:8080,127.0.0.1:8080")
        if not allowed_hosts:
            raise ConfigurationError("ADAPTER_ALLOWED_HOSTS must contain at least one host")
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return cls(
            litellm_base_url=_base_url(_required("LITELLM_BASE_URL")),
            litellm_api_key=api_key,
            mcp_bearer_token=token,
            allowed_search_tools=frozenset(_csv("LITELLM_ALLOWED_SEARCH_TOOLS")),
            allowed_hosts=allowed_hosts,
            allowed_origins=_csv("ADAPTER_ALLOWED_ORIGINS"),
            refresh_seconds=_integer("DISCOVERY_REFRESH_SECONDS", 30, 10, 3600),
            request_timeout_seconds=_integer("LITELLM_TIMEOUT_SECONDS", 30, 5, 120),
            max_response_bytes=_integer("MAX_RESPONSE_BYTES", 2_000_000, 1024, 20_000_000),
            host=os.getenv("ADAPTER_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=_integer("ADAPTER_PORT", 8080, 1, 65535),
            log_level=log_level,
        )
