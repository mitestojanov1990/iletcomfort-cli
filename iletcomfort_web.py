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
        merged.update({k: v for k, v in env.items() if v.strip() != ""})

        missing = [
            k
            for k in ("ILETCOMFORT_ACCOUNT", "ILETCOMFORT_PASSWORD", "WEBUI_PASSWORD")
            if not merged.get(k, "").strip()
        ]
        if missing:
            raise ConfigError(
                "Missing required configuration: " + ", ".join(missing)
            )

        secret_key = merged.get("WEBUI_SECRET_KEY", "")
        if not secret_key and secret_key_path is not None:
            if secret_key_path.exists():
                try:
                    secret_key = secret_key_path.read_text().strip()
                except OSError:
                    pass
            if not secret_key:
                secret_key = secrets.token_hex(32)
                try:
                    secret_key_path.write_text(secret_key)
                    try:
                        secret_key_path.chmod(0o600)
                    except OSError:
                        pass
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


import hmac
from functools import wraps

from flask import (
    Flask,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


def create_app(config: Config, client) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.webui_secret_key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            submitted = request.form.get("password", "")
            if hmac.compare_digest(submitted, config.webui_password):
                session.clear()
                session["authed"] = True
                return redirect(url_for("appliances"))
            return render_template("login.html", error="Wrong password")
        return render_template("login.html", error=None)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @require_auth
    def appliances():
        try:
            items = client.list_appliances()
        except Exception as e:
            return render_template("appliances.html", appliances=[], error=str(e))
        if len(items) == 1:
            return redirect(url_for("device", code=items[0]["applianceCode"]))
        return render_template("appliances.html", appliances=items, error=None)

    @app.route("/device/<code>")
    @require_auth
    def device(code):
        return f"device {code} placeholder"

    return app


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped
