# On-Device Field Inventory (HMI / Service Menu)

This document maps every value the device's local HMI (KANION ECOSPAR 16 kW
Indoor, M-Thermal Split, EU short-frame firmware variant) exposes through its
`OPERATION PARAMETER` service menu and home screen, against the bytes returned
by the cloud `subtype 0x01` (status) and `subtype 0x02` (sensor) queries.

The on-device labels are the **authoritative names** for each sensor — they
override the labels used in upstream Midea references. Several names in
`API_FINDINGS.md` (notably `tp_temp`, `th_temp`) were initially inherited from
midea-local and disagree with what the firmware itself calls these values.

## 1. Capture Setup

- **Hardware:** KANION ECOSPAR 16 kW Indoor (Hidro Company DOOEL importer)
- **HMI part number:** `0000C331 01 71H120F 22174100262ZCX8`
  - `C3` = device type (heat pump)
  - `01` = device subtype
  - `71H120F` = SN8 prefix → EU short-frame firmware variant
- **Firmware:** IDU 2021-10-26 v99, ODU 2021-09-22 v32, HMI 2021-10-14 v74
- **Cloud region:** `eu.dollin.net`
- **Capture state:** OPERATE MODE = OFF, ONLINE UNITS = 1, last selected mode = Cool

Photos of the on-device screens are stored under `docs/images/`.

## 2. Captured Frames (idle state)

### Status frame (subtype 0x01) — 36 bytes
```
aa,23,c3,00,00,00,00,00,00,03,01,
10,17,00,02,02,17,17,2d,17,41,23,19,05,37,19,19,05,3c,22,3c,14,21,00,80,
3a
```

### Sensor frame (subtype 0x02) — 49 bytes
```
aa,30,c3,00,00,00,00,00,00,03,02,
00,03,23,05,00,0c,28,05,28,05,00,0a,00,03,23,00,00,00,00,03,23,00,00,00,00,03,00,00,00,00,00,03,00,00,00,00,00,
1b
```

The sensor frame contains a recurring `03,23` record header at indices
`[12-13]`, `[24-25]`, `[30-31]`, `[36]`, `[42]`, with zero payloads on this
firmware. **No live sensor temperatures appear in the 0x02 frame.** Every
sensor value the cloud sees is in the status (0x01) frame.

## 3. Field Inventory — Home Screen

| On-device label | Value | Frame index | Encoding | Hex | Verified |
|---|---|---|---|---|---|
| Outdoor air (top right) | 28°C | — | — | — | NOT IN CLOUD FRAME |
| Heat zone setpoint (left, snowflake idle) | 23°C | status[16] | direct | `0x17` | ✓ |
| DHW tank temperature (right, tap icon) | 33°C | status[32] | direct | `0x21` | ✓ |
| Mode badge (centre) | OFF | status body byte 0 (live_ops) | bit | `[11]=0x10` | ✓ (zone-heat, dhw-heat, tbh, fast-dhw bits all 0; bit4 persistent) |
| Date/time | 06-05-2026 15:29 | — | — | — | clock not in protocol |
| WiFi indicator | — | — | — | — | — |

## 4. Field Inventory — Service Information / Parameter Display

| On-device label | Value | Frame index | Encoding | Hex | Verified |
|---|---|---|---|---|---|
| ROOM SET TEMP | -- | (unmapped) | — | — | — |
| MAIN SET TEMP | 23°C | status[16] | direct | `0x17` | ✓ |
| TANK SET TEMP | 45°C | status[18] | direct | `0x2d` | ✓ |
| ROOM ACTUAL TEMP | -- | (unmapped) | — | — | — |

## 5. Field Inventory — OPERATION PARAMETER Pages 1/9 – 9/9

Idle state (compressor off, no flow). Anywhere the on-device LCD shows a value,
the predicted byte location and observed hex are listed. `—` means no
candidate byte for this field has been confirmed in either captured frame.

### Page 1/9 — System State

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| ONLINE UNITS NUMBER | 1 | — | — | — | not seen in 0x02 frame |
| OPERATE MODE | OFF | status[14] | enum (2=Cool,3=Heat) | `0x02` | ✓ — last mode persists when device off |
| SV1 STATE | OFF | status[11]? | bitfield | `0x10` | partial — bit5/SV unknown |
| SV2 STATE | OFF | status[11]? | bitfield | `0x10` | partial |
| SV3 STATE | OFF | status[11]? | bitfield | `0x10` | partial |
| PUMP_I | OFF | status[11]? | bitfield | `0x10` | partial |

### Page 2/9 — Pumps & Backup Heaters

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| PUMP_O | OFF | status[11] | bit | — | bits 0-3 idle, exact bit-to-pump mapping unknown |
| PUMP_C | OFF | status[11] | bit | — | unknown bit |
| PUMP_S | OFF | status[11] | bit | — | unknown bit |
| PUMP_D | OFF | status[11] | bit | — | unknown bit |
| PIPE BACKUP HEATER (IBH) | OFF | status[11] | bit | — | doc claims bit5 = TBH, IBH unknown |
| TANK BACKUP HEATER (TBH) | OFF | status[11] bit5 | bit | clear in `0x10` | ✓ |

### Page 3/9 — Hydraulic / Power

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| GAS BOILER | OFF | (unmapped) | — | — | — |
| T1 LEAVING WATER TEMP | -- | (unmapped) | — | — | sensor likely disconnected |
| WATER FLOW | 0.00 M³/H | (unmapped) | — | — | — |
| HEAT PUMP CAPACITY | 0.00 kW | (unmapped) | — | — | — |
| POWER CONSUM | 29 kkWh | (unmapped) | — | — | possibly 16-bit total |
| Ta ROOM TEMP | -- | (unmapped) | — | — | — |

### Page 4/9 — Water Circuit Temperatures

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| T5 WATER TANK TEMP | 33°C | status[32] | direct | `0x21` | ✓ |
| Tw2 CIRCUIT2 WATER TEMP | -- | (unmapped) | — | — | — |
| T1S' C1 CLI. CURVE TEMP | -- | (unmapped) | — | — | — |
| T1S2' C2 CLI. CURVE TEMP | -- | (unmapped) | — | — | — |
| TW_O PLATE W-OUTLET | 25°C | status[28] or [30] | raw-35 | `0x3C` | ✓ (one of [28]/[30]) |
| TW_I PLATE W-INLET | 25°C | status[28] or [30] | raw-35 | `0x3C` | ✓ (one of [28]/[30]) |

### Page 5/9 — Buffer Tank / Solar / IDU Software

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| Tbt1 BUFFERTANK_UP TEMP | -- | (unmapped) | — | — | sensor disconnected |
| Tbt2 BUFFERTANK_LOW TEMP | -- | (unmapped) | — | — | sensor disconnected |
| Tsolar | -- | (unmapped) | — | — | sensor disconnected |
| IDU SOFTWARE | 26-10-2021V99 | — | — | — | NOT IN CLOUD FRAME |

### Page 6/9 — Compressor / EXV

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| ODU MODEL | 16 kW | (unmapped) | — | — | capacity code |
| COMP. CURRENT | 0 A | (unmapped) | — | — | — |
| COMP. FREQUENCY | 0 Hz | (unmapped) | — | — | — |
| COMP. RUN TIME (cycle) | 5 MIN | (unmapped) | — | — | — |
| COMP. TOTAL RUN TIME | 12937 Hrs | (unmapped) | — | — | NOT IN CLOUD FRAME |
| EXPANSION VALVE | 480 P | (unmapped) | — | — | NOT IN CLOUD FRAME |

### Page 7/9 — Electrical

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| FAN SPEED | 0 R/MIN | (unmapped) | — | — | — |
| IDU TARGET FREQUENCY | 0 Hz | (unmapped) | — | — | — |
| FREQUENCY LIMITED TYPE | 0 | (unmapped) | — | — | — |
| SUPPLY VOLTAGE | 238 V | (unmapped) | — | — | NOT IN CLOUD FRAME |
| DC GENERATRIX VOLTAGE | 320 V | (unmapped) | — | — | NOT IN CLOUD FRAME |
| DC GENERATRIX CURRENT | 0 A | (unmapped) | — | — | — |

### Page 8/9 — Plate Heat Exchanger / Compressor Pipe Temps

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| TW_O PLATE W-OUTLET | 25°C | status[28] or [30] | raw-35 | `0x3C` | ✓ |
| TW_I PLATE W-INLET | 25°C | status[28] or [30] | raw-35 | `0x3C` | ✓ |
| T2 PLATE F-OUT TEMP | 25°C | status[22]/[25]/[26] | direct | `0x19` | ✓ (one of these) |
| T2B PLATE F-IN TEMP | 25°C | status[22]/[25]/[26] | direct | `0x19` | ✓ |
| **Th COMP. SUCTION TEMP** | 28°C | (unmapped) | — | — | label clarifies that "Th" on this device = compressor SUCTION pipe, NOT DHW tank |
| **Tp COMP. DISCHARGE TEMP** | 28°C | (unmapped) | — | — | label clarifies that "Tp" on this device = compressor DISCHARGE, NOT plate exchanger |

### Page 9/9 — Outdoor / Pressure / Software

| Label | Value | Index | Enc | Hex | Verified |
|---|---|---|---|---|---|
| T3 OUTDOOR EXCHANGE TEMP | 28°C | (unmapped) | — | — | NOT IN CLOUD FRAME |
| T4 OUTDOOR AIR TEMP | 28°C | (unmapped) | — | — | NOT IN CLOUD FRAME |
| TF MODULE TEMP | 31°C | (unmapped) | — | — | NOT IN CLOUD FRAME |
| P1 COMP. PRESSURE | 1710 kPa | (unmapped) | — | — | NOT IN CLOUD FRAME |
| ODU SOFTWARE | 22-09-2021V32 | — | — | — | NOT IN CLOUD FRAME |
| HMI SOFTWARE | 14-10-2021V74 | — | — | — | NOT IN CLOUD FRAME |

## 6. Headline Findings

1. **EU short-frame firmware emits all live sensor data in the status (0x01)
   frame.** The sensor (0x02) frame is a 49-byte stub with recurring `03,23`
   record headers and zero payloads. The spec firmware's split where
   electrical/temperature data lives in 0x02 does not apply here.

2. **The on-device LCD exposes ~30 fields that the cloud channel never sees.**
   COMP. TOTAL RUN TIME, EXPANSION VALVE position, SUPPLY VOLTAGE, DC bus
   voltage, P1 pressure, T3/T4/TF temperatures, ODU/HMI/IDU software versions —
   none of these appear in either the 0x01 or 0x02 cloud responses on this
   firmware. They are either:
   - Read off the modbus/internal bus directly by the wired HMI, never pushed
     to the cloud relay, **or**
   - Reachable via a different subtype query that has not yet been discovered
     (subtype 0x10 returns error 1214; other subtypes untried).

3. **Setpoints and tank temperature are unconstrained, direct °C bytes.**
   Confirmed:
   - `status[16]` = active zone setpoint (matches MAIN SET TEMP)
   - `status[18]` = DHW setpoint (matches TANK SET TEMP)
   - `status[32]` = DHW tank temperature (matches T5 WATER TANK TEMP)

4. **Plate / water-circuit temperatures cluster around indices `[22], [25],
   [26], [28], [30]`** with both encoding styles in play (direct °C `0x19=25`,
   and raw-35 `0x3C=25`). All four labelled sensors (TW_O, TW_I, T2, T2B)
   were reading exactly 25°C at capture time, so we cannot yet distinguish
   which byte maps to which sensor — that requires capturing a state where the
   sensors disagree (e.g. during active flow with a temperature gradient).

5. **The "Th" / "Tp" naming clash is now resolved.** The device firmware
   calls the compressor *suction* pipe `Th` and the *discharge* pipe `Tp`.
   These are NOT the same as the upstream-doc `th_temp` (DHW tank, direct °C)
   and `tp_temp` (mislabelled as "plate exchanger" — it is in fact also
   discharge). See `API_FINDINGS.md` §6 for the correction.

## 9. Live Cross-Reference (2026-10-31, active Space+DHW heating + Fast DHW Boost)

Captured via `iletcomfort_client.py --api-base https://eu.dollin.net status` /
`sensors`, cross-referenced against phone photos of the physical HMI taken
within the same ~20-minute window (unit actively heating: Space Heating ON,
DHW Heating ON, Fast DHW Boost ON, Zone 1 = Heat/35°C, DHW target = 45°C).

Two live captures ~20-30 min apart, status body (`d=1` convention, matches
`API_FINDINGS.md` §17 / `decode_its_status`'s `body[d+N]`):

```
capture 1: 01 55 17 90 03 03 23 1e 2d 17 41 23 19 05 37 19 19 05 3c 22 3c 14 18 00 80
capture 2: 01 55 17 90 03 03 23 1e 2d 17 41 23 19 05 37 19 19 05 3c 22 3c 14 35 00 80
```

Only **one byte** (`d+21`, index 22 in the printed body) differed between the
two captures: `0x18` (24) → `0x35` (53). Every other byte — including the
`live_ops` bitfield (`0x55`), zone setpoint, room temp, and DHW setpoint —
stayed frozen across the ~20-30 min gap even though the compressor was
running the whole time.

Sensor frame (subtype 0x02) was **byte-identical** across both captures:
`02 00 03 29 14 1e 14 32 05 28 05 00 0a 00 03 23 00 00 00 00 03 23 00 00 00 00 03 00 00 00 00 00 03 00 00 00 00 00`.

### Findings

1. **Home-screen right-side value is the DHW tank's *actual* temperature, not
   its setpoint.** The HMI main screen showed `35°C` on the tap-icon side
   while `d+7` (decoded as `dhw_setpoint_v`) read `0x2d` = 45. These are
   different things: `d+7` is the DHW **target** (confirmed by the matching
   `TANK SET TEMP` field from the idle capture in §4, `status[18]=0x2d=45`),
   and the home screen's live `35` is the tank's **current** temperature —
   consistent with `DHW Heating: ON` (tank still climbing toward the 45°C
   target). `OPERATION PARAMETER` page 4/9 confirms this: `T5 WATER TANK
   TEMP` read `35°C` at the same moment, matching the home screen exactly.

2. **`d+21` (`water_outlet_temp` in the code, "probable") is a genuinely
   live-updating byte** — it moved from 24→53 while every other status byte
   stayed constant. However it does **not** match any single HMI value we
   captured at a comparable moment (`T5`=35, `TW_O`=48, `TW_I`=46 were the
   closest candidates on page 4/9, captured a few minutes before capture 2's
   `53`). Given finding #1, `d+21` is most likely a second, independent water
   temperature reading from a different point than `T5` — a good follow-up
   test is to capture `status` and photograph page 4/9 or 8/9 within seconds
   of each other, ideally during a period of large thermal gradient (e.g.
   right after the compressor starts from cold).

3. **Correction — bit4 (`0x10`) of the `live_ops` bitfield is NOT an active
   TANK BACKUP HEATER indicator**, despite `OPERATION PARAMETER` page 2/9
   showing `TANK BACKUP HEATER: ON` during this capture (which would have
   been a tempting match for the raw `0x55` = `0b01010101`, bits 0/2/4/6).
   This is ruled out by the **idle-state** capture in §2-§3 above: with the
   unit fully `OFF` (no heating, no DHW, no TBH, no fast-DHW), `live_ops` was
   `0x10` — i.e. bit4 was *already set* with everything else off, and
   `TANK BACKUP HEATER` read `OFF` on the HMI at that time. A bit that is set
   both when the backup heater is off (idle) and when it's on (this capture)
   cannot be tracking that heater. Bit4 remains unidentified; it looks like a
   static capability/config flag baked into this byte position rather than a
   live run-state bit for this installation.

4. **The rich OPERATION PARAMETER telemetry (pages 3, 6, 7, 8, 9) is
   confirmed absent from both cloud frames even during active compressor
   operation.** COMP. CURRENT (8A), COMP. FREQUENCY (29Hz), FAN SPEED
   (500 R/MIN), SUPPLY VOLTAGE (228V), DC GENERATRIX VOLTAGE/CURRENT
   (370V/1.10A), T2/T2B/T3/T4/TF temps, P1 PRESSURE (3040kPa), WATER FLOW
   (2.70 m³/h), POWER CONSUM, and HEAT PUMP CAPACITY were all live and
   changing on the HMI at capture time, yet neither the 25-byte status body
   nor the 37-byte sensor body (which didn't change a single byte across the
   whole window) contain plausible matches for any of them. This reinforces
   §6 finding #2: this telemetry needs a subtype we haven't found yet (0x10
   errors out; worth trying a broader subtype sweep, e.g. 0x03-0x0f).

## 7. Open Questions

- Which exact bit of `status[11]` corresponds to which pump (PUMP_O / _C / _S
  / _D) and which corresponds to PIPE vs TANK backup heater?
- Where do COMP. TOTAL RUN TIME, EXPANSION VALVE, SUPPLY VOLTAGE, DC bus
  voltage, P1 pressure live, if anywhere reachable from the cloud channel?
- What does `status[34]=0x80` represent? Constant high bit suggests a
  capability flag.
- Of the five 25°C-bearing bytes (`[22], [25], [26], [28], [30]`), which is
  TW_O vs TW_I vs T2 vs T2B?
- What does the `03,23` record-header pattern in the sensor frame signify?
  Looks like a TLV-style empty-record marker; the firmware may emit non-zero
  payloads only when specific subsystems are active.

## 8. Reproduction

```bash
# Login (EU region)
.venv/bin/python iletcomfort_client.py --api-base https://eu.dollin.net \
    login --account EMAIL --password PASSWORD

# Capture status (subtype 0x01)
.venv/bin/python iletcomfort_client.py --api-base https://eu.dollin.net \
    raw aa0cc30000000000000301012c

# Capture sensors (subtype 0x02)
.venv/bin/python iletcomfort_client.py --api-base https://eu.dollin.net \
    raw aa0cc30000000000000302012b
```

Compare the captured bytes against §5 above with the on-device
`MENU → SERVICE INFORMATION → PARAMETER DISPLAY` and `OPERATION PARAMETER`
pages 1/9 through 9/9 visible.
