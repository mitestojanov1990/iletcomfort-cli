"""Flask web UI for iLetComfort.

Read-only dashboard that reuses ILetComfortClient and its on-disk token
cache. See docs/superpowers/specs/2026-05-05-web-ui-design.md for design.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Config:
    iletcomfort_account: str
    iletcomfort_password: str
    webui_password: str
    webui_secret_key: str
    iletcomfort_api_base: str = "https://us.dollin.net"
    webui_host: str = "127.0.0.1"
    webui_port: int = 8000

    @classmethod
    def from_env(
        cls,
        env: dict[str, str],
        env_file: Path | None,
        secret_key_path: Path | None,
    ) -> "Config":
        merged: dict[str, str] = {}
        if env_file is not None and env_file.exists():
            merged.update(_parse_env_file(env_file))
        merged.update({k: v for k, v in env.items() if v != ""})

        missing = [
            k
            for k in ("ILETCOMFORT_ACCOUNT", "ILETCOMFORT_PASSWORD", "WEBUI_PASSWORD")
            if not merged.get(k)
        ]
        if missing:
            raise ConfigError(
                "Missing required configuration: " + ", ".join(missing)
            )

        secret_key = merged.get("WEBUI_SECRET_KEY", "")
        if not secret_key and secret_key_path is not None:
            if secret_key_path.exists():
                secret_key = secret_key_path.read_text().strip()
            else:
                secret_key = secrets.token_hex(32)
                secret_key_path.write_text(secret_key)
                try:
                    secret_key_path.chmod(0o600)
                except OSError:
                    pass
        if not secret_key:
            secret_key = secrets.token_hex(32)

        port_str = merged.get("WEBUI_PORT", "8000")
        try:
            port = int(port_str)
        except ValueError as e:
            raise ConfigError(f"WEBUI_PORT must be an integer, got {port_str!r}") from e

        return cls(
            iletcomfort_account=merged["ILETCOMFORT_ACCOUNT"],
            iletcomfort_password=merged["ILETCOMFORT_PASSWORD"],
            webui_password=merged["WEBUI_PASSWORD"],
            webui_secret_key=secret_key,
            iletcomfort_api_base=merged.get(
                "ILETCOMFORT_API_BASE", "https://us.dollin.net"
            ),
            webui_host=merged.get("WEBUI_HOST", "127.0.0.1"),
            webui_port=port,
        )


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out
