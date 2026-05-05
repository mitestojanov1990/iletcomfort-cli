# Capturing iLetComfort SET Commands From the Android App

## Why

The ITS short-frame firmware variant (Section 17 of `API_FINDINGS.md`) accepts SET frames in a layout that does **not** match the spec. Sending guessed SET frames mutates real device state in ways we can't predict — the protocol does not protocol-clamp inputs, and at least one byte position has been observed to silently activate persistent subsystems we can't reset from the app UI.

The only safe way to learn the SET frame layout for this firmware is to capture an actual SET request from a known-working client. The Android app (`com.btri.OEMPlus`) is the most accessible source.

What you get from one capture:
- The exact SET frame size for this firmware (probably 36 bytes, not the spec's 62).
- The byte position and encoding of mode, setpoint, and toggle bits.
- Which echo bytes the device validates (if any).

## Tooling Choices

Three options, ordered by setup cost:

| Tool | Setup time | Pros | Cons |
|---|---|---|---|
| **HTTP Toolkit** (recommended) | ~15 min | One-click ADB cert install into emulator; nice UI for inspecting JSON bodies | Requires you to either install on an Android emulator or have a rooted/patched real device |
| **mitmproxy** + `apk-mitm` | ~30 min | Works on real (unrooted) device after re-installing patched APK | More moving parts; you lose the existing app's cached login |
| **Frida + objection** | ~60 min | Bypasses cert pinning at runtime on rooted device | Needs root; overkill for a one-shot capture |

Below: the HTTP Toolkit + Android emulator path. It avoids touching your phone.

## Setup — HTTP Toolkit + Android Emulator

### 1. Install HTTP Toolkit

Free for non-commercial use. Mac: `brew install --cask http-toolkit` or download from <https://httptoolkit.com/>.

### 2. Install Android Studio (for the emulator only)

You only need the SDK tools and a system image, not the IDE day-to-day.

```bash
brew install --cask android-studio
```

Open Android Studio once → "More Actions" → "Virtual Device Manager" → create a new device. Pick a phone profile (e.g. Pixel 5) and a system image. **Choose an image with `(Google APIs)` not `(Google Play)`** — Play Store images run a hardened build that blocks user CA installs. If only Play Store images are available for the version you want, that's a blocker; use a different API level.

Boot the emulator at least once.

### 3. Get the iLetComfort APK

Two paths:

- **Easy:** export from a real Android device that has the app installed. With ADB enabled on the device:
  ```bash
  adb shell pm path com.btri.OEMPlus
  # prints something like: package:/data/app/com.btri.OEMPlus-xxxx/base.apk
  adb pull /data/app/com.btri.OEMPlus-xxxx/base.apk iletcomfort.apk
  ```
- **Otherwise:** download from a reputable mirror like APKMirror. **Verify the package name** is `com.btri.OEMPlus` — there are unrelated apps with similar names.

### 4. Launch HTTP Toolkit's Android interceptor

In HTTP Toolkit:
1. Click **Intercept** in the left sidebar.
2. Pick **Android Device via ADB** (works for emulators too, as long as the emulator is running and `adb devices` shows it).
3. HTTP Toolkit pushes its CA into the system trust store automatically (this is why `(Google APIs)` images are required — they allow modifying `/system`).

You should see a "Connected" banner. The emulator's traffic is now flowing through HTTP Toolkit's local mitm proxy.

### 5. Install and run the app

Drag your `iletcomfort.apk` onto the emulator window, or:
```bash
adb install iletcomfort.apk
```

Open the app, log in with your iLetComfort account credentials.

> Logging in here will kick your phone's iOS/Android app session (single active session per account). After capture, log back in on your real device.

### 6. Capture a SET command

In HTTP Toolkit's **View** tab, filter to `host: eu.dollin.net` (or `us.dollin.net` for US accounts).

Trigger one specific change in the app, e.g. **switch zone1 from Cool to Heat**. You should see a POST to:

```
https://eu.dollin.net/midea/open/business/v1/appliance/control/hexadecimal
```

Click the request. The body looks like:

```json
{
  "applianceCode": "<your_appliance_code>",
  "command": "aa<...>"
}
```

**The `command` field is the SET frame.** Copy the hex string. Note exactly which knob you turned.

Repeat for the changes you want to map — each one takes ~30 seconds:
- Switch zone1 Cool ↔ Heat
- Change zone1 setpoint by 1°C
- Toggle DHW on/off
- Change DHW setpoint by 1°C
- Toggle TBH on/off
- Toggle Curve on/off
- Toggle Fast DHW on/off

For each: capture both the SET hex sent AND the response hex returned, plus a fresh `status` query immediately after (via the CLI: `.venv/bin/python iletcomfort_client.py --api-base https://eu.dollin.net raw aa0cc30000000000000301012c`).

## Analysing Captures

Once you have a handful of SET frames with known semantics, do this for each one:

1. **Frame size:** count the bytes. Probably 36, possibly different.
2. **Diff against another captured SET** (same operation, slightly different parameter — e.g. setpoint 23 vs 24). The bytes that differ tell you exactly where that parameter lives.
3. **Compare against the status frame captured immediately before the SET.** Bytes that match the status are echo bytes (the device may or may not validate them). Bytes that differ are control inputs.
4. **Document each finding** in `API_FINDINGS.md` Section 17.4.

### Useful diff helper

```bash
.venv/bin/python <<'EOF'
a = "aa3dc3...capture_a..."
b = "aa3dc3...capture_b..."
ab = bytes.fromhex(a)
bb = bytes.fromhex(b)
for i, (x, y) in enumerate(zip(ab, bb)):
    if x != y:
        print(f"  [{i:2d}] 0x{x:02x} -> 0x{y:02x}  (delta {y-x:+d})")
EOF
```

## Once You Have the SET Layout

1. Add a `_build_c3_set_short(...)` to `iletcomfort_client.py` mirroring the existing `build_c3_set` but with the verified offsets.
2. In the existing CLI `set` command, dispatch on the same firmware-variant detection used for the decoder.
3. Add tests covering each verified SET parameter.
4. Update `API_FINDINGS.md` Section 17.4 — replace the "unknown" notes with documented offsets and bit meanings.
5. Optionally enable SET in the web UI as a separate page.

Until then: the CLI's `set` and the web UI remain read-only on this firmware variant.

## Recovery From Stuck State

If a SET experiment leaves the unit in a state the app can't reset (as happened with `frame[11]` bit 4 and `frame[19]`):

1. **Power-cycle at the breaker** — ~30 seconds off. Most likely to clear non-persistent firmware state.
2. **Check for a service / engineer / installer menu** in the app — typically reached via long-press on a version number, a hidden PIN, or an off-screen swipe. Hardware vendor docs may reference it.
3. **Compare with a fresh captured status before any SET work** to confirm baseline.
