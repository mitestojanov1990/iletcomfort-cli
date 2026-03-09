# iLetComfort API & Protocol Reference

**Last Updated:** 2026-03-04
**App:** iLetComfort v1.6.4 (com.btri.OEMPlus, build 308)
**Platform:** iOS (iPad), built with Alamofire/5.5.0, RxCocoa/RxSwift
**Manufacturer:** Shanghai KONG / ITS brand, built on Midea Dollin OEM platform
**Device Type:** 0xC3 (Heat Pump), ITS/BTRI proprietary protocol variant

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Authentication & Signing](#2-authentication--signing)
3. [Cloud API Endpoints](#3-cloud-api-endpoints)
4. [C3 Heat Pump Protocol (Frames)](#4-c3-heat-pump-protocol-frames)
5. [Status Query — Subtype 0x01](#5-status-query--subtype-0x01)
6. [Sensor Query — Subtype 0x02](#6-sensor-query--subtype-0x02)
7. [SET Command — Control Frame](#7-set-command--control-frame)
8. [Temperature Encoding](#8-temperature-encoding)
9. [Mode Mapping Discrepancy](#9-mode-mapping-discrepancy)
10. [LAN Communication](#10-lan-communication)
11. [MQTT Real-Time Updates](#11-mqtt-real-time-updates)
12. [Server-Side Decode Endpoint](#12-server-side-decode-endpoint)
13. [App Constants & Secrets](#13-app-constants--secrets)
14. [Key Differences from midea-local](#14-key-differences-from-midea-local)
15. [Python Client Reference](#15-python-client-reference)
16. [Open Questions & Future Work](#16-open-questions--future-work)

---

## 1. Architecture Overview

```
┌─────────────────────┐
│  iLetComfort App     │
│  (com.btri.OEMPlus)  │
└─────────┬───────────┘
          │
          ├── HTTPS ──► us.dollin.net (Midea Dollin Cloud API)
          │              ├── /v1/user/login                    (Auth, v1 signing)
          │              ├── /midea/open/business/v1/...       (Business API, v2.0 signing)
          │              │    ├── /appliance/list
          │              │    ├── /appliance/info
          │              │    ├── /appliance/control/hexadecimal   ◄── Main command channel
          │              │    ├── /ads
          │              │    └── /country/channel/retrieve
          │              └── /v2/open/sdk/appliance/privateKey  (SDK API, v2.1 signing)
          │
          ├── HTTPS ──► us.ibuildinghvac.com (iBUILDING HVAC)
          │              ├── /api/apps-oem/v1/privacy/isLatestVersion
          │              └── /api/mibp-connector-mo-parser/mibp/sys/mg_iot/decode
          │
          ├── MQTT/TLS ► a39t91kk7ictzi-ats.iot.us-west-2.amazonaws.com:8883
          │              (AWS IoT Core — real-time device state push)
          │
          └── HTTPS ──► sentry.msmartlife.net (Error tracking / Sentry)
                         Key: bc30997250a25e5e81910fe1c0480080
```

All device control goes through the **hexadecimal control endpoint** on `us.dollin.net`.
The app sends raw C3 protocol frames as hex strings, and the cloud relays them to the
device, returning the device's hex response.

---

## 2. Authentication & Signing

### 2.1 Password Encryption

Reverse-engineered from the iOS binary (`+[MideaSecurity encryptDataBeforeLogin:]`):

```
1. password_hash = SHA256(plaintext_password).hexdigest()   # 64-char lowercase hex
2. key_material  = SHA256(ENCRYPT_KEY).hexdigest()
3. aes_key       = key_material[0:16]   (ASCII bytes, not hex-decoded)
4. aes_iv        = key_material[16:32]  (ASCII bytes, not hex-decoded)
5. encrypted     = AES-128-CBC(password_hash, aes_key, aes_iv, PKCS7 padding)
6. result        = encrypted.hex()      # 160-char lowercase hex string
```

**Key detail:** The AES key and IV are the *ASCII characters* of the first 32 hex digits
of the SHA-256 digest, NOT the decoded bytes. This is how the iOS app implements it.

### 2.2 Signing Scheme 1 — v1 Login API (`sign` header)

Used for all `/v1/` endpoints.

```
random  = strftime("%Y%m%d%H%M%S") + str(randint(0, 65535))
prefix  = "meicloud"  for login endpoints (IOT_KEY)
          "btri"      for all other v1 endpoints (APP_KEY)
message = prefix + json_body + random
sign    = hex(HMAC-SHA256(key=APP_SECRET, msg=message))
```

**Status: VERIFIED** against captured traffic.

**Required Headers:**
```http
random: <random value from above>
src: 20
appid: 8010
language: en_US
clienttype: 2
appvnum: 1.6.4
stamp: <YYYYMMDDHHmmss>
deviceid: <SHA256 hash, 32 uppercase hex chars>
sign: <hex HMAC>
reqid: <MD5 hash>
Content-Type: application/json
User-Agent: iLetComfort/1.6.4 (com.btri.OEMPlus; build:308; iOS 26.3.0) Alamofire/5.5.0
```

**Note:** `clientId` is NOT sent for v1 endpoints (only for v2.0 business endpoints).

### 2.3 Signing Scheme 2 — v2.0 Business API (`signature` header)

Used for all `/midea/open/business/v1/` endpoints.

```
message   = "POST" + path + json_body
signature = base64(HMAC-SHA256(key=CLIENT_SECRET, msg=message))
```

Deterministic — no random/nonce component. The same request body always produces the same signature.

**Status: VERIFIED** against 5/5 captured v2.0 requests.

**Required Headers:**
```http
authorization: Bearer <JWT access token>
clientId: d056337d77334ecc95aca4bff6025533
signature: <Base64 HMAC>
signatureversion: 2.0
reqid: <random UUID hex>
language: en_US
Content-Type: application/json
User-Agent: iLetComfort/1.6.4 (com.btri.OEMPlus; build:308; iOS 26.3.0) Alamofire/5.5.0
```

### 2.4 Signing Scheme 3 — v2.1 SDK API (`signature` header)

Used for `/v2/open/sdk/` endpoints (e.g. `privateKey`).

```
signature = base64(HMAC-SHA256(key=UNKNOWN_SDK_SECRET, msg=?))
```

**Status: NOT CRACKED.** The signing key is embedded in the OEMPlusSDK iOS binary framework.

Exhaustive cracking attempts tested 40+ hypotheses:
- All known keys: clientSecret, appSecret, appKey, iotKey, clientId, encryptKey
- Hex-decoded versions of all keys
- Derived keys: MD5, SHA256, HMAC combinations, XOR, concatenations
- Message format variations: with/without method, path, sorted body, URL-encoded
- Bearer token as part of signed message

The signing key is definitively NOT derivable from any known app constants.

**4 captured v2.1 samples:**

| reqId (truncated) | stamp | signature |
|---|---|---|
| 53b5af6f... | 1772539772390 | mFUxKNSjwKvt5W28a3GH+f49NPAqpKYpSMAvRCAC0OE= |
| 1bc1ffe3... | 1772539803651 | yoobc0eIzn32Uiq/JppP9ASZgLTTtiZSOShGHSdxmxA= |
| 4707b74b... | 1772539834015 | twbJM8tjB7V3e5lKyoqtAj0rpcmM0gmYOgIU1vGDI00= |
| bb5d07eb... | 1772539899927 | ChCnU9p4GsABMSrN+UdnD8Z5MInbq0I+mGPwUuRD0tA= |

**Workaround:** The private keys returned by this endpoint are STATIC per account.
Once fetched (via mitmproxy capture), they can be cached permanently.

### 2.5 JWT Token Structure

```
Format:   us_A_<base64 JWT>
Header:   {"typ": "JWT", "alg": "HS256"}
Payload:  {"aud": "<user_id>", "exp": 1772626207, "iat": 1772539807}
Expiry:   ~24 hours from issuance
Prefix:   us = region, A = type
```

**Single active session:** Only ONE token is valid at a time per account. Logging in from
the iOS app invalidates the CLI token, and vice versa. Each login generates a new token and
immediately revokes the previous one. This means the CLI and app cannot be used concurrently.

**Workaround:** Store login credentials (account + password) and automatically re-authenticate
when a token is rejected (error code 14005). This effectively logs the other client out on
every API call, so interleaved use will cause a constant login war.

### 2.6 Comparison with Open-Source Midea Libraries

The open-source MSmartHome/midea_ac_lan libraries use a DIFFERENT API layer:

| Feature | Open-Source (MSmartHome) | iLetComfort (Dollin) |
|---|---|---|
| API Host | `appsmb.com` (proxied) | `us.dollin.net` (direct) |
| Endpoint style | `/mas/v5/app/proxy?alias=...` | `/midea/open/business/v1/...` |
| Signing | `hex(HMAC-SHA256("PROD_VnoClJI9aikS8dyy", ...))` | `base64(HMAC-SHA256(clientSecret, ...))` |
| Headers | `sign`, `secretVersion: 1`, `random` | `signature`, `signatureversion: 2.0`, `clientid` |
| Token endpoint | `/v1/iot/secure/getToken` (takes `udpid`) | `/v2/open/sdk/appliance/privateKey` (no device params) |
| Account system | MSmartHome accounts | Dollin/OEM accounts |

**MSmartHome proxy login does NOT work for Dollin accounts** — they are completely separate
authentication systems despite both being Midea platforms.

---

## 3. Cloud API Endpoints

### 3.1 Login

```http
POST https://us.dollin.net/v1/user/login
```

**Request Body:**
```json
{
    "loginAccount": "user@example.com",
    "password": "<AES-encrypted hex string, 160 chars>",
    "encryptVersion": "1"
}
```

**Response (success):**
```json
{
    "code": 0,
    "msg": "success!",
    "data": {
        "accessToken": "us_A_eyJ0eXAi...",
        "expiredDate": 1772626207075,
        "uid": "<your_user_id>",
        "userName": "user@example.com",
        "nickName": "nickname",
        "email": "user@example.com",
        "registerRegion": "us",
        "countryName": "South Africa",
        "requestServer": {
            "apiServer": {
                "host": "us.dollin.net",
                "fullHost": "us.dollin.net",
                "port": 443
            },
            "mqttServer": {
                "host": "a39t91kk7ictzi-ats.iot.us-west-2.amazonaws.com",
                "fullHost": null,
                "port": 8883
            }
        }
    }
}
```

**Error Codes:**
- `code: 0` — Success
- `code: 14005` — Token expired or invalid
- Other non-zero codes indicate login failure

### 3.2 Appliance List

```http
POST https://us.dollin.net/midea/open/business/v1/appliance/list
```

**Request Body:**
```json
{"queryAuth": true}
```

**Response:**
```json
{
    "code": 0,
    "data": [{
        "applianceCode": "<your_appliance_code>",
        "name": "Heat Pump",
        "applianceType": "0xC3",
        "modelNumber": "0",
        "online": 1,
        "activeStatus": 0,
        "sn": "<your_serial_number>",
        "sn8": "171000AU",
        "owner": true,
        "authStatus": 0
    }]
}
```

### 3.3 Appliance Info

```http
POST https://us.dollin.net/midea/open/business/v1/appliance/info
```

**Request Body:**
```json
{"applianceCode": "<your_appliance_code>"}
```

Returns extended device metadata including model, firmware, capabilities.

### 3.4 Hexadecimal Control (MAIN COMMAND CHANNEL)

This is the primary endpoint for all device communication. Both queries and SET commands
go through here as raw hex frames.

```http
POST https://us.dollin.net/midea/open/business/v1/appliance/control/hexadecimal
```

**Request Body (query example):**
```json
{
    "applianceCode": "<your_appliance_code>",
    "command": "aa0cc30000000000000301012c"
}
```

**Response:**
```json
{
    "code": 0,
    "data": "aa,3d,c3,00,00,00,00,00,00,03,01,01,04,55,6e,32,55,37,0f,23,37,25,28,c0,00,00,00,23,23,28,17,00,64,07,00,28,00,00,00,27,00,00,00,00,00,00,01,e0,07,bc,07,bc,00,00,00,00,01,78,00,00,01,e0"
}
```

The `command` field is a continuous hex string (no separators).
The `data` response field is comma-separated hex bytes.

### 3.5 Private Keys (LAN Communication)

```http
POST https://us.dollin.net/v2/open/sdk/appliance/privateKey
```

**Request Body:**
```json
{
    "reqId": "<uuid hex>",
    "stamp": "<timestamp_ms>",
    "bizGroup": "msmart"
}
```

**Response:**
```json
{
    "code": 0,
    "data": {
        "msmart": [
            {
                "privateKeyName": "msmartAP",
                "privateKeyValue": "<your_msmart_ap_key>"
            },
            {
                "privateKeyName": "msmartBLE",
                "privateKeyValue": "<your_msmart_ble_key>"
            }
        ]
    }
}
```

**Note:** This endpoint uses v2.1 signing (NOT CRACKED). We can fetch these keys via mitmproxy
or use hardcoded values since they are static per account.

### 3.6 Other Endpoints

```http
POST https://us.dollin.net/midea/open/business/v1/ads
Body: {}
Response: {"adsId": "132353", "adsDomain": "module.appsmb.com", "adsPort": 443}

POST https://us.dollin.net/midea/open/business/v1/country/channel/retrieve
Body: {"countryCode": "ZA"}
```

---

## 4. C3 Heat Pump Protocol (Frames)

### 4.1 General Frame Structure

All commands and responses use the same frame envelope:

```
Offset  Size  Name        Description
------  ----  ----------  -----------
[0]     1     start       Always 0xAA
[1]     1     length      Total frame size minus start byte and checksum
[2]     1     device_type 0xC3 for heat pump
[3-5]   3     padding     0x00
[6]     1     frame_id    0x00 (unused in cloud mode)
[7]     1     frame_proto 0x00
[8]     1     dev_proto   0x00 (device protocol version)
[9]     1     msg_type    0x02=SET, 0x03=QUERY, 0x04=NOTIFY
[10..]  N     body        body[0]=subtype, body[1:]=payload
[-1]    1     checksum    (~sum(frame[1:-1]) + 1) & 0xFF
```

### 4.2 Query Frame (12 bytes)

```
[AA] [0C] [C3] [00 00 00 00 00 00] [03] [subtype] [checksum]
       ↑                              ↑      ↑
    length=12                    msg_type  body (1 byte only!)
```

**Important:** The query body is just the subtype byte alone — NOT `[subtype, 0x01]`.
This differs from some Midea protocol implementations.

Hex examples:
- Status query (0x01): `aa0cc30000000000000301012c`
- Sensor query (0x02): `aa0cc30000000000000302012b`

### 4.3 Query Response Frame (62 bytes)

```
[AA] [3D] [C3] [00 00 00 00 00 00] [03] [subtype] [payload...] [checksum]
       ↑                              ↑
   length=61                    msg_type=0x03 (query response)
```

The body is `frame[10:-1]`, with `body[0]` = subtype and `body[1:]` = payload data.
Data offset `d = 1` is used to skip the subtype byte when decoding fields.

### 4.4 SET Frame (62 bytes)

See [Section 7](#7-set-command--control-frame) for full details.

### 4.5 Checksum Algorithm

```python
checksum = (~sum(frame[1:-1]) + 1) & 0xFF
```

Two's complement of the sum of all bytes from index 1 to the second-to-last byte.

---

## 5. Status Query — Subtype 0x01

Query command: `aa0cc30000000000000301012c`

Response body layout (62-byte frame, body = frame[10:-1], data offset d=1):

### 5.1 Full Byte Map

```
Offset  Field                    Encoding         Notes
------  ---------------------    --------         -----
d+0     pump_status_flags        bitfield         bit0=outdoor_pump, bit1=system_pump
d+1     operating_mode           enum             0=Off, 1=Heat, 2=Cool, 3=Auto, 4=WaterPump
d+2     t5s_def                  raw-35           Active mode target setpoint (heating/cooling)
d+3     t5s_max                  raw-35           DHW max setpoint
d+4     set_temperature          direct           DHW target temperature (Celsius, no offset)
d+5     config_status            byte             Configuration/status byte
d+6     td_max                   raw-35           Heating temperature max
d+7     td_min                   raw-35           Heating temperature min
d+8     ptc_temperature_1        raw-35           PTC heater temperature
d+9     trdh_max                 raw-35           Max return temp for DHW
d+10    trdh_min                 raw-35           Min return temp for DHW
d+11    trdh_def                 raw-35           Default return temp for DHW
d+12    feature_validity_flags   bitfield         bit7=mute_valid, bit6=force_heat_valid, bit5=sterilize_valid
d+13    status_flags             bitfield         bit0=comp_running, bit1=ibh_running, bit2=sterilize_running
d+14    enable_flags_1           byte             Enable/disable flags group 1
d+15    enable_flags_2           byte             Enable/disable flags group 2
d+16    box_bottom_temp          raw-35           Box bottom temperature
d+17    ptc_temperature          raw-35           PTC temperature (main)
d+18    tr_temperature           raw-35           Return temperature
d+19    sterilize_hour           byte             Sterilization schedule hour
d+20    sterilize_min            byte             Sterilization schedule minute
d+21    sterilize_temperature    raw-35           Sterilization target temp
d+22    sterilize_cycle_days     byte             Days between sterilization cycles
d+23    error_code               byte             Current error code (0=no error)
d+24    heat_pump_work_limit     raw-35           Heat pump working temp limit
d+25    vacation_start_year      byte             Vacation schedule start year
d+26    vacation_start_month     byte             Vacation schedule start month
d+27    vacation_start_day       byte             Vacation schedule start day
d+28    vacation_end             packed           bits[7:5]=end_month, bits[4:0]=end_day
d+29..d+34  (reserved/unknown)   —
d+35    exv_drg_hi               }                EXV opening degree (16-bit BE)
d+36    exv_drg_lo               }
d+37    pressure_h_hi            }                High-side pressure (16-bit BE)
d+38    pressure_h_lo            }
d+39    pressure_l_hi            }                Low-side pressure (16-bit BE)
d+40    pressure_l_lo            }
d+41    comp_frq_hi              }                Compressor frequency (16-bit BE)
d+42    comp_frq_lo              }
d+43    total_kwh_hi             }                Total energy consumption kWh (16-bit BE)
d+44    total_kwh_lo             }
d+45    comp_run_hours_hi        }                Compressor total run hours (16-bit BE)
d+46    comp_run_hours_lo        }
d+47    fan_run_hours_hi         }                Fan total run hours (16-bit BE)
d+48    fan_run_hours_lo         }
```

### 5.2 Example Decoded Response

Raw: `aa,3d,c3,00,00,00,00,00,00,03,01,01,04,55,6e,32,55,37,0f,23,37,25,28,c0,00,00,00,23,23,28,17,00,64,07,00,28,00,00,00,27,00,00,00,00,00,00,01,e0,07,bc,07,bc,00,00,00,00,01,78,00,00,01,e0`

| Field | Raw | Decoded |
|---|---|---|
| mode | 0x04 | WaterPump |
| t5s_def | 0x55 (85) | 50°C |
| t5s_max | 0x6e (110) | 75°C |
| set_temperature | 0x32 (50) | 50°C (direct) |
| td_max | 0x55 (85) | 50°C |
| td_min | 0x37 (55) | 20°C |
| feature_validity | 0xC0 | mute=yes, force_heat=yes, sterilize=no |
| comp_running | bit0 of 0x27 | yes |
| error_code | 0x00 | no error |
| comp_frq | 0x07BC | 1980 (Hz?) |
| total_kwh | 0x07BC | 1980 kWh |

---

## 6. Sensor Query — Subtype 0x02

Query command: `aa0cc30000000000000302012b`

Response body layout (62-byte frame, body = frame[10:-1], data offset d=1):

### 6.1 Full Byte Map

```
Offset  Field                    Encoding         Notes
------  ---------------------    --------         -----
d+0     status_byte              byte             General status
d+1..d+10  (reserved)            zeros
d+11    online_num               byte             Number of outdoor units online
d+12    odu_mac_type             byte             Outdoor unit MAC type
d+13    limit_frq_code           byte             Frequency limit code
d+14    tf_temp                  direct           Refrigerant fluid temperature
d+15    tp_temp                  direct           Plate heat exchanger temperature
d+16    th_temp                  direct           DHW tank temperature
d+17    water_pres               byte             Water pressure
d+18    water_flow               byte             Water flow switch
d+19    capacity_hp              byte             Heat pump capacity
d+20    t3_temp                  raw-35           Condenser temperature
d+21    t4_temp                  raw-35           Outdoor ambient temperature
d+22    t2_temp                  raw-35           Evaporator temperature
d+23    t2b_temp                 raw-35           Secondary evaporator temperature
d+24    twin_temp                raw-35           Water inlet temperature
d+25    twout_temp               raw-35           Water outlet temperature
d+26    t1_temp                  raw-35           Suction temperature (204=disconnected)
d+27    odu_current_hi           }                Outdoor unit current (16-bit BE)
d+28    odu_current_lo           }
d+29    odu_voltage              direct           Outdoor unit voltage
d+30    dc_current               direct           DC bus current
d+31    idu_version_b0           }
d+32    idu_version_b1           }                IDU firmware version (3-byte date+ver)
d+33    idu_version_b2           }
d+34    odu_version_b0           }
d+35    odu_version_b1           }                ODU firmware version (3-byte date+ver)
d+36    odu_version_b2           }
d+37    hmi_version_b0           }
d+38    hmi_version_b1           }                HMI firmware version (3-byte date+ver)
d+39    hmi_version_b2           }
d+40    mute_level               byte             0x00=Level 1 (or off), 0x01=Level 2
d+41    ctrl_flag                byte             0x00=normal, 0x01=mute active, 0x02=boost active
d+42    dc_voltage_hi            }                DC bus voltage (16-bit BE)
d+43    dc_voltage_lo            }
d+44    ibh1_run_hours           byte             IBH1 total run hours
d+45    ibh2_run_hours           byte             IBH2 total run hours
d+46    tbh_run_hours            byte             TBH total run hours
d+47    ahs_run_hours            byte             AHS total run hours
d+48    hpc_value                byte             HPC value
```

### 6.2 Firmware Version Encoding

Firmware versions are packed into 3 bytes:

```python
year    = 2000 + (byte0 >> 1)
month   = ((byte0 & 1) << 3) | (byte1 >> 5)
day     = byte1 & 0x1F
version = byte2
# Example: 0x31, 0x37, 0x0C → 2024-09-23 v12
```

### 6.3 Example Decoded Response

Raw: `aa,3d,c3,00,00,00,00,00,00,03,02,01,00,...,01,02,00,22,2c,1f,00,01,00,39,3c,46,4b,42,42,ef,...`

| Field | Raw | Decoded |
|---|---|---|
| tf_temp | 0x22 (34) | 34°C (direct) |
| tp_temp | 0x2C (44) | 44°C (direct) |
| th_temp | 0x1F (31) | 31°C (direct) |
| t3_temp (condenser) | 0x39 (57) | 22°C (57-35) |
| t4_temp (outdoor) | 0x3C (60) | 25°C (60-35) |
| t2_temp (evaporator) | 0x46 (70) | 35°C (70-35) |
| t2b_temp | 0x4B (75) | 40°C (75-35) |
| twin_temp (inlet) | 0x42 (66) | 31°C (66-35) |
| twout_temp (outlet) | 0x42 (66) | 31°C (66-35) |
| t1_temp (suction) | 0xEF (239) | N/A (sensor disconnected) |

### 6.4 Subtype 0x10 — NOT SUPPORTED

Sending a subtype 0x10 (UnitPara) query returns **error code 1214**. This device does not
support the standard Midea C3 UnitPara query. Use subtype 0x02 for all sensor data.

---

## 7. SET Command — Control Frame

SET commands use msg_type=0x02 and produce a 62-byte frame. The critical requirement is
that SET frames must **echo specific bytes from the most recent status (0x01) query response**.
This means you must always query status before sending a SET command.

### 7.1 SET Frame Structure (62 bytes)

```
Offset  Size  Value/Field          Description
------  ----  ----------------     -----------
[0]     1     0xAA                 Start byte
[1]     1     0x3D (61)            Length
[2]     1     0xC3                 Device type
[3-8]   6     0x00                 Padding
[9]     1     0x02                 msg_type = SET
[10]    1     0x01                 Subtype (always 0x01 for control)
[11]    1     0x01                 Control type (always 0x01)
[12]    1     MODE                 Operating mode (see below)
[13]    1     TEMP + 35            Target temperature (+35 offset encoded)
[14]    1     status[d+4]          Echo: sterilize_hour (typically 0x23)
[15]    1     status[d+5]          Echo: t5s_def (typically 0x28)
[16]    1     status[d+6]          Echo: version (typically 0x17)
[17]    1     0x00                 Zero
[18]    1     status[d+8]          Echo: sterilize_temp encoding (typically 0x64)
[19]    1     status[d+9]          Echo: sterilize_cycle (typically 0x07)
[20-22] 3     0x00                 Zeros
[23]    1     status[d+13]         Echo: status flags (typically 0x27)
[24]    1     0x00                 Zero
[25]    1     MUTE_LEVEL           0x00=level1, 0x01=level2
[26]    1     CTRL_FLAG            0x00=normal, 0x01=mute, 0x02=boost
[27]    1     0x00                 Zero
[28]    1     status[d+18]         Echo: tr_temperature (typically 0x28)
[29-60] 32    0x00                 Zeros
[61]    1     CHECKSUM             Two's complement checksum
```

### 7.2 SET Mode Values

**IMPORTANT:** The SET command uses DIFFERENT mode numbers than the query response!

| Mode | SET Value (frame[12]) | Query Response Value (d+1) |
|---|---|---|
| Off | 0x00 | 0x00 |
| Heat | 0x01 | 0x01 |
| Cool | 0x03 | 0x02 |
| Water Pump | 0x04 | 0x04 |

### 7.3 Control Flag Values

| CTRL_FLAG (frame[26]) | Effect |
|---|---|
| 0x00 | Normal operation |
| 0x01 | Mute (silent mode) enabled |
| 0x02 | Boost mode enabled |

When mute is active, MUTE_LEVEL (frame[25]) selects the level:
- 0x00 = Silent level 1 (less quiet)
- 0x01 = Silent level 2 (more quiet)

### 7.4 Temperature Encoding in SET

The target temperature in the SET frame (frame[13]) is encoded as `temperature + 35`.
For example, to set 28°C: `28 + 35 = 63 = 0x3F`.

### 7.5 Temperature Validation Ranges

| Mode | Min (°C) | Max (°C) |
|---|---|---|
| Heat | 10 | 40 |
| Cool | 12 | 40 |
| Water Pump | 15 | 75 |

### 7.6 Status Echo Bytes

The SET frame MUST include bytes echoed from the most recent subtype 0x01 query response.
If these echo bytes don't match, the device may reject the command. The echo bytes come from
the status response body at the following offsets (d=1, same data_offset as decoding):

```
SET frame[14] ← status_body[d+4]   (set_temperature / sterilize config)
SET frame[15] ← status_body[d+5]   (config_status)
SET frame[16] ← status_body[d+6]   (td_max)
SET frame[18] ← status_body[d+8]   (ptc_temperature_1)
SET frame[19] ← status_body[d+9]   (trdh_max)
SET frame[23] ← status_body[d+13]  (status_flags)
SET frame[28] ← status_body[d+18]  (tr_temperature)
```

### 7.7 SET Command Workflow

```
1. Query current status:  send subtype 0x01 query
2. Parse response:        extract status body bytes + decode current mode/temp
3. Merge changes:         overlay requested changes onto current values
4. Build SET frame:       62 bytes with echo bytes from step 2
5. Send SET frame:        via hexadecimal control endpoint
6. Verify (optional):     query status again to confirm change
```

### 7.8 Example SET Frames

**Set mode to Heat at 28°C (normal operation):**
```
aa3dc3000000000000020101013f[echo bytes...]000000000000...00[checksum]
                         ↑  ↑
                    heat  28+35=63=0x3F
```

**Enable boost mode (keeping current mode/temp):**
```
frame[26] = 0x02  (boost)
```

**Enable mute level 2 (keeping current mode/temp):**
```
frame[25] = 0x01  (level 2)
frame[26] = 0x01  (mute)
```

**Power off:**
```
frame[12] = 0x00  (mode off)
```

---

## 8. Temperature Encoding

The ITS/BTRI protocol uses two temperature encoding schemes:

### 8.1 Offset Encoding (raw - 35)

Most temperature fields in both status and sensor responses:

```
actual_temperature = raw_byte - 35
```

Examples:
- 0x37 (55) → 20°C
- 0x3F (63) → 28°C
- 0x55 (85) → 50°C
- 0x6E (110) → 75°C

Special value:
- 0xEF (239) → 204 after offset → **sensor disconnected**

### 8.2 Direct Encoding (no offset)

A few fields use the raw byte value directly with no offset:
- `tf_temp` — refrigerant fluid temperature (subtype 0x02, d+14)
- `tp_temp` — plate heat exchanger temperature (subtype 0x02, d+15)
- `th_temp` — DHW tank temperature (subtype 0x02, d+16)
- `set_temperature` — DHW target (subtype 0x01, d+4)
- `odu_voltage` — outdoor unit voltage (subtype 0x02, d+29)

### 8.3 SET Command Temperature

In the SET frame, temperature is encoded as `value + 35` (the reverse of query decoding).

---

## 9. Mode Mapping Discrepancy

**This is a critical implementation detail.** The operating mode values differ between
query responses and SET commands:

```
Mode         Query Response (d+1)    SET Command (frame[12])
---------    -------------------     ----------------------
Off          0                       0
Heat         1                       1
Cool         2                       3
Auto         3                       (not observed in SET)
Water Pump   4                       4
```

When reading the current mode from a query and echoing it back in a SET command, you
MUST translate between these two numbering schemes.

---

## 10. LAN Communication

### 10.1 Current Status: NOT WORKING

Direct LAN communication with the heat pump is not currently possible for several reasons:

1. **Device not on local network** — Port 6444 scan found no device responding
2. **iLetComfort is cloud-only** — No LAN IP cached in app data
3. **Missing per-device token** — The Midea V3 LAN handshake requires a 64-byte
   device-specific token, which is NOT available through the Dollin API
4. **Dollin API has no getToken endpoint** — `/v1/iot/secure/getToken` returns 404

### 10.2 Account-Wide Keys (Available)

The msmartAP and msmartBLE private keys were successfully fetched via the
`/v2/open/sdk/appliance/privateKey` endpoint:

```
msmartAP:  96 hex chars = 48 bytes (16-byte header + 32-byte key)
msmartBLE: 64 hex chars = 32 bytes
```

These are account-wide keys, not device-specific. They would be used for LAN discovery
and initial handshake, but the per-device token is still required for the V3 protocol.

### 10.3 UDPID Computation

For reference, the UDPID is computed from the appliance ID:

```python
id_bytes = appliance_id.to_bytes(6, "little")
digest   = sha256(id_bytes).digest()
udpid    = (digest[:16] XOR digest[16:]).hex()  # 32-char hex
```

---

## 11. MQTT Real-Time Updates

- **Broker:** AWS IoT Core (us-west-2)
- **Host:** `a39t91kk7ictzi-ats.iot.us-west-2.amazonaws.com`
- **Port:** 8883 (TLS)
- **Protocol:** MQTT over TLS

Used for real-time device state push notifications. Not captured via HTTP proxy (separate
TCP/TLS connection). Would require Wireshark/tcpdump capture and AWS IoT MQTT topic analysis.

The login response provides the MQTT server details, suggesting the app subscribes to
device-specific topics after authentication.

---

## 12. Server-Side Decode Endpoint

The iBUILDING HVAC platform has a server-side decode endpoint that can parse raw hex frames:

```http
POST https://us.ibuildinghvac.com/api/mibp-connector-mo-parser/mibp/sys/mg_iot/decode
```

Accepts raw hex + device info JSON, returns decoded field names and values. Uses the same
auth headers as the Dollin API (v1 signing with "meicloud" prefix).

This endpoint was useful during reverse engineering to validate our decoder implementation.

---

## 13. App Constants & Secrets

### 13.1 API Credentials

| Constant | Value | Usage |
|---|---|---|
| APP_SECRET | `SIT_4VjZdg19laDoIrut` | v1 HMAC signing key |
| APP_KEY | `btri` | v1 signing prefix (non-login) |
| IOT_KEY | `meicloud` | v1 signing prefix (login) |
| CLIENT_ID | `d056337d77334ecc95aca4bff6025533` | v2.0 header |
| CLIENT_SECRET | `35b965531383ce9f37f829a19712bf3a` | v2.0 HMAC signing key |
| ENCRYPT_KEY | `4dbc9ff6c15944d78eebb581c2b23de3` | Password AES encryption |
| APP_ID | `8010` | v1 header |

### 13.2 Device Info

| Property | How to obtain |
|---|---|
| Appliance Code | `python3 iletcomfort_client.py list` → `applianceCode` field |
| Serial Number | `python3 iletcomfort_client.py list` → `sn` field |
| Model SN8 | `python3 iletcomfort_client.py list` → `sn8` field |
| Device Type | 0xC3 (Heat Pump) |
| Device Subtype | 1 |
| Brand | ITS |
| User ID | Login response → `uid` field |

### 13.3 Infrastructure

| Service | URL/Host |
|---|---|
| API Base | `https://us.dollin.net` |
| iBUILDING | `https://us.ibuildinghvac.com` |
| MQTT Broker | `a39t91kk7ictzi-ats.iot.us-west-2.amazonaws.com:8883` |
| Sentry | `sentry.msmartlife.net` (key: `bc30997250a25e5e81910fe1c0480080`) |
| Ads Module | `module.appsmb.com:443` |

### 13.4 Obtaining Your Device-Specific Values

The app constants in 13.1 are universal (extracted from the iOS binary). The values below are specific to your account/device and must be obtained yourself:

1. **Login & User ID** — Run `python3 iletcomfort_client.py login --account EMAIL --password PASSWORD`. The response includes your `uid` and `accessToken`.

2. **Appliance Code & Serial Number** — Run `python3 iletcomfort_client.py list` after logging in. Each device entry includes `applianceCode`, `sn`, and `sn8`.

3. **LAN Private Keys (msmartAP/msmartBLE)** — These require the v2.1 SDK signing key which has not been cracked. To obtain them:
   - Set up [mitmproxy](https://mitmproxy.org/) to intercept HTTPS traffic from the iLetComfort iOS/Android app
   - Open the app and navigate to trigger a `/v2/open/sdk/appliance/privateKey` request
   - Copy the key values from the intercepted response
   - Keys are static per account — you only need to capture them once

4. **MQTT Credentials** — The MQTT broker hostname is returned in the login response under `requestServer.mqttServer`. Authentication details require further reverse engineering.

### 13.5 App Forensics

- iOS app container: `~/Library/Containers/1DC91666-D548-4DF5-9E08-32D7668BB8E4/`
- App logs **plaintext secrets** (token, device info) on every startup in `Documents/OEMPlus/*.log`
- OEMPlusSDK framework contains the v2.1 signing key (not yet extracted)

---

## 14. Key Differences from midea-local

This device uses the **ITS/BTRI proprietary protocol** which differs significantly from the
standard Midea C3 protocol used by midea-local/midea_ac_lan:

| Feature | Standard Midea C3 (midea-local) | ITS/BTRI Protocol (this device) |
|---|---|---|
| Temperature encoding | Various (half-degree, signed) | raw - 35 offset (mostly) |
| Subtype 0x01 body | C3BasicBody (zones, DHW, curve) | ITS status (pump, mode, DHW, sterilization) |
| Subtype 0x02 body | C3EnergyBody | ITS sensors (temps, electrical, firmware) |
| Subtype 0x10 | UnitPara (supported) | **NOT SUPPORTED** (error 1214) |
| Mode values | 0=None,1=Cool,2=Heat,3=Auto | Query: 0=Off,1=Heat,2=Cool,3=Auto,4=WP |
| SET mode values | Same as query | **DIFFERENT** from query! |
| Zone support | Zone1 + Zone2 | Single zone (DHW focus) |
| Direct temps | None | tf, tp, th (no offset) |

**The midea-local C3BasicBody and C3EnergyBody decoders are WRONG for this device.**
Attempting to use them produces garbage values.

---

## 15. Python Client Reference

### 15.1 CLI Commands

```bash
# Authentication
python3 iletcomfort_client.py login --account EMAIL --password PASSWORD
python3 iletcomfort_client.py set-token JWT_TOKEN

# Device discovery
python3 iletcomfort_client.py list
python3 iletcomfort_client.py info [--appliance CODE]

# Monitoring
python3 iletcomfort_client.py status [--appliance CODE] [--json]
python3 iletcomfort_client.py sensors [--appliance CODE] [--json]

# Control
python3 iletcomfort_client.py set --mode {off,heat,cool,waterpump}
python3 iletcomfort_client.py set --temp N
python3 iletcomfort_client.py set --boost {on,off}
python3 iletcomfort_client.py set --mute {off,1,2}
python3 iletcomfort_client.py set --mode heat --temp 28   # combined

# Raw commands
python3 iletcomfort_client.py raw HEX_COMMAND [--appliance CODE]
python3 iletcomfort_client.py decode HEX_RESPONSE

# LAN keys
python3 iletcomfort_client.py fetch-lan-key [--force]
python3 iletcomfort_client.py set-lan-key --appliance CODE --token TOK --key KEY
```

### 15.2 Python API

```python
from iletcomfort_client import ILetComfortClient

client = ILetComfortClient()
client.load_token()

# Query
status = client.query_status(appliance_code)
sensors = client.query_sensors(appliance_code)

# Control
client.set_device(appliance_code, mode=MODE_HEAT, temperature=28)
client.set_device(appliance_code, boost=True)
client.set_device(appliance_code, mute=2)  # silent level 2
client.set_device(appliance_code, mode=MODE_OFF)  # power off
```

---

## 16. Open Questions & Future Work

### Resolved
- [x] Query status (subtype 0x01) — WORKING
- [x] Query sensors (subtype 0x02) — WORKING
- [x] SET commands (mode, temperature, boost, mute) — IMPLEMENTED
- [x] Temperature encoding scheme — DECODED (raw-35 offset, with direct exceptions)
- [x] v1 login signing — VERIFIED
- [x] v2.0 business API signing — VERIFIED
- [x] Password encryption — DECODED (AES-128-CBC with SHA256 key derivation)
- [x] Sensor ctrl_flag and mute_level — d+41 is ctrl_flag (0=normal, 1=mute, 2=boost), d+40 is mute_level (0=L1, 1=L2)
- [x] Active mode setpoint — t5s_def (d+2, offset-encoded) is the active heating/cooling target, NOT set_temperature (d+4, which is DHW tank target)

### Open
- [ ] **v2.1 SDK signing key** — Extract from iOS binary (OEMPlusSDK framework) or Android APK
- [ ] **LAN communication** — Requires device on local network + per-device V3 token
- [ ] **MQTT integration** — Capture and decode MQTT topics for real-time push updates
- [x] **Home Assistant integration** — Custom component built (cloud-based, via ha-iletcomfort repo)
- [ ] **Additional SET features** — Sterilization schedule, vacation mode, DHW boost
- [ ] **Error code reference** — Map error_code values to human-readable descriptions
- [ ] **Subtype 0x04 (notify)** — Decode unsolicited push notifications from device
