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
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)


def _is_auth_error(exc: Exception) -> bool:
    s = str(exc)
    return "14005" in s or "expired or invalid" in s


def _call_with_relogin(client, config: Config, fn, *args, **kwargs):
    """Call fn(...). On auth error, re-login once and retry exactly once."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        if not _is_auth_error(e):
            raise
        client.login(account=config.iletcomfort_account, password=config.iletcomfort_password)
        try:
            client.save_token()
        except Exception:
            pass
        return fn(*args, **kwargs)


def _safe_call(retrying_caller, client, config: Config, fn, *args, **kwargs):
    """Run retrying_caller; return {'value': result, 'error': None} or
    {'value': None, 'error': str(exc)} on failure. Never raises."""
    try:
        result = retrying_caller(client, config, fn, *args, **kwargs)
        return {"value": result, "error": None}
    except Exception as e:
        return {"value": None, "error": str(e)}


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


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
        try:
            appliances_list = client.list_appliances()
        except Exception as e:
            appliances_list = None
            list_error = str(e)
        else:
            list_error = None

        if appliances_list is not None:
            known = {a["applianceCode"] for a in appliances_list}
            if code not in known:
                abort(404)

        metadata = _safe_call(_call_with_relogin, client, config, client.get_appliance_info, code)
        status_obj = _safe_call(_call_with_relogin, client, config, client.query_status, code)
        sensors_obj = _safe_call(_call_with_relogin, client, config, client.query_sensors, code)

        meta_rows: list[tuple[str, object]] = []
        if metadata["value"] is not None:
            for k in ("name", "sn", "sn8", "modelNumber", "online", "owner"):
                if k in metadata["value"]:
                    meta_rows.append((k, metadata["value"][k]))

        return render_template(
            "device.html",
            code=code,
            name=(metadata["value"] or {}).get("name") if metadata["value"] else None,
            metadata={"rows": meta_rows, "error": metadata["error"]},
            status=status_obj,
            sensors=sensors_obj,
            updated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            list_error=list_error,
        )

    @app.route("/device/<code>/raw")
    @require_auth
    def device_raw(code):
        return f"raw {code} placeholder"

    return app
