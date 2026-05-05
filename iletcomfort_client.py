#!/usr/bin/env python3
"""
iLetComfort / ITS/BTRI Heat Pump Cloud API Client.

A complete Python API client for interacting with ITS/BTRI cloud-connected
heat pumps (device type 0xC3) used by the iLetComfort app.

Protocol decoding uses the ITS/BTRI proprietary byte layout. Temperature fields use
a +35 offset encoding (actual = raw - 35), with 0xEF (204 after offset)
indicating a disconnected sensor.

Supports:
  - Authentication via the Dollin cloud API (v1 login)
  - Appliance discovery and info retrieval (v2.0 business API)
  - Sending C3 heat pump hex commands (query/control)
  - Decoding ITS protocol responses (status 0x01, sensors 0x02)
Usage:
  python3 iletcomfort_client.py login --account EMAIL --password PLAINTEXT_PW
  python3 iletcomfort_client.py list
  python3 iletcomfort_client.py info [--appliance CODE]
  python3 iletcomfort_client.py status [--appliance CODE]
  python3 iletcomfort_client.py sensors [--appliance CODE]
  python3 iletcomfort_client.py set --mode heat [--temp 28] [--appliance CODE]
  python3 iletcomfort_client.py set --boost on
  python3 iletcomfort_client.py set --mute 1
  python3 iletcomfort_client.py raw HEX_COMMAND [--appliance CODE]
  python3 iletcomfort_client.py set-token TOKEN
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import random as random_module
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding as crypto_padding

# ---------------------------------------------------------------------------
# App constants extracted from the iLetComfort iOS binary
# ---------------------------------------------------------------------------
APP_SECRET = "SIT_4VjZdg19laDoIrut"
APP_KEY = "btri"
IOT_KEY = "meicloud"  # Used as prefix for login signing (instead of APP_KEY)
CLIENT_ID = "d056337d77334ecc95aca4bff6025533"
CLIENT_SECRET = "35b965531383ce9f37f829a19712bf3a"
ENCRYPT_KEY = "4dbc9ff6c15944d78eebb581c2b23de3"
APP_ID = "8010"
API_BASE = "https://us.dollin.net"

# C3 heat pump device type
DEVICE_TYPE_C3 = 0xC3  # 195

# Token file location
TOKEN_FILE = Path.home() / ".iletcomfort_token"

# ITS protocol: many temperature fields are encoded as (raw_byte - 35).
# A raw value of 0xEF (239) yields 204 after offset, meaning sensor disconnected.
TEMP_OFFSET = 35
SENSOR_DISCONNECTED = 204  # (0xEF - 35) indicates sensor fault / not connected

# SET command: operating modes (as sent in the SET frame, different from query response!)
# Query response: 0=Off, 1=Heat, 2=Cool, 3=Auto, 4=WaterPump
# SET command:    0=Off, 1=Heat, 3=Cool, 4=WaterPump
MODE_OFF = 0x00
MODE_HEAT = 0x01
MODE_COOL = 0x03
MODE_WATERPUMP = 0x04
MODE_MAP: dict[str, int] = {
    "off": MODE_OFF,
    "heat": MODE_HEAT,
    "cool": MODE_COOL,
    "waterpump": MODE_WATERPUMP,
}

# Temperature validation ranges per mode (Celsius)
TEMP_RANGES: dict[int, tuple[int, int]] = {
    MODE_HEAT: (10, 40),
    MODE_COOL: (12, 30),
    MODE_WATERPUMP: (15, 75),
}


# ---------------------------------------------------------------------------
# Signing algorithms (verified against captured traffic)
# ---------------------------------------------------------------------------

def sign_v1(json_body: str, *, use_iot_key: bool = False) -> tuple[str, str]:
    """Compute the v1 API signature.

    Scheme 1 -- used for /v1/ endpoints.
    sign = hex(HMAC-SHA256(key=appSecret, msg=prefix + jsonBody + random))

    The prefix depends on the endpoint:
      - Login endpoints use IOT_KEY ("meicloud") as the prefix
      - All other v1 endpoints use APP_KEY ("btri") as the prefix

    Both verified against captured traffic from the iLetComfort iOS app.

    The random value format matches the iOS app's createRandomString:
      yyyyMMddHHmmss + arc4random()%65536

    Args:
        json_body: The JSON body string to sign.
        use_iot_key: If True, use IOT_KEY ("meicloud") prefix instead of APP_KEY.
                     Must be True for login requests.

    Returns:
        Tuple of (sign_hex, random_value).
    """
    timestamp_fmt = time.strftime("%Y%m%d%H%M%S")
    random_suffix = str(random_module.randint(0, 65535))
    random_value = timestamp_fmt + random_suffix

    prefix = IOT_KEY if use_iot_key else APP_KEY
    message = prefix + json_body + random_value
    signature = hmac.new(
        APP_SECRET.encode("ascii"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return signature, random_value


def encrypt_password(plaintext_password: str) -> str:
    """Encrypt a plaintext password for the Dollin v1 login API.

    Reverse-engineered from the iLetComfort iOS binary (OEMPlusDataKit framework):
      +[MideaSecurity encryptDataBeforeLogin:]

    Algorithm (encryptVersion=1):
      1. SHA256(plaintext_password) -> 64-char lowercase hex digest
      2. Derive AES-128-CBC key material from SHA256(ENCRYPT_KEY):
           aes_key = sha256(ENCRYPT_KEY)[0:16]  (first 16 ASCII hex chars)
           aes_iv  = sha256(ENCRYPT_KEY)[16:32]  (next 16 ASCII hex chars)
      3. AES-128-CBC encrypt the hex digest with PKCS7 padding
      4. Return as lowercase hex string (160 chars = 80 bytes)

    Args:
        plaintext_password: The user's plaintext password.

    Returns:
        Encrypted password hex string ready for the login API body.
    """
    # Step 1: SHA256 the plaintext password
    password_hash_hex = hashlib.sha256(plaintext_password.encode("utf-8")).hexdigest()

    # Step 2: Derive AES key and IV from ENCRYPT_KEY
    key_material = hashlib.sha256(ENCRYPT_KEY.encode("utf-8")).hexdigest()
    aes_key = key_material[0:16].encode("ascii")   # 16 bytes -> AES-128
    aes_iv = key_material[16:32].encode("ascii")    # 16 bytes IV

    # Step 3: AES-128-CBC encrypt with PKCS7 padding
    padder = crypto_padding.PKCS7(128).padder()
    padded_data = padder.update(password_hash_hex.encode("utf-8")) + padder.finalize()

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()

    # Step 4: Return as lowercase hex
    return ciphertext.hex()


def sign_v2(method: str, path: str, body: str) -> str:
    """Compute the v2.0 business API signature.

    Scheme 2 -- used for /midea/open/business/v1/ endpoints.
    Verified against 5/5 captured requests.
    signature = base64(HMAC-SHA256(key=clientSecret, msg=METHOD + PATH + BODY))

    Returns:
        Base64-encoded signature string.
    """
    message = method + path + body
    sig_bytes = hmac.new(
        CLIENT_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(sig_bytes).decode("utf-8")


# ---------------------------------------------------------------------------
# C3 heat pump protocol -- frame construction
# ---------------------------------------------------------------------------

def build_c3_query(subtype: int) -> str:
    """Build a C3 query command frame.

    Frame structure:
      [AA] [length] [C3] [00*5] [00=proto_ver] [03=query] [subtype] [checksum]

    The header is 10 bytes (indices 0-9). The body is [subtype] only.
    Length byte = header_length + body_length = 10 + 1 = 11.

    Args:
        subtype: Query subtype (0x01=status, 0x02=sensors).

    Returns:
        Hex-encoded command string (no separators).
    """
    # 10-byte header: AA, length, device_type, padding*5, protocol_version, message_type
    header = [
        0xAA,               # [0] start byte
        0x00,               # [1] length (filled below)
        DEVICE_TYPE_C3,     # [2] device type
        0x00, 0x00, 0x00,   # [3-5] padding
        0x00, 0x00,         # [6-7] padding (frame ID, frame protocol version)
        0x00,               # [8] device protocol version
        0x03,               # [9] message type = query
    ]

    # Body: body_type only (subtype byte, no extra parameter)
    body = [subtype]

    # Length = header_size + body_size (counts all bytes from [1] onward excluding checksum)
    header[1] = len(header) + len(body)

    frame = header + body

    # Checksum: (~sum(stream[1:]) + 1) & 0xFF
    checksum = (~sum(frame[1:]) + 1) & 0xFF
    frame.append(checksum)

    return bytes(frame).hex()


def build_c3_set(
    mode: int,
    temperature: int,
    status_body: bytearray,
    mute_level: int = 0,
    ctrl_flag: int = 0,
) -> str:
    """Build a C3 SET command frame (62 bytes).

    The SET frame must echo certain bytes from the most recent subtype 0x01
    status response. This ensures the device accepts the command.

    Frame structure (62 bytes total):
      [0]     0xAA       start
      [1]     0x3D       length (61)
      [2]     0xC3       device type
      [3-8]   0x00       padding
      [9]     0x02       msg_type = SET
      [10]    0x01       subtype
      [11]    0x01       control_type
      [12]    MODE       0x00=off, 0x01=heat, 0x03=cool, 0x04=waterpump
      [13]    TEMP       target_temperature + 35
      [14-23] STATUS_ECHO  bytes from last status query
      [24]    0x00
      [25]    MUTE_LEV   0x00=level1, 0x01=level2
      [26]    CTRL_FLAG  0x00=normal, 0x01=mute, 0x02=boost
      [27]    0x00
      [28]    STATUS_ECHO  another byte from status
      [29-60] 0x00       zeros
      [61]    CHECKSUM   two's complement

    Args:
        mode: Operating mode (MODE_OFF, MODE_HEAT, MODE_COOL, MODE_WATERPUMP).
        temperature: Target temperature in Celsius (encoded as temp + 35).
        status_body: Raw body from the most recent subtype 0x01 query response.
        mute_level: 0=level1, 1=level2.
        ctrl_flag: 0=normal, 1=mute, 2=boost.

    Returns:
        Hex-encoded command string (62 bytes = 124 hex chars).
    """
    d = 1  # data_offset in status body (skip subtype byte)
    frame = bytearray(62)

    # Header
    frame[0] = 0xAA
    frame[1] = 0x3D  # 61
    frame[2] = DEVICE_TYPE_C3

    # SET message type and subtypes
    frame[9] = 0x02   # msg_type = SET
    frame[10] = 0x01  # subtype
    frame[11] = 0x01  # control_type

    # Mode and temperature
    frame[12] = mode
    frame[13] = temperature + TEMP_OFFSET

    # Status echo bytes from subtype 0x01 response body
    # [14] = d+4  (sterilize_hour)
    # [15] = d+5  (t5s_def)
    # [16] = d+6  (version)
    # [17] = 0x00
    # [18] = d+8  (sterilize_temp encoding)
    # [19] = d+9  (sterilize_cycle)
    # [20-22] = 0x00
    # [23] = d+13 (status flags)
    # [28] = d+18 (tr_temperature)
    if len(status_body) > d + 18:
        frame[14] = status_body[d + 4]
        frame[15] = status_body[d + 5]
        frame[16] = status_body[d + 6]
        # frame[17] = 0x00 (already zero)
        frame[18] = status_body[d + 8]
        frame[19] = status_body[d + 9]
        # frame[20..22] = 0x00 (already zero)
        frame[23] = status_body[d + 13]
        frame[28] = status_body[d + 18]

    # Mute level and control flag
    frame[25] = mute_level
    frame[26] = ctrl_flag

    # Checksum: (~sum(frame[1:-1]) + 1) & 0xFF
    frame[61] = (~sum(frame[1:61]) + 1) & 0xFF

    return frame.hex()


# ---------------------------------------------------------------------------
# C3 response parsing -- frame extraction
# ---------------------------------------------------------------------------

def parse_hex_response(hex_data: str) -> bytearray:
    """Parse a hex response string (comma-separated or continuous) into bytes."""
    hex_data = hex_data.strip()
    if "," in hex_data:
        return bytearray(int(x.strip(), 16) for x in hex_data.split(","))
    return bytearray(bytes.fromhex(hex_data))


def extract_c3_body(raw: bytearray) -> tuple[int, bytearray]:
    """Extract the body from a C3 response frame.

    Frame layout:
      raw[0]:     0xAA header
      raw[1]:     length
      raw[2]:     device type (0xC3)
      raw[3..8]:  padding / frame info
      raw[9]:     message type (0x03 for query response)
      raw[10:-1]: body (body[0] = subtype / body_type)
      raw[-1]:    checksum

    The body returned here is raw[10:-1]. The caller uses data_offset=1
    to skip the subtype byte when decoding fields.

    Returns:
        Tuple of (body_type, body) where body = raw[10:-1].
    """
    if len(raw) < 12:
        raise ValueError(f"Response too short: {len(raw)} bytes, expected at least 12")
    if raw[0] != 0xAA:
        raise ValueError(f"Invalid header byte: 0x{raw[0]:02x}, expected 0xAA")
    if raw[2] != DEVICE_TYPE_C3:
        raise ValueError(f"Not a C3 device response: 0x{raw[2]:02x}")

    body = raw[10:-1]
    body_type = body[0] if body else 0
    return body_type, body


def _temp_offset(raw_byte: int) -> float | None:
    """Decode a temperature byte using the ITS +35 offset encoding.

    Most ITS protocol temperature fields encode as: actual_temp = raw_byte - 35.
    A raw value of 0xEF (239) gives 204, which signals a disconnected sensor.

    Returns:
        The decoded temperature in Celsius, or None if the sensor is
        disconnected (raw == 0xEF).
    """
    value = raw_byte - TEMP_OFFSET
    if value == SENSOR_DISCONNECTED:
        return None
    return float(value)


def _format_temp(value: float | None, unit: str = "C") -> str:
    """Format a temperature value for display, handling disconnected sensors."""
    if value is None:
        return "N/A (sensor disconnected)"
    return f"{value:.0f} {unit}"


def _decode_its_version(b0: int, b1: int, b2: int) -> str:
    """Decode a 3-byte ITS version field into a human-readable string.

    Encoding: byte0 = (year-2000)*2 + (month>>3),
              byte1 = ((month&7)<<5) | day,
              byte2 = version number.
    """
    year = 2000 + (b0 >> 1)
    month = ((b0 & 1) << 3) | (b1 >> 5)
    day = b1 & 0x1F
    version = b2
    return f"{year:04d}-{month:02d}-{day:02d} v{version}"


# ---------------------------------------------------------------------------
# ITS protocol response decoding -- subtype 0x01 (Status & Control)
# ---------------------------------------------------------------------------

@dataclass
class ITSStatus:
    """Decoded ITS subtype 0x01 -- status, control settings, and runtime data.

    Byte layout uses the ITS/BTRI proprietary protocol with body = frame[10:-1],
    data_offset d=1 (skip body_type byte).
    """

    # Pump status flags (d+0)
    pump_outdoor: bool = False     # bit0: outdoor pump
    pump_system: bool = False      # bit1: system pump

    # Operating mode (d+1): 0=off, 1=cool, 2=heat, 3=auto, 4=waterpump
    mode: int = 0
    mode_name: str = "Off"

    # DHW setpoints (d+2..d+4)
    t5s_def: float | None = None   # DHW default setpoint (raw - 35)
    t5s_max: float | None = None   # DHW max setpoint (raw - 35)
    set_temperature: int = 0       # DHW target (direct, no offset)

    # Config/status (d+5)
    config_status: int = 0

    # Heating temperature limits (d+6..d+7)
    td_max: float | None = None    # raw - 35
    td_min: float | None = None    # raw - 35

    # PTC / box temperatures (d+8..d+11)
    ptc_temperature_1: float | None = None   # d+8, raw - 35
    trdh_max: float | None = None            # d+9, raw - 35
    trdh_min: float | None = None            # d+10, raw - 35
    trdh_def: float | None = None            # d+11, raw - 35

    # Feature validity flags (d+12)
    mute_valid: bool = False
    force_heat_valid: bool = False
    sterilize_valid: bool = False

    # Status flags (d+13) -- compressor, IBH, sterilize, etc.
    comp_running: bool = False
    ibh_running: bool = False
    sterilize_running: bool = False
    status_flags_raw: int = 0

    # Enable flags (d+14, d+15)
    enable_flags_1: int = 0
    enable_flags_2: int = 0

    # More temperatures (d+16..d+18)
    box_bottom_temp: float | None = None    # d+16, raw - 35
    ptc_temperature: float | None = None    # d+17, raw - 35
    tr_temperature: float | None = None     # d+18, raw - 35

    # Sterilization (d+19..d+22)
    version_or_sterilize_hour: int = 0      # d+19
    sterilize_min: int = 0                  # d+20
    sterilize_temperature: float | None = None  # d+21, raw - 35
    sterilize_cycle_days: int = 0           # d+22

    # Error code (d+23)
    error_code: int = 0

    # Heat pump work limit (d+24)
    heat_pump_work_temp_limit: float | None = None  # raw - 35

    # Vacation schedule (d+25..d+28)
    vacation_start_year: int = 0
    vacation_start_month: int = 0
    vacation_start_day: int = 0
    vacation_end_month: int = 0
    vacation_end_day: int = 0

    # Extended operational data (d+35..d+48)
    exv_drg: int = 0              # 16-bit BE, EXV opening degree
    pressure_h: int = 0           # 16-bit BE, high-side pressure
    pressure_l: int = 0           # 16-bit BE, low-side pressure
    comp_frq: int = 0             # 16-bit BE, compressor frequency
    total_kwh: int = 0            # 16-bit BE, total energy kWh
    comp_total_run_hours: int = 0 # 16-bit BE
    fan_total_run_hours: int = 0  # 16-bit BE

    # ITS short-frame firmware variant. Populated only when the device returns a
    # 36-byte status frame (length byte 0x23). The spec-mapped fields above are
    # left at their defaults in that case -- ignore them and read the variant
    # fields below. Reverse-engineered against an EU ITS unit; bits 1/3/4/7 of
    # live_ops_raw are unidentified.
    firmware_variant: str = "spec"
    live_ops_raw: int | None = None      # body[d+0]: bitfield of currently-running ops
    live_heat: bool = False              # live_ops bit 0: space heating active
    live_dhw: bool = False               # live_ops bit 2: DHW heating active
    live_tbh: bool = False               # live_ops bit 5: TBH (booster heater) active
    live_fast_dhw: bool = False          # live_ops bit 6: Fast DHW boost active
    zone1_mode_raw: int | None = None    # body[d+3]: 2=Cool, 3=Heat
    zone1_mode: str | None = None
    zone1_setpoint: int | None = None    # body[d+5], direct C
    zone1_room_temp: int | None = None   # body[d+6], direct C (probable)
    dhw_setpoint_v: int | None = None    # body[d+7], direct C
    water_outlet_temp: int | None = None # body[d+21], direct C (probable)

    # Raw body for further analysis
    raw_body: bytes = field(default_factory=bytes, repr=False)


def decode_its_status(body: bytearray) -> ITSStatus:
    """Decode ITS subtype 0x01 body into an ITSStatus object.

    Uses data_offset=1 to skip the body_type byte.
    body[0] = subtype (0x01), body[1..] = actual data.
    """
    status = ITSStatus()
    status.raw_body = bytes(body)
    d = 1  # data_offset: skip body_type byte

    body_len = len(body)

    # ITS short-frame firmware variant: 36-byte frame -> ~25-byte body.
    # Layout differs from the spec; dispatch to a separate decoder.
    if body_len < 30:
        return _decode_its_status_short(body, status, d)

    if body_len < d + 5:
        return status

    # Pump status flags -- body[d+0]
    b0 = body[d + 0]
    status.pump_outdoor = bool(b0 & 0x01)
    status.pump_system = bool(b0 & 0x02)

    # Operating mode -- body[d+1]
    modes = {0: "Off", 1: "Heat", 2: "Cool", 3: "Auto", 4: "Water Pump"}
    status.mode = body[d + 1]
    status.mode_name = modes.get(status.mode, f"Unknown({status.mode})")

    # DHW setpoints -- body[d+2..d+4]
    status.t5s_def = _temp_offset(body[d + 2])
    status.t5s_max = _temp_offset(body[d + 3])
    status.set_temperature = body[d + 4]  # direct, no offset

    # Config/status -- body[d+5]
    if body_len > d + 5:
        status.config_status = body[d + 5]

    # Heating temp limits -- body[d+6..d+7]
    if body_len > d + 7:
        status.td_max = _temp_offset(body[d + 6])
        status.td_min = _temp_offset(body[d + 7])

    # PTC / box temperatures -- body[d+8..d+11]
    if body_len > d + 11:
        status.ptc_temperature_1 = _temp_offset(body[d + 8])
        status.trdh_max = _temp_offset(body[d + 9])
        status.trdh_min = _temp_offset(body[d + 10])
        status.trdh_def = _temp_offset(body[d + 11])

    # Feature validity flags -- body[d+12]
    if body_len > d + 12:
        b12 = body[d + 12]
        status.mute_valid = bool(b12 & 0x80)
        status.force_heat_valid = bool(b12 & 0x40)
        status.sterilize_valid = bool(b12 & 0x20)

    # Status flags -- body[d+13]
    if body_len > d + 13:
        b13 = body[d + 13]
        status.status_flags_raw = b13
        status.comp_running = bool(b13 & 0x01)
        status.ibh_running = bool(b13 & 0x02)
        status.sterilize_running = bool(b13 & 0x04)

    # Enable flags -- body[d+14..d+15]
    if body_len > d + 15:
        status.enable_flags_1 = body[d + 14]
        status.enable_flags_2 = body[d + 15]

    # More temperatures -- body[d+16..d+18]
    if body_len > d + 18:
        status.box_bottom_temp = _temp_offset(body[d + 16])
        status.ptc_temperature = _temp_offset(body[d + 17])
        status.tr_temperature = _temp_offset(body[d + 18])

    # Sterilization -- body[d+19..d+22]
    if body_len > d + 22:
        status.version_or_sterilize_hour = body[d + 19]
        status.sterilize_min = body[d + 20]
        status.sterilize_temperature = _temp_offset(body[d + 21])
        status.sterilize_cycle_days = body[d + 22]

    # Error code -- body[d+23]
    if body_len > d + 23:
        status.error_code = body[d + 23]

    # Heat pump work temp limit -- body[d+24]
    if body_len > d + 24:
        status.heat_pump_work_temp_limit = _temp_offset(body[d + 24])

    # Vacation schedule -- body[d+25..d+28]
    if body_len > d + 28:
        status.vacation_start_year = body[d + 25]
        status.vacation_start_month = body[d + 26]
        status.vacation_start_day = body[d + 27]
        b28 = body[d + 28]
        status.vacation_end_month = (b28 >> 5) & 0x07
        status.vacation_end_day = b28 & 0x1F

    # Extended operational data -- body[d+35..d+48]
    if body_len > d + 48:
        status.exv_drg = (body[d + 35] << 8) | body[d + 36]
        status.pressure_h = (body[d + 37] << 8) | body[d + 38]
        status.pressure_l = (body[d + 39] << 8) | body[d + 40]
        status.comp_frq = (body[d + 41] << 8) | body[d + 42]
        status.total_kwh = (body[d + 43] << 8) | body[d + 44]
        status.comp_total_run_hours = (body[d + 45] << 8) | body[d + 46]
        status.fan_total_run_hours = (body[d + 47] << 8) | body[d + 48]

    return status


def _decode_its_status_short(
    body: bytearray, status: ITSStatus, d: int
) -> ITSStatus:
    """Decode the 25-byte status body returned by the ITS short-frame firmware.

    Verified field offsets (body indices, with d=1):
      d+0  = live operations bitfield (Heat / DHW / TBH / Fast DHW)
      d+3  = zone1 user-set mode (2=Cool, 3=Heat)
      d+5  = active zone setpoint, direct C
      d+6  = zone room temp, direct C (probable)
      d+7  = DHW setpoint, direct C
      d+21 = water outlet temp, direct C (probable -- only byte seen tracking
             live with TBH heating)
    All other body bytes were constant across 6 captures spanning Heat/DHW/TBH/
    Fast-DHW/Cool toggles, so they are treated as opaque config.
    """
    status.firmware_variant = "its_short"
    body_len = len(body)

    if body_len > d + 0:
        b = body[d + 0]
        status.live_ops_raw = b
        status.live_heat = bool(b & 0x01)
        status.live_dhw = bool(b & 0x04)
        status.live_tbh = bool(b & 0x20)
        status.live_fast_dhw = bool(b & 0x40)

    if body_len > d + 3:
        m = body[d + 3]
        status.zone1_mode_raw = m
        status.zone1_mode = {2: "Cool", 3: "Heat"}.get(m, f"Unknown({m})")

    if body_len > d + 5:
        status.zone1_setpoint = body[d + 5]
    if body_len > d + 6:
        status.zone1_room_temp = body[d + 6]
    if body_len > d + 7:
        status.dhw_setpoint_v = body[d + 7]
    if body_len > d + 21:
        status.water_outlet_temp = body[d + 21]

    return status


# ---------------------------------------------------------------------------
# ITS protocol response decoding -- subtype 0x02 (Sensors & Extended)
# ---------------------------------------------------------------------------

@dataclass
class ITSSensors:
    """Decoded ITS subtype 0x02 -- sensor temperatures and extended data.

    This is the primary source of live sensor readings from the heat pump.
    Byte layout uses the ITS/BTRI proprietary protocol with body = frame[10:-1],
    data_offset d=1.
    """

    # Status byte (d+0)
    status_byte: int = 0

    # Outdoor unit info (d+11..d+13)
    online_num: int = 0
    odu_mac_type: int = 0
    limit_frq_code: int = 0

    # Direct-read temperatures (no offset) -- d+14..d+16
    tf_temp: int = 0              # Refrigerant fluid temperature
    tp_temp: int = 0              # Plate heat exchanger temperature
    th_temp: int = 0              # DHW tank temperature

    # Water system (d+17..d+18)
    water_pres: int = 0           # Water pressure
    water_flow: int = 0           # Water flow switch

    # Capacity (d+19)
    capacity_hp: int = 0

    # Offset-encoded temperatures (raw - 35) -- d+20..d+26
    t3_temp: float | None = None  # Condenser temperature
    t4_temp: float | None = None  # Outdoor ambient temperature
    t2_temp: float | None = None  # Evaporator temperature
    t2b_temp: float | None = None # Secondary evaporator temperature
    twin_temp: float | None = None  # Water inlet temperature
    twout_temp: float | None = None # Water outlet temperature
    t1_temp: float | None = None  # Suction temperature (204 = fault)

    # Outdoor unit electrical (d+27..d+30)
    odu_current: int = 0          # 16-bit BE
    odu_voltage: int = 0          # direct
    dc_current: int = 0           # direct

    # Firmware versions (d+31..d+39)
    idu_version: str = ""
    odu_version: str = ""
    hmi_version: str = ""

    # Runtime totals (d+40..d+48)
    mute_level: int = 0  # d+40: 0=Level 1 (or off), 1=Level 2
    ctrl_flag: int = 0  # d+41: 0=normal, 1=mute, 2=boost
    dc_voltage: int = 0             # 16-bit BE
    ibh1_total_run_hours: int = 0
    ibh2_total_run_hours: int = 0
    tbh_total_run_hours: int = 0
    ahs_total_run_hours: int = 0
    hpc_value: int = 0

    # Set to "its_short" when the device returns a short sensor frame whose
    # layout has not been reverse-engineered. In that case all decoded fields
    # above are unreliable; consult raw_body instead.
    firmware_variant: str = "spec"

    # Raw body for further analysis
    raw_body: bytes = field(default_factory=bytes, repr=False)


def decode_its_sensors(body: bytearray) -> ITSSensors:
    """Decode ITS subtype 0x02 body into an ITSSensors object.

    Uses data_offset=1 to skip the body_type byte.
    body[0] = subtype (0x02), body[1..] = actual data.
    """
    sensors = ITSSensors()
    sensors.raw_body = bytes(body)
    d = 1  # data_offset: skip body_type byte

    body_len = len(body)

    # ITS short-frame firmware variant: ~38-byte body vs the spec's 49.
    # Layout has not been mapped, so don't pretend to decode -- caller will
    # display raw bytes instead.
    if body_len < 45:
        sensors.firmware_variant = "its_short"
        return sensors

    if body_len < d + 1:
        return sensors

    # Status byte -- body[d+0]
    sensors.status_byte = body[d + 0]

    # body[d+1..d+10] are reserved (zeros)

    # Outdoor unit info -- body[d+11..d+13]
    if body_len > d + 13:
        sensors.online_num = body[d + 11]
        sensors.odu_mac_type = body[d + 12]
        sensors.limit_frq_code = body[d + 13]

    # Direct-read temperatures (no offset) -- body[d+14..d+16]
    if body_len > d + 16:
        sensors.tf_temp = body[d + 14]
        sensors.tp_temp = body[d + 15]
        sensors.th_temp = body[d + 16]

    # Water system -- body[d+17..d+18]
    if body_len > d + 18:
        sensors.water_pres = body[d + 17]
        sensors.water_flow = body[d + 18]

    # Capacity -- body[d+19]
    if body_len > d + 19:
        sensors.capacity_hp = body[d + 19]

    # Offset-encoded temperatures (raw - 35) -- body[d+20..d+26]
    if body_len > d + 26:
        sensors.t3_temp = _temp_offset(body[d + 20])
        sensors.t4_temp = _temp_offset(body[d + 21])
        sensors.t2_temp = _temp_offset(body[d + 22])
        sensors.t2b_temp = _temp_offset(body[d + 23])
        sensors.twin_temp = _temp_offset(body[d + 24])
        sensors.twout_temp = _temp_offset(body[d + 25])
        sensors.t1_temp = _temp_offset(body[d + 26])

    # Outdoor unit electrical -- body[d+27..d+30]
    if body_len > d + 30:
        sensors.odu_current = (body[d + 27] << 8) | body[d + 28]
        sensors.odu_voltage = body[d + 29]
        sensors.dc_current = body[d + 30]

    # Firmware versions -- body[d+31..d+39]
    if body_len > d + 39:
        sensors.idu_version = _decode_its_version(
            body[d + 31], body[d + 32], body[d + 33],
        )
        sensors.odu_version = _decode_its_version(
            body[d + 34], body[d + 35], body[d + 36],
        )
        sensors.hmi_version = _decode_its_version(
            body[d + 37], body[d + 38], body[d + 39],
        )

    # Runtime totals -- body[d+40..d+48]
    if body_len > d + 48:
        sensors.mute_level = body[d + 40]  # 0=Level 1 (or off), 1=Level 2
        sensors.ctrl_flag = body[d + 41]  # 0=normal, 1=mute, 2=boost
        sensors.dc_voltage = (body[d + 42] << 8) | body[d + 43]
        sensors.ibh1_total_run_hours = body[d + 44]
        sensors.ibh2_total_run_hours = body[d + 45]
        sensors.tbh_total_run_hours = body[d + 46]
        sensors.ahs_total_run_hours = body[d + 47]
        sensors.hpc_value = body[d + 48]

    return sensors


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class ILetComfortClient:
    """Client for the iLetComfort / Midea Dollin cloud API."""

    def __init__(
        self,
        api_base: str = API_BASE,
        access_token: str | None = None,
        timeout: int = 15,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._access_token = access_token
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": (
                "iLetComfort/1.6.4 (com.btri.OEMPlus; build:308; iOS 26.3.0) "
                "Alamofire/5.5.0"
            ),
            "language": "en_US",
        })

    @property
    def access_token(self) -> str | None:
        return self._access_token

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        self._access_token = value

    # -- Low-level request methods --

    def _v1_request(
        self, path: str, body_dict: dict[str, Any], *,
        use_iot_key: bool = False,
    ) -> dict[str, Any]:
        """Send a v1 API request with Scheme 1 signing.

        Header layout matches captured iLetComfort iOS traffic exactly:
          random, src, appid, language, clienttype, appvnum, stamp, deviceid,
          sign, content-type, reqid
        Note: clientId is NOT sent for v1 login (only v2.0 business endpoints).

        Args:
            path: The API endpoint path (e.g. "/v1/user/login").
            body_dict: The request body as a dictionary.
            use_iot_key: If True, use IOT_KEY ("meicloud") prefix for signing.
                         Required for login requests; other v1 endpoints use
                         APP_KEY ("btri") prefix.
        """
        url = self._api_base + path
        json_body = json.dumps(body_dict, separators=(",", ":"))
        sign_hex, random_value = sign_v1(json_body, use_iot_key=use_iot_key)

        stamp = time.strftime("%Y%m%d%H%M%S")

        headers = {
            "random": random_value,
            "src": "20",
            "appid": APP_ID,
            "language": "en_US",
            "clienttype": "2",
            "appvnum": "1.6.4",
            "stamp": stamp,
            "deviceid": hashlib.sha256(
                f"iletcomfort-py-{int(time.time())}".encode()
            ).hexdigest()[:32].upper(),
            "sign": sign_hex,
            "reqid": hashlib.md5(
                f"{time.time()}-{random_module.random()}".encode()
            ).hexdigest(),
        }

        response = self._session.post(
            url, data=json_body, headers=headers, timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()

    def _v2_request(self, path: str, body_dict: dict[str, Any]) -> dict[str, Any]:
        """Send a v2.0 business API request with Scheme 2 signing."""
        if not self._access_token:
            raise AuthError(
                "No access token. Run 'login' or 'set-token' first."
            )

        url = self._api_base + path
        json_body = json.dumps(body_dict, separators=(",", ":"))
        signature = sign_v2("POST", path, json_body)

        headers = {
            "authorization": f"Bearer {self._access_token}",
            "clientId": CLIENT_ID,
            "signature": signature,
            "signatureversion": "2.0",
            "reqid": hashlib.md5(
                f"{time.time()}-{random_module.random()}".encode()
            ).hexdigest(),
        }

        response = self._session.post(
            url, data=json_body, headers=headers, timeout=self._timeout,
        )
        response.raise_for_status()
        result = response.json()

        # Handle expired/invalid token (code 14005)
        code = result.get("code")
        if code == 14005:
            raise AuthError(
                "Access token expired or invalid. "
                "Please obtain a fresh token from the iLetComfort app "
                "and run: iletcomfort_client.py set-token NEW_TOKEN"
            )

        return result

    # -- Token persistence --

    def save_token(self, filepath: Path = TOKEN_FILE) -> None:
        """Save the access token to a JSON file."""
        data: dict[str, Any] = {}
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        data["access_token"] = self._access_token
        data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        filepath.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )
        filepath.chmod(0o600)

    def load_token(self, filepath: Path = TOKEN_FILE) -> bool:
        """Load access token from a saved file. Returns True if successful."""
        if not filepath.exists():
            return False
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            token = data.get("access_token")
            if token:
                self._access_token = token
                return True
        except (json.JSONDecodeError, OSError, KeyError):
            pass
        return False

    def _save_last_on_state(
        self, mode: int, temperature: int, filepath: Path = TOKEN_FILE,
    ) -> None:
        """Save the last known on-state (mode + temp) for power-on restore."""
        data: dict[str, Any] = {}
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        data["last_on_mode"] = mode
        data["last_on_temp"] = temperature
        filepath.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )

    def _load_last_on_state(
        self, filepath: Path = TOKEN_FILE,
    ) -> tuple[int, int] | None:
        """Load the last known on-state. Returns (set_mode, temperature) or None."""
        if not filepath.exists():
            return None
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            mode = data.get("last_on_mode")
            temp = data.get("last_on_temp")
            if mode is not None and temp is not None:
                return (int(mode), int(temp))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return None

    def _load_appliance_code(self, filepath: Path = TOKEN_FILE) -> str | None:
        """Load the saved default appliance code."""
        if not filepath.exists():
            return None
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            return data.get("default_appliance")
        except (json.JSONDecodeError, OSError):
            return None

    def _save_appliance_code(
        self, code: str, filepath: Path = TOKEN_FILE,
    ) -> None:
        """Save a default appliance code for convenience."""
        data: dict[str, Any] = {}
        if filepath.exists():
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        data["default_appliance"] = code
        filepath.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )

    # -- Public API methods --

    def login(
        self, account: str, password: str, *, pre_encrypted: bool = False,
    ) -> dict[str, Any]:
        """Authenticate with the Dollin cloud.

        Accepts either a plaintext password (default) or a pre-encrypted hex
        string obtained from a network capture.

        The encryption scheme (encryptVersion=1) was reverse-engineered from the
        iLetComfort iOS binary:
          AES-128-CBC(SHA256(password), key/iv derived from SHA256(ENCRYPT_KEY))

        Args:
            account: Email or phone number.
            password: Plaintext password, or encrypted hex if pre_encrypted=True.
            pre_encrypted: If True, skip encryption and send password as-is.

        Returns:
            The login response data dict.
        """
        encrypted_pw = password if pre_encrypted else encrypt_password(password)

        body = {
            "loginAccount": account,
            "password": encrypted_pw,
            "encryptVersion": "1",
        }

        result = self._v1_request("/v1/user/login", body, use_iot_key=True)

        if result.get("code") == 0 and "data" in result:
            self._access_token = result["data"]["accessToken"]
            self.save_token()
            return result["data"]

        raise ApiError(
            f"Login failed: code={result.get('code')}, msg={result.get('msg')}"
        )

    def list_appliances(self) -> list[dict[str, Any]]:
        """List all appliances linked to the account.

        Returns:
            List of appliance dicts with keys like applianceCode, name, type.
        """
        result = self._v2_request(
            "/midea/open/business/v1/appliance/list",
            {"queryAuth": True},
        )

        if result.get("code") == 0:
            appliances = result.get("data", [])
            # Auto-save the first appliance code for convenience
            if isinstance(appliances, list) and len(appliances) > 0:
                first_code = str(appliances[0].get("applianceCode", ""))
                if first_code:
                    self._save_appliance_code(first_code)
            return appliances

        raise ApiError(
            f"List appliances failed: code={result.get('code')}, "
            f"msg={result.get('msg')}"
        )

    def get_appliance_info(self, appliance_code: str) -> dict[str, Any]:
        """Get detailed info about a specific appliance."""
        result = self._v2_request(
            "/midea/open/business/v1/appliance/info",
            {"applianceCode": appliance_code},
        )

        if result.get("code") == 0:
            return result.get("data", {})

        raise ApiError(
            f"Get appliance info failed: code={result.get('code')}, "
            f"msg={result.get('msg')}"
        )

    def send_hex_command(
        self, appliance_code: str, command_hex: str,
    ) -> str:
        """Send a raw hex command to an appliance and return the response hex.

        Args:
            appliance_code: Target appliance code.
            command_hex: Hex-encoded command frame.

        Returns:
            Response hex string from the API (comma-separated or continuous).
        """
        result = self._v2_request(
            "/midea/open/business/v1/appliance/control/hexadecimal",
            {
                "applianceCode": appliance_code,
                "command": command_hex,
            },
        )

        if result.get("code") == 0:
            return result.get("data", "")

        raise ApiError(
            f"Send command failed: code={result.get('code')}, "
            f"msg={result.get('msg')}"
        )

    def query_status(self, appliance_code: str) -> ITSStatus:
        """Query heat pump status and control settings (subtype 0x01).

        Also saves the last on-state (mode + temperature) whenever the device
        is running, so that 'set --on' can restore it later.
        """
        command = build_c3_query(0x01)
        response_hex = self.send_hex_command(appliance_code, command)
        raw = parse_hex_response(response_hex)
        _body_type, body = extract_c3_body(raw)
        status = decode_its_status(body)

        # Persist last on-state for --on restore
        if status.mode != 0:
            _q2s = {1: MODE_HEAT, 2: MODE_COOL, 4: MODE_WATERPUMP}
            set_mode = _q2s.get(status.mode)
            if set_mode is not None:
                self._save_last_on_state(set_mode, int(status.t5s_def) if status.t5s_def is not None else status.set_temperature)

        return status

    def query_sensors(self, appliance_code: str) -> ITSSensors:
        """Query heat pump sensor temperatures and extended data (subtype 0x02).

        This reads live sensor data including water inlet/outlet, refrigerant,
        condenser, evaporator temperatures, electrical readings, and firmware
        versions.
        """
        command = build_c3_query(0x02)
        response_hex = self.send_hex_command(appliance_code, command)
        raw = parse_hex_response(response_hex)
        _body_type, body = extract_c3_body(raw)
        return decode_its_sensors(body)

    def set_device(
        self,
        appliance_code: str,
        *,
        mode: int | None = None,
        temperature: int | None = None,
        boost: bool | None = None,
        mute: int | None = None,
        mute_level: int | None = None,
        power_on: bool = False,
    ) -> dict[str, Any]:
        """Send a SET command to the heat pump.

        Queries current status first (subtype 0x01) to obtain echo bytes and
        current values, merges the requested changes, validates temperature
        ranges, builds the SET frame, and sends it.

        The last on-state (mode + temperature) is saved whenever the device is
        running, and restored when power_on=True is used.

        Args:
            appliance_code: Target appliance code.
            mode: Operating mode (MODE_OFF/HEAT/COOL/WATERPUMP), or None to keep current.
            temperature: Target temperature in Celsius, or None to keep current.
            boost: True to enable boost, False to disable, None to keep current.
            mute: Mute level (0=off, 1=level1, 2=level2), or None to keep current.
            mute_level: Explicit mute level byte (0 or 1). Auto-derived from mute if not set.
            power_on: If True, restore the last known on-state (mode + temp).

        Returns:
            Dict with 'sent' (the command hex) and 'response' (response hex).
        """
        # 1. Query current status to get echo bytes + current values
        command = build_c3_query(0x01)
        response_hex = self.send_hex_command(appliance_code, command)
        raw = parse_hex_response(response_hex)
        _body_type, status_body = extract_c3_body(raw)
        status = decode_its_status(status_body)

        # 2. Determine effective values (merge requested with current)
        # Map query response mode (0=Off,1=Heat,2=Cool,4=WaterPump)
        # to SET mode (0=Off,1=Heat,3=Cool,4=WaterPump)
        _query_to_set_mode = {0: MODE_OFF, 1: MODE_HEAT, 2: MODE_COOL, 4: MODE_WATERPUMP}
        current_set_mode = _query_to_set_mode.get(status.mode, MODE_OFF)

        # Save last on-state whenever the device is currently running
        if current_set_mode != MODE_OFF:
            self._save_last_on_state(current_set_mode, int(status.t5s_def) if status.t5s_def is not None else status.set_temperature)

        # Handle power_on: restore last on-state
        if power_on:
            saved = self._load_last_on_state()
            if saved is None:
                raise ValueError(
                    "No saved on-state to restore. Use --mode to specify "
                    "a mode explicitly (e.g. --mode heat)."
                )
            if mode is None:
                mode = saved[0]
            if temperature is None:
                temperature = saved[1]

        eff_mode = mode if mode is not None else current_set_mode

        # Determine effective temperature.
        # status.set_temperature is the DHW/waterpump setpoint — it is NOT valid for
        # heat/cool modes. When no temperature is specified, keep the current setpoint
        # as-is, trusting the device already has a valid temperature for its mode.
        if temperature is not None:
            eff_temp = temperature
            temp_explicitly_set = True
        else:
            eff_temp = status.set_temperature
            temp_explicitly_set = False

        # Determine ctrl_flag and mute_level from current status + requested changes
        eff_ctrl_flag = 0x00  # normal
        eff_mute_level = 0x00  # level1

        if boost is True:
            eff_ctrl_flag = 0x02
        elif mute is not None:
            if mute == 0:
                eff_ctrl_flag = 0x00  # normal (mute off)
            else:
                eff_ctrl_flag = 0x01  # mute
                eff_mute_level = 0x00 if mute == 1 else 0x01

        if mute_level is not None:
            eff_mute_level = mute_level

        # 3. Validate temperature against mode-specific ranges (only when explicitly set)
        if temp_explicitly_set and eff_mode in TEMP_RANGES and eff_mode != MODE_OFF:
            temp_min, temp_max = TEMP_RANGES[eff_mode]
            if not (temp_min <= eff_temp <= temp_max):
                mode_name = {v: k for k, v in MODE_MAP.items()}.get(eff_mode, "unknown")
                raise ValueError(
                    f"Temperature {eff_temp}C out of range for {mode_name} mode "
                    f"(allowed: {temp_min}-{temp_max}C)"
                )

        # 4. Build and send the SET frame
        set_hex = build_c3_set(
            mode=eff_mode,
            temperature=eff_temp,
            status_body=status_body,
            mute_level=eff_mute_level,
            ctrl_flag=eff_ctrl_flag,
        )

        set_response_hex = self.send_hex_command(appliance_code, set_hex)

        # Save on-state if we just turned the device on
        if eff_mode != MODE_OFF:
            self._save_last_on_state(eff_mode, eff_temp)

        return {
            "sent": set_hex,
            "response": set_response_hex,
        }

    def get_ads(self) -> dict[str, Any]:
        """Get ads/module info."""
        result = self._v2_request(
            "/midea/open/business/v1/ads", {},
        )
        if result.get("code") == 0:
            return result.get("data", {})
        raise ApiError(
            f"Get ads failed: code={result.get('code')}, msg={result.get('msg')}"
        )



# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Authentication or authorization error."""


class ApiError(Exception):
    """API request error."""


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------

def print_its_status(status: ITSStatus) -> None:
    """Print an ITSStatus in a human-readable format."""
    if status.firmware_variant == "its_short":
        _print_its_status_short(status)
        return

    print("\n" + "=" * 60)
    print("  ITS HEAT PUMP -- STATUS & CONTROL (subtype 0x01)")
    print("=" * 60)

    print("\n  Pump Status:")
    print(f"    Outdoor Pump:     {'ON' if status.pump_outdoor else 'OFF'}")
    print(f"    System Pump:      {'ON' if status.pump_system else 'OFF'}")

    print("\n  Operating Mode:")
    print(f"    Mode:             {status.mode_name} ({status.mode})")

    print("\n  Component Status:")
    print(f"    Compressor:       {'Running' if status.comp_running else 'Idle'}")
    print(f"    IBH:              {'Running' if status.ibh_running else 'Idle'}")
    print(f"    Sterilize:        {'Running' if status.sterilize_running else 'Idle'}")

    print("\n  DHW Settings:")
    print(f"    DHW Target:       {status.set_temperature} C")
    print(f"    DHW Default (T5S):{_format_temp(status.t5s_def)}")
    print(f"    DHW Max (T5S):    {_format_temp(status.t5s_max)}")

    print("\n  Temperature Limits:")
    print(f"    TD Max:           {_format_temp(status.td_max)}")
    print(f"    TD Min:           {_format_temp(status.td_min)}")
    print(f"    TRDH Max:         {_format_temp(status.trdh_max)}")
    print(f"    TRDH Min:         {_format_temp(status.trdh_min)}")
    print(f"    TRDH Default:     {_format_temp(status.trdh_def)}")
    print(f"    HP Work Limit:    {_format_temp(status.heat_pump_work_temp_limit)}")

    print("\n  Temperatures:")
    print(f"    Box Bottom:       {_format_temp(status.box_bottom_temp)}")
    print(f"    PTC:              {_format_temp(status.ptc_temperature)}")
    print(f"    TR:               {_format_temp(status.tr_temperature)}")

    print("\n  Feature Validity:")
    print(f"    Mute Valid:       {'Yes' if status.mute_valid else 'No'}")
    print(f"    Force Heat Valid: {'Yes' if status.force_heat_valid else 'No'}")
    print(f"    Sterilize Valid:  {'Yes' if status.sterilize_valid else 'No'}")

    if status.sterilize_valid:
        print("\n  Sterilization:")
        print(f"    Temperature:      {_format_temp(status.sterilize_temperature)}")
        print(f"    Cycle (days):     {status.sterilize_cycle_days}")
        print(f"    Hour:             {status.version_or_sterilize_hour}")
        print(f"    Minute:           {status.sterilize_min}")

    print(f"\n  Error Code:         {status.error_code}")

    if status.exv_drg or status.comp_frq or status.total_kwh:
        print("\n  Operational Data:")
        print(f"    EXV Opening:      {status.exv_drg}")
        print(f"    Compressor Freq:  {status.comp_frq} Hz")
        print(f"    Pressure High:    {status.pressure_h}")
        print(f"    Pressure Low:     {status.pressure_l}")
        print(f"    Total Energy:     {status.total_kwh} kWh")
        print(f"    Comp Run Hours:   {status.comp_total_run_hours} h")
        print(f"    Fan Run Hours:    {status.fan_total_run_hours} h")

    if status.vacation_start_year:
        print("\n  Vacation Schedule:")
        print(
            f"    Start:            "
            f"{status.vacation_start_year}/"
            f"{status.vacation_start_month:02d}/"
            f"{status.vacation_start_day:02d}"
        )
        print(
            f"    End:              "
            f"{status.vacation_end_month:02d}/"
            f"{status.vacation_end_day:02d}"
        )
    print()


def _print_its_status_short(status: ITSStatus) -> None:
    """Print the short-frame variant of ITS status."""
    print("\n" + "=" * 60)
    print("  ITS HEAT PUMP -- STATUS  (firmware: ITS short-frame variant)")
    print("=" * 60)

    print("\n  Active Operations  (live -- what the unit is doing right now):")
    print(f"    Space Heating:    {'ON' if status.live_heat else 'off'}")
    print(f"    DHW Heating:      {'ON' if status.live_dhw else 'off'}")
    print(f"    TBH Booster:      {'ON' if status.live_tbh else 'off'}")
    print(f"    Fast DHW Boost:   {'ON' if status.live_fast_dhw else 'off'}")
    if status.live_ops_raw is not None:
        unidentified = status.live_ops_raw & ~0x65
        suffix = (
            f"  -- unidentified bits: 0x{unidentified:02x}"
            if unidentified
            else ""
        )
        print(f"    raw bitfield:     0x{status.live_ops_raw:02x}{suffix}")

    print("\n  Zone 1:")
    print(f"    Mode:             {status.zone1_mode or '?'}")
    if status.zone1_setpoint is not None:
        print(f"    Setpoint:         {status.zone1_setpoint} C")
    if status.zone1_room_temp is not None:
        print(f"    Room Temp:        {status.zone1_room_temp} C   (probable)")

    print("\n  DHW:")
    if status.dhw_setpoint_v is not None:
        print(f"    Setpoint:         {status.dhw_setpoint_v} C")

    print("\n  Water Circuit:")
    if status.water_outlet_temp is not None:
        print(f"    Outlet Temp:      {status.water_outlet_temp} C   (probable)")

    print("\n  Raw body  (verified bytes only -- rest are opaque config):")
    hex_str = " ".join(f"{b:02x}" for b in status.raw_body)
    print(f"    {hex_str}")
    print()


def print_its_sensors(sensors: ITSSensors) -> None:
    """Print ITSSensors in a human-readable format."""
    if sensors.firmware_variant == "its_short":
        print("\n" + "=" * 60)
        print("  ITS HEAT PUMP -- SENSORS  (decode unavailable for this firmware)")
        print("=" * 60)
        print(
            "\n  This firmware returns a short sensor frame whose layout has\n"
            "  not been reverse-engineered yet. Most of the bytes are zero\n"
            "  while the unit is idle. Raw body for further analysis:\n"
        )
        hex_str = " ".join(f"{b:02x}" for b in sensors.raw_body)
        print(f"    {hex_str}")
        print()
        return

    print("\n" + "=" * 60)
    print("  ITS HEAT PUMP -- SENSOR READINGS (subtype 0x02)")
    print("=" * 60)

    print("\n  Refrigerant Circuit:")
    print(f"    Tf  (Refrigerant Fluid):   {sensors.tf_temp} C")
    print(f"    Tp  (Plate Heat Exchanger): {sensors.tp_temp} C")
    print(f"    T3  (Condenser):           {_format_temp(sensors.t3_temp)}")
    print(f"    T2  (Evaporator):          {_format_temp(sensors.t2_temp)}")
    print(f"    T2b (Evaporator 2):        {_format_temp(sensors.t2b_temp)}")
    print(f"    T1  (Suction):             {_format_temp(sensors.t1_temp)}")

    print("\n  Water Circuit:")
    print(f"    Twin (Water Inlet):        {_format_temp(sensors.twin_temp)}")
    print(f"    Twout (Water Outlet):      {_format_temp(sensors.twout_temp)}")
    print(f"    Th  (DHW Tank):            {sensors.th_temp} C")
    print(f"    Water Pressure:            {sensors.water_pres}")
    print(f"    Water Flow:                {sensors.water_flow}")

    print("\n  Outdoor Unit:")
    print(f"    T4  (Outdoor Ambient):     {_format_temp(sensors.t4_temp)}")
    print(f"    ODU Voltage:               {sensors.odu_voltage} V")
    print(f"    ODU Current:               {sensors.odu_current} A")
    print(f"    DC Current:                {sensors.dc_current} A")
    print(f"    DC Voltage:                {sensors.dc_voltage} V")

    print(f"\n  Capacity:                    {sensors.capacity_hp}")

    if sensors.idu_version or sensors.odu_version or sensors.hmi_version:
        print("\n  Firmware Versions:")
        if sensors.idu_version:
            print(f"    IDU:                       {sensors.idu_version}")
        if sensors.odu_version:
            print(f"    ODU:                       {sensors.odu_version}")
        if sensors.hmi_version:
            print(f"    HMI:                       {sensors.hmi_version}")

    print("\n  Runtime Totals:")
    print(f"    Mute Level:                {sensors.mute_level} (0=L1/off, 1=L2)")
    print(f"    Ctrl Flag:                 {sensors.ctrl_flag} (0=normal, 1=mute, 2=boost)")
    print(f"    IBH1 Hours:                {sensors.ibh1_total_run_hours} h")
    print(f"    IBH2 Hours:                {sensors.ibh2_total_run_hours} h")
    print(f"    TBH Hours:                 {sensors.tbh_total_run_hours} h")
    print(f"    AHS Hours:                 {sensors.ahs_total_run_hours} h")
    print(f"    HPC Value:                 {sensors.hpc_value}")

    print("\n  ODU Info:")
    print(f"    Online Num:                {sensors.online_num}")
    print(f"    MAC Type:                  {sensors.odu_mac_type}")
    print(f"    Limit Freq Code:           {sensors.limit_frq_code}")
    print()


def print_appliance_list(appliances: list[dict[str, Any]]) -> None:
    """Print the appliance list in a human-readable format."""
    print("\n" + "=" * 60)
    print("  APPLIANCES")
    print("=" * 60)

    if not appliances:
        print("\n  No appliances found.")
        print()
        return

    for i, app in enumerate(appliances, 1):
        online = "Online" if app.get("online") == 1 else "Offline"
        print(f"\n  [{i}] {app.get('name', 'Unknown')}")
        print(f"      Code:   {app.get('applianceCode', 'N/A')}")
        print(f"      Type:   {app.get('applianceType', 'N/A')}")
        print(f"      SN:     {app.get('sn', 'N/A')}")
        print(f"      SN8:    {app.get('sn8', 'N/A')}")
        print(f"      Status: {online}")
        print(f"      Owner:  {'Yes' if app.get('owner') else 'No'}")
    print()


def print_appliance_info(info: dict[str, Any]) -> None:
    """Print appliance info in a human-readable format."""
    print("\n" + "=" * 60)
    print("  APPLIANCE INFO")
    print("=" * 60)

    if not info:
        print("\n  No info returned.")
        print()
        return

    for key, value in sorted(info.items()):
        print(f"  {key}: {value}")
    print()


def print_raw_response(hex_data: str) -> None:
    """Print a raw hex response with byte-level analysis."""
    raw = parse_hex_response(hex_data)

    print("\n" + "=" * 60)
    print("  RAW RESPONSE")
    print("=" * 60)
    print(f"\n  Length: {len(raw)} bytes")
    print(f"  Hex:    {','.join(f'{b:02x}' for b in raw)}")
    print()

    for i, b in enumerate(raw):
        note = ""
        if i == 0 and b == 0xAA:
            note = "  (header)"
        elif i == 1:
            note = f"  (length={b})"
        elif i == 2:
            dev_types = {0xC3: "Heat Pump", 0xAC: "Air Conditioner"}
            note = f"  ({dev_types.get(b, 'device type')})"
        elif i == 9:
            msg_types = {0x02: "set", 0x03: "query/response", 0x04: "notify"}
            note = f"  ({msg_types.get(b, 'msg type')})"
        elif i == 10:
            note = f"  (subtype 0x{b:02x})"
        elif 10 < i < len(raw) - 1 and 1 <= b <= 100:
            note = f"  (possible temp: {b} C)"
        elif i == len(raw) - 1:
            note = "  (checksum)"

        print(f"  [{i:3d}] 0x{b:02x} ({b:3d}){note}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def resolve_appliance_code(
    args: argparse.Namespace,
    client: ILetComfortClient,
) -> str:
    """Resolve the appliance code from args or saved state."""
    code = getattr(args, "appliance", None)
    if code:
        return code

    saved = client._load_appliance_code()
    if saved:
        return saved

    print(
        "Error: No appliance code specified and none saved.\n"
        "Run 'list' first to discover appliances, or use --appliance CODE.",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_login(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'login' command."""
    try:
        data = client.login(
            args.account,
            args.password,
            pre_encrypted=getattr(args, "encrypted", False),
        )
        print("Login successful.")
        print(f"  User:  {data.get('userName', 'N/A')}")
        print(f"  UID:   {data.get('uid', 'N/A')}")
        print(f"  Token: {data.get('accessToken', 'N/A')[:40]}...")
        print(f"  Token saved to {TOKEN_FILE}")
    except ApiError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_set_token(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'set-token' command."""
    client.access_token = args.token
    client.save_token()
    print(f"Token saved to {TOKEN_FILE}")


def cmd_list(_args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'list' command."""
    try:
        appliances = client.list_appliances()
        if getattr(_args, "json_output", False):
            print(json.dumps(appliances, indent=2))
        else:
            print_appliance_list(appliances)
    except (AuthError, ApiError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_info(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'info' command."""
    code = resolve_appliance_code(args, client)
    try:
        info = client.get_appliance_info(code)
        if getattr(args, "json_output", False):
            print(json.dumps(info, indent=2))
        else:
            print_appliance_info(info)
    except (AuthError, ApiError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _dataclass_to_json_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass to a JSON-safe dict, excluding raw bytes."""
    import dataclasses
    d = dataclasses.asdict(obj)
    d.pop("raw_body", None)
    d.pop("extra_bytes", None)
    return d


def cmd_status(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'status' command -- ITS subtype 0x01."""
    code = resolve_appliance_code(args, client)
    try:
        status = client.query_status(code)
        if getattr(args, "json_output", False):
            print(json.dumps(_dataclass_to_json_dict(status), indent=2))
        else:
            print_its_status(status)
    except (AuthError, ApiError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sensors(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'sensors' command -- ITS subtype 0x02."""
    code = resolve_appliance_code(args, client)
    try:
        sensors = client.query_sensors(code)
        if getattr(args, "json_output", False):
            print(json.dumps(_dataclass_to_json_dict(sensors), indent=2))
        else:
            print_its_sensors(sensors)
    except (AuthError, ApiError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_raw(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'raw' command."""
    code = resolve_appliance_code(args, client)
    try:
        response_hex = client.send_hex_command(code, args.hex_command)
        if getattr(args, "json_output", False):
            print(json.dumps({"response": response_hex}))
        else:
            print_raw_response(response_hex)
    except (AuthError, ApiError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_set(args: argparse.Namespace, client: ILetComfortClient) -> None:
    """Handle the 'set' command -- send a SET frame to control the heat pump."""
    code = resolve_appliance_code(args, client)

    mode_val = MODE_MAP.get(args.mode) if args.mode else None
    temp_val = args.temp
    boost_val = None
    mute_val = None
    power_on = getattr(args, "on", False)
    power_off = getattr(args, "off", False)

    if args.boost is not None:
        boost_val = args.boost == "on"
    if args.mute is not None:
        mute_val = 0 if args.mute == "off" else int(args.mute)

    # --off is shorthand for --mode off
    if power_off:
        mode_val = MODE_OFF

    # Must specify at least one change
    if (
        not power_on
        and mode_val is None
        and temp_val is None
        and boost_val is None
        and mute_val is None
    ):
        print(
            "Error: Specify at least one of --on, --off, --mode, --temp, --boost, --mute.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        result = client.set_device(
            code,
            mode=mode_val,
            temperature=temp_val,
            boost=boost_val,
            mute=mute_val,
            power_on=power_on,
        )

        if getattr(args, "json_output", False):
            print(json.dumps(result, indent=2))
        else:
            print("\nSET command sent successfully.")
            parts = []
            if power_on:
                parts.append("Power: ON (restored last state)")
            if power_off:
                parts.append("Power: OFF")
            elif args.mode:
                parts.append(f"Mode: {args.mode}")
            if temp_val is not None:
                parts.append(f"Temperature: {temp_val}C")
            if boost_val is not None:
                parts.append(f"Boost: {'on' if boost_val else 'off'}")
            if mute_val is not None:
                parts.append(f"Mute: {args.mute}")
            for p in parts:
                print(f"  {p}")
            print(f"\n  Sent:     {result['sent'][:40]}...")
            print(f"  Response: {result['response'][:40]}...")
            print()

    except (AuthError, ApiError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_decode(args: argparse.Namespace, _client: ILetComfortClient) -> None:
    """Handle the 'decode' command -- offline decode of a hex response."""
    hex_data = args.hex_data
    try:
        raw = parse_hex_response(hex_data)
        body_type, body = extract_c3_body(raw)

        json_output = getattr(args, "json_output", False)

        if body_type == 0x01:
            status = decode_its_status(body)
            if json_output:
                print(json.dumps(_dataclass_to_json_dict(status), indent=2))
            else:
                print_its_status(status)
        elif body_type == 0x02:
            sensors = decode_its_sensors(body)
            if json_output:
                print(json.dumps(_dataclass_to_json_dict(sensors), indent=2))
            else:
                print_its_sensors(sensors)
        else:
            print(f"Unknown subtype 0x{body_type:02x}, printing raw response.")
            print_raw_response(hex_data)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="iletcomfort_client",
        description="iLetComfort / ITS/BTRI Heat Pump Cloud API Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s set-token us_A_eyJ0eXAi...\n"
            "  %(prog)s list\n"
            "  %(prog)s status\n"
            "  %(prog)s sensors --json\n"
            "  %(prog)s sensors --appliance YOUR_APPLIANCE_CODE\n"
            "  %(prog)s set --mode heat\n"
            "  %(prog)s set --temp 28\n"
            "  %(prog)s set --boost on\n"
            "  %(prog)s set --mute 1\n"
            "  %(prog)s set --mode heat --temp 28\n"
            "  %(prog)s raw aa0cc30000000000000301012c\n"
            "  %(prog)s decode 'aa,3d,c3,...'\n"
        ),
    )
    parser.add_argument(
        "--json", dest="json_output", action="store_true",
        help="Output in JSON format instead of human-readable text",
    )
    parser.add_argument(
        "--api-base", default=API_BASE,
        help=f"API base URL (default: {API_BASE})",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # login
    login_parser = subparsers.add_parser(
        "login", help="Authenticate with the Dollin cloud",
    )
    login_parser.add_argument(
        "--account", required=True, help="Email or phone number",
    )
    login_parser.add_argument(
        "--password", required=True,
        help="Plaintext password (encrypted automatically), or hex if --encrypted",
    )
    login_parser.add_argument(
        "--encrypted", action="store_true", default=False,
        help="Treat --password as a pre-encrypted hex string (skip encryption)",
    )

    # set-token
    token_parser = subparsers.add_parser(
        "set-token", help="Manually set an access token",
    )
    token_parser.add_argument(
        "token", help="The JWT access token (us_A_... format)",
    )

    # list
    subparsers.add_parser(
        "list", help="List all appliances on the account",
    )

    # info
    info_parser = subparsers.add_parser(
        "info", help="Get detailed info about an appliance",
    )
    info_parser.add_argument(
        "--appliance", help="Appliance code (auto-detected if only one)",
    )

    # status
    status_parser = subparsers.add_parser(
        "status", help="Query heat pump status and control settings (subtype 0x01)",
    )
    status_parser.add_argument(
        "--appliance", help="Appliance code (auto-detected if only one)",
    )

    # sensors
    sensors_parser = subparsers.add_parser(
        "sensors", help="Query heat pump sensor temperatures and data (subtype 0x02)",
    )
    sensors_parser.add_argument(
        "--appliance", help="Appliance code (auto-detected if only one)",
    )

    # raw
    raw_parser = subparsers.add_parser(
        "raw", help="Send a raw hex command and display the response",
    )
    raw_parser.add_argument(
        "hex_command", help="Hex-encoded command frame",
    )
    raw_parser.add_argument(
        "--appliance", help="Appliance code (auto-detected if only one)",
    )

    # set
    set_parser = subparsers.add_parser(
        "set", help="Control the heat pump (on/off, mode, temperature, boost, mute)",
    )
    set_parser.add_argument(
        "--appliance", help="Appliance code (auto-detected if only one)",
    )
    power_group = set_parser.add_mutually_exclusive_group()
    power_group.add_argument(
        "--on", action="store_true", default=False,
        help="Turn on, restoring last known mode and temperature",
    )
    power_group.add_argument(
        "--off", action="store_true", default=False,
        help="Turn off (shorthand for --mode off)",
    )
    set_parser.add_argument(
        "--mode", choices=["off", "heat", "cool", "waterpump"],
        help="Operating mode",
    )
    set_parser.add_argument(
        "--temp", type=int, metavar="N",
        help="Target temperature in Celsius",
    )
    set_parser.add_argument(
        "--boost", choices=["on", "off"],
        help="Enable or disable boost mode",
    )
    set_parser.add_argument(
        "--mute", choices=["off", "1", "2"],
        help="Silent mode: off, level 1, or level 2",
    )

    # decode (offline)
    decode_parser = subparsers.add_parser(
        "decode",
        help="Decode a C3 hex response offline (no API call needed)",
    )
    decode_parser.add_argument(
        "hex_data",
        help="Hex response string (comma-separated or continuous)",
    )

    return parser


def main() -> None:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    client = ILetComfortClient(api_base=args.api_base)

    # Commands that do not need a token
    if args.command == "decode":
        cmd_decode(args, client)
        return

    if args.command == "set-token":
        cmd_set_token(args, client)
        return

    # Load saved token if available
    if args.command != "login":
        if not client.load_token():
            print(
                "Error: No saved token found.\n"
                "Run one of:\n"
                "  iletcomfort_client.py login --account EMAIL --password YOUR_PASSWORD\n"
                "  iletcomfort_client.py set-token YOUR_JWT_TOKEN\n",
                file=sys.stderr,
            )
            sys.exit(1)

    # Dispatch
    commands = {
        "login": cmd_login,
        "list": cmd_list,
        "info": cmd_info,
        "status": cmd_status,
        "sensors": cmd_sensors,
        "raw": cmd_raw,
        "set": cmd_set,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args, client)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
