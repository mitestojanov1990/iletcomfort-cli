"""Tests for iletcomfort_web."""
from __future__ import annotations

import pytest


def test_module_imports(import_web_module):
    assert import_web_module is not None


def test_config_from_env_happy_path(import_web_module):
    cfg = import_web_module.Config.from_env(
        env={
            "ILETCOMFORT_ACCOUNT": "user@example.com",
            "ILETCOMFORT_PASSWORD": "pw",
            "WEBUI_PASSWORD": "secret",
            "WEBUI_SECRET_KEY": "abc",
        },
        env_file=None,
        secret_key_path=None,
    )
    assert cfg.iletcomfort_account == "user@example.com"
    assert cfg.iletcomfort_password == "pw"
    assert cfg.webui_password == "secret"
    assert cfg.webui_secret_key == "abc"
    assert cfg.iletcomfort_api_base == "https://us.dollin.net"
    assert cfg.webui_host == "127.0.0.1"
    assert cfg.webui_port == 8000


def test_config_missing_required_raises(import_web_module):
    with pytest.raises(import_web_module.ConfigError) as ei:
        import_web_module.Config.from_env(env={}, env_file=None, secret_key_path=None)
    msg = str(ei.value)
    assert "ILETCOMFORT_ACCOUNT" in msg
    assert "ILETCOMFORT_PASSWORD" in msg
    assert "WEBUI_PASSWORD" in msg


def test_config_env_file_supplements_env(tmp_path, import_web_module):
    env_file = tmp_path / ".iletcomfort_web.env"
    env_file.write_text(
        "# comment line\n"
        "ILETCOMFORT_ACCOUNT=file@example.com\n"
        "\n"
        "ILETCOMFORT_PASSWORD = filepw \n"
        "WEBUI_PASSWORD=wpw\n"
        "WEBUI_SECRET_KEY=k\n"
    )
    cfg = import_web_module.Config.from_env(env={}, env_file=env_file, secret_key_path=None)
    assert cfg.iletcomfort_account == "file@example.com"
    assert cfg.iletcomfort_password == "filepw"


def test_config_env_overrides_env_file(tmp_path, import_web_module):
    env_file = tmp_path / ".iletcomfort_web.env"
    env_file.write_text("ILETCOMFORT_ACCOUNT=file@example.com\n")
    cfg = import_web_module.Config.from_env(
        env={
            "ILETCOMFORT_ACCOUNT": "env@example.com",
            "ILETCOMFORT_PASSWORD": "pw",
            "WEBUI_PASSWORD": "wpw",
            "WEBUI_SECRET_KEY": "k",
        },
        env_file=env_file,
        secret_key_path=None,
    )
    assert cfg.iletcomfort_account == "env@example.com"


def test_config_autogenerates_secret_key(tmp_path, import_web_module):
    secret_path = tmp_path / "secret"
    cfg = import_web_module.Config.from_env(
        env={
            "ILETCOMFORT_ACCOUNT": "u@example.com",
            "ILETCOMFORT_PASSWORD": "pw",
            "WEBUI_PASSWORD": "wpw",
        },
        env_file=None,
        secret_key_path=secret_path,
    )
    assert len(cfg.webui_secret_key) == 64  # secrets.token_hex(32) -> 64 hex chars
    assert secret_path.exists()
    assert secret_path.read_text().strip() == cfg.webui_secret_key


def test_config_reuses_persisted_secret_key(tmp_path, import_web_module):
    secret_path = tmp_path / "secret"
    secret_path.write_text("persisted-key-value")
    cfg = import_web_module.Config.from_env(
        env={
            "ILETCOMFORT_ACCOUNT": "u@example.com",
            "ILETCOMFORT_PASSWORD": "pw",
            "WEBUI_PASSWORD": "wpw",
        },
        env_file=None,
        secret_key_path=secret_path,
    )
    assert cfg.webui_secret_key == "persisted-key-value"


def test_config_port_parsed_as_int(import_web_module):
    cfg = import_web_module.Config.from_env(
        env={
            "ILETCOMFORT_ACCOUNT": "u@example.com",
            "ILETCOMFORT_PASSWORD": "pw",
            "WEBUI_PASSWORD": "wpw",
            "WEBUI_SECRET_KEY": "k",
            "WEBUI_PORT": "9001",
        },
        env_file=None,
        secret_key_path=None,
    )
    assert cfg.webui_port == 9001


def test_config_whitespace_only_env_var_is_treated_as_missing(import_web_module):
    with pytest.raises(import_web_module.ConfigError) as ei:
        import_web_module.Config.from_env(
            env={
                "ILETCOMFORT_ACCOUNT": "   ",
                "ILETCOMFORT_PASSWORD": "pw",
                "WEBUI_PASSWORD": "wpw",
                "WEBUI_SECRET_KEY": "k",
            },
            env_file=None,
            secret_key_path=None,
        )
    assert "ILETCOMFORT_ACCOUNT" in str(ei.value)


def test_config_invalid_port_raises(import_web_module):
    with pytest.raises(import_web_module.ConfigError) as ei:
        import_web_module.Config.from_env(
            env={
                "ILETCOMFORT_ACCOUNT": "u@example.com",
                "ILETCOMFORT_PASSWORD": "pw",
                "WEBUI_PASSWORD": "wpw",
                "WEBUI_SECRET_KEY": "k",
                "WEBUI_PORT": "not-a-number",
            },
            env_file=None,
            secret_key_path=None,
        )
    assert "WEBUI_PORT" in str(ei.value)


def test_config_env_file_skips_lines_without_equals(tmp_path, import_web_module):
    env_file = tmp_path / ".iletcomfort_web.env"
    env_file.write_text(
        "BARE_LINE_NO_EQUALS\n"
        "ILETCOMFORT_ACCOUNT=u@example.com\n"
        "ILETCOMFORT_PASSWORD=pw\n"
        "WEBUI_PASSWORD=wpw\n"
        "WEBUI_SECRET_KEY=k\n"
    )
    cfg = import_web_module.Config.from_env(
        env={}, env_file=env_file, secret_key_path=None
    )
    assert cfg.iletcomfort_account == "u@example.com"


def test_login_get_renders_form(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"password" in resp.data.lower()


def test_login_post_wrong_password_rerenders_with_error(client):
    resp = client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 200
    assert b"wrong password" in resp.data.lower()


def test_login_post_correct_password_redirects_and_sets_cookie(client):
    resp = client.post("/login", data={"password": "secret"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")
    cookies = resp.headers.getlist("Set-Cookie")
    assert any("session=" in c for c in cookies)


def test_unauthed_root_redirects_to_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_logout_clears_session(client):
    client.post("/login", data={"password": "secret"})
    resp = client.post("/logout")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    follow = client.get("/")
    assert follow.status_code == 302
    assert "/login" in follow.headers["Location"]


def _login(c):
    c.post("/login", data={"password": "secret"})


def test_appliances_renders_two_devices(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = [
        {
            "applianceCode": "AAA111",
            "name": "Living Room",
            "applianceType": "0xC3",
            "online": 1,
            "owner": True,
            "sn": "SN-A",
            "sn8": "171H120F",
        },
        {
            "applianceCode": "BBB222",
            "name": "Cabin",
            "applianceType": "0xC3",
            "online": 0,
            "owner": False,
            "sn": "SN-B",
            "sn8": "171000AU",
        },
    ]
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Living Room" in body
    assert "AAA111" in body
    assert "Cabin" in body
    assert "BBB222" in body


def test_appliances_redirects_when_one_device(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = [
        {
            "applianceCode": "ONLY1",
            "name": "Solo",
            "applianceType": "0xC3",
            "online": 1,
            "owner": True,
            "sn": "S",
            "sn8": "S",
        }
    ]
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/device/ONLY1" in resp.headers["Location"]


def test_appliances_empty_list_renders_message(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = []
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"no appliances" in resp.data.lower()
