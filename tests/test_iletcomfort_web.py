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


def _short_status():
    from iletcomfort_client import ITSStatus

    s = ITSStatus()
    s.firmware_variant = "its_short"
    s.live_ops_raw = 0x64
    s.live_heat = False
    s.live_dhw = True
    s.live_tbh = True
    s.live_fast_dhw = True
    s.zone1_mode = "Heat"
    s.zone1_setpoint = 35
    s.zone1_room_temp = 30
    s.dhw_setpoint_v = 45
    s.water_outlet_temp = 31
    s.raw_body = bytes(
        [0x01, 0x64, 0x17, 0x90, 0x03, 0x03, 0x23, 0x1E, 0x2D, 0x30,
         0x41, 0x23, 0x19, 0x05, 0x37, 0x19, 0x19, 0x05, 0x3C, 0x22,
         0x3C, 0x14, 0x1F, 0x00, 0x80]
    )
    return s


def _short_sensors():
    from iletcomfort_client import ITSSensors

    s = ITSSensors()
    s.firmware_variant = "its_short"
    s.raw_body = bytes(range(38))
    return s


def _appliance(code="AAA111"):
    return [{
        "applianceCode": code,
        "name": "Test Pump",
        "applianceType": "0xC3",
        "online": 1,
        "owner": True,
        "sn": "SN-T",
        "sn8": "171H120F",
    }]


def test_device_renders_short_variant_status(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    mock_client.get_appliance_info.return_value = {"name": "Test Pump", "sn": "SN-T"}
    mock_client.query_status.return_value = _short_status()
    mock_client.query_sensors.return_value = _short_sensors()

    resp = client.get("/device/AAA111")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Test Pump" in body
    assert "DHW" in body
    assert "TBH" in body
    assert "Fast DHW" in body
    assert "35" in body  # zone setpoint
    assert "45" in body  # DHW setpoint
    assert "decode unavailable" in body.lower()  # short-variant sensors


def test_device_unknown_code_returns_404(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    resp = client.get("/device/NOSUCH")
    assert resp.status_code == 404


def test_device_auth_error_triggers_one_relogin(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    mock_client.get_appliance_info.return_value = {"name": "Test"}

    auth_err = Exception("Access token expired or invalid. ...")
    mock_client.query_status.side_effect = [auth_err, _short_status()]
    mock_client.query_sensors.return_value = _short_sensors()

    resp = client.get("/device/AAA111")
    assert resp.status_code == 200
    assert mock_client.login.call_count == 1
    mock_client.login.assert_called_with(
        account="user@example.com", password="pw"
    )
    assert mock_client.query_status.call_count == 2  # original + retry


def test_device_auth_error_relogin_then_failure_renders_inline_error(
    client, mock_client
):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    mock_client.get_appliance_info.return_value = {"name": "Test"}

    auth_err = Exception("code=14005")
    mock_client.query_status.side_effect = [auth_err, auth_err]
    mock_client.query_sensors.return_value = _short_sensors()

    resp = client.get("/device/AAA111")
    assert resp.status_code == 200
    assert b"14005" in resp.data or b"could not load status" in resp.data.lower()


def test_device_transient_1214_renders_inline_error(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    mock_client.get_appliance_info.return_value = {"name": "Test"}
    mock_client.query_status.side_effect = Exception("code=1214, msg=System error")
    mock_client.query_sensors.return_value = _short_sensors()

    resp = client.get("/device/AAA111")
    assert resp.status_code == 200
    assert b"1214" in resp.data or b"temporarily unreachable" in resp.data.lower()
    # query_status was tried exactly once -- 1214 must not retry.
    assert mock_client.query_status.call_count == 1


def test_device_metadata_failure_does_not_break_status_card(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    mock_client.get_appliance_info.side_effect = Exception("network down")
    mock_client.query_status.return_value = _short_status()
    mock_client.query_sensors.return_value = _short_sensors()

    resp = client.get("/device/AAA111")
    assert resp.status_code == 200
    assert b"network down" in resp.data
    assert b"Zone 1" in resp.data  # status card still rendered


def test_device_raw_renders_hex_dump(client, mock_client):
    _login(client)
    mock_client.list_appliances.return_value = _appliance("AAA111")
    mock_client.get_appliance_info.return_value = {"name": "Test"}
    mock_client.query_status.return_value = _short_status()
    mock_client.query_sensors.return_value = _short_sensors()

    resp = client.get("/device/AAA111/raw")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "01 64 17 90" in body  # status raw bytes (first four)
    assert "Raw status frame" in body
    assert "Raw sensor frame" in body


def test_appliances_list_failure_renders_inline_error(client, mock_client):
    _login(client)
    mock_client.list_appliances.side_effect = Exception("network down")
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"network down" in resp.data
    assert b"Could not load appliances" in resp.data
