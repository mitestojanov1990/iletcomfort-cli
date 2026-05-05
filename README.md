# iLetComfort

Reverse-engineered Python client for ITS/BTRI heat pumps (Midea 0xC3) controlled via the iLetComfort iOS app.

The iLetComfort app (com.btri.OEMPlus) by Shanghai KONG / ITS brand is built on the Midea Dollin OEM platform but uses a **proprietary protocol variant** — standard midea-local decoders produce garbage for this device.

## Features

- Full cloud API client (login, device discovery, status, sensors, control)
- Decoded ITS/BTRI protocol (status subtype 0x01, sensors subtype 0x02)
- SET commands: mode (heat/cool/waterpump/off), target temperature, boost, silent mode (L1/L2)
- CLI interface for interactive use and scripting

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install requests cryptography
```

## Usage

```bash
# Authenticate
python3 iletcomfort_client.py login --account user@example.com --password YOUR_PASSWORD

# List appliances
python3 iletcomfort_client.py list

# Query status and sensors
python3 iletcomfort_client.py status
python3 iletcomfort_client.py sensors

# Control
python3 iletcomfort_client.py set --mode heat --temp 28
python3 iletcomfort_client.py set --boost on
python3 iletcomfort_client.py set --mute 2        # silent level 2
python3 iletcomfort_client.py set --mode off       # power off

# Raw hex command
python3 iletcomfort_client.py raw aa0cc30000000000000301012c
```

## Protocol Notes

- Device type 0xC3, communicates via `us.dollin.net` cloud API
- Temperature encoding: most fields use `raw_byte - 35` offset; some (tf, tp, th) are direct
- Query and SET commands use **different mode numbering** — see [API_FINDINGS.md](API_FINDINGS.md)
- Single active session per account (logging in from the iOS app invalidates the CLI token)

## Home Assistant

A custom component is available at [ha-iletcomfort](https://github.com/tgenov/ha-iletcomfort).

## Documentation

See [API_FINDINGS.md](API_FINDINGS.md) for the full protocol reference.

## Web UI

A small Flask dashboard is available for read-only browsing of all data the
CLI exposes. It shares the CLI's cached token to minimize the
single-active-session conflict with the iOS app.

### Setup

```bash
pip install -r requirements.txt
```

Set credentials either as environment variables or in `~/.iletcomfort_web.env`
(one `KEY=value` per line):

```
ILETCOMFORT_ACCOUNT=user@example.com
ILETCOMFORT_PASSWORD=your-iletcomfort-password
ILETCOMFORT_API_BASE=https://eu.dollin.net   # or https://us.dollin.net (default)
WEBUI_PASSWORD=password-for-the-web-form
```

A session-cookie signing key (`WEBUI_SECRET_KEY`) is auto-generated and
persisted to `~/.iletcomfort_web_secret` on first run. To override the bind
address, set `WEBUI_HOST` (default `127.0.0.1`) and `WEBUI_PORT` (default
`8000`).

### Run

```bash
.venv/bin/python iletcomfort_web.py
```

Then open `http://127.0.0.1:8000/`.

The UI is on-demand — the Refresh button is the only way new data is
fetched. No auto-polling. **Do not expose to the internet without TLS:** the
web password is sent in plaintext on each login.
