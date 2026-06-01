# Chevrolet Bolt EUV — Non-ACC (lateral + alpha longitudinal)

Vehicle-specific notes, module inventory, and the on-car validation plan for the
`CHEVROLET_BOLT_NON_ACC_2ND_GEN` port.

- **Branch:** `bolt-euv-no-acc-long` (longitudinal alpha) / `bolt-euv-no-acc` (lateral-only fallback)
- **opendbc:** `photon-cat/opendbc` @ branch `bolt-euv-no-acc-long`
- **Reference VIN:** `1G1FY6S08N4120995` — 2022 Chevrolet Bolt EUV LT, **no ACC, no Super Cruise**

## openpilot integration summary

- Integrated at the **front view camera (B174W)** via the GM camera harness (`networkLocation = fwdCamera`).
- The car has **no K124 (ASCM)** — like every VOACC Bolt EUV. openpilot *impersonates* K124_ASCM on
  the bus (it is the transmitter node for `EBCMFrictionBrakeCmd`, `ASCMGasRegenCmd`, `ASCMLKASteeringCmd`).
- **Lateral (default):** `openpilotLongitudinalControl = False`, `pcmCruise = True`; engagement read from
  the stock non-adaptive cruise control (`ECMCruiseControl.CruiseActive`, msg 977 / 0x3D1, bit 39).
- **Longitudinal (alpha, opt-in, ALLOW_DEBUG build):** `openpilotLongitudinalControl = True`,
  `pcmCruise = False`; engage via cruise stalk buttons; sends the same gas/regen + friction brake
  commands as a camera-ACC Bolt EUV. Safety param `HW_CAM | HW_CAM_LONG | NON_ACC | EV`.

## Longitudinal command architecture (who talks to whom)

```
openpilot (as K124_ASCM)
   │  ASCMGasRegenCmd  0x2CB ─────────────► K20  ECM            (gas / regen)
   │  EBCMFrictionBrakeCmd 0x315 ─────────► K17  EBCM ─────────► K177 Brake Booster (builds pressure)
   │  ASCMLKASteeringCmd 0x180 ──────────► K43  PSCM           (steering)
   └─ all of the above traverse ─────────► K56  SDGM (gateway, routes/firewalls between buses)
```

- **There is no direct command to K177 (booster).** openpilot's only brake lever is `0x315` → **K17 (EBCM)**;
  K17 orchestrates K177. The DBC models no booster node.
- **K17 (EBCM) is the gating module** for braking. Its status `EBCMFrictionBrakeStatus` (562) carries
  `FrictionBrakeUnavailable`, which this port maps to `accFaulted` in longitudinal mode — i.e. a K17
  rejection surfaces as a fault rather than silence.
- **K56 (SDGM) is the gateway.** The Bolt EUV is treated as camera-integrated (not in `SDGM_CAR`), but the
  physical car still has K56 between bus segments. If commands don't reach K17/K20 on-car, suspect K56
  routing/firewalling first.

### Core hypothesis being tested
ACC vs non-ACC on the Bolt is a **K9 (BCM) configuration** difference, not different brake/gas actuator
firmware. If **K17/K177/K20 run the same software as an ACC Bolt EUV**, the `0x315` and `0x2CB` commands
should actuate identically. The CVN/software tables below are the reference to diff against a known-good
ACC Bolt EUV.

## Module software / calibration (reference VIN 1G1FY6S08N4120995)

### K56 — Serial Data Gateway Module, Processor 1
| Module Id | CVN | Selected Software |
|---|---|---|
| 00 | N/A | 13526568 |
| 01 | 49C1 | 13536793 |
| 02 | 8805 | 13518855 |
| 03 | 46B1 | 13536797 |
| 04 | AAA8 | 13530651 |
| 05 | 46DC | 13526562 |

### K56 — Serial Data Gateway Module, Processor 2
| Module Id | CVN | Selected Software |
|---|---|---|
| 00 | N/A | 13526569 |
| 01 | 07F2 | 13536794 |
| 02 | ADCD | 13518854 |
| 03 | 044B | 13536799 |
| 04 | 8B60 | 13530650 |

### B174W — Front View Camera (Windshield)
| Module Id | CVN | Selected Software |
|---|---|---|
| 00 | N/A | 23509153 |
| 01 | N/A | 42790953 |
| 02 | 0814 | 42688815 |

### K17 — Electronic Brake Control Module (EBCM) — gates `0x315`
| Module Id | CVN | Selected Software |
|---|---|---|
| 00 | N/A | 42793363 |
| 01 | N/A | 42781130 |
| 02 | N/A | 42693739 |
| 03 | N/A | 42774925 |
| 04 | N/A | 42708840 |
| 05 | N/A | 42774926 |

### K177 — Brake Booster Control Module (downstream of K17)
| Module Id | CVN | Selected Software |
|---|---|---|
| 00 | N/A | 42571218 |
| 01 | 1537 | 42754357 |
| 02 | 3904 | 42761398 |

> **TODO:** diff K17 / K177 / K20 software against a known-good ACC Bolt EUV. Match ⇒ strong evidence the
> command path is identical and only K9 (BCM) config differs.

## Module inventory (Bolt EUV catalog)

This is the full Bolt EUV module catalog. Items marked **absent** are not present on the reference no-ACC car.

| ECU | Name | Notes |
|---|---|---|
| K56 | Serial Data Gateway Module | **gateway — important for bus routing** |
| B174W | Front View Camera (Windshield) | openpilot integration point |
| K17 | Electronic Brake Control Module | gates friction brake `0x315` |
| K177 | Brake Booster Control Module | builds pressure, downstream of K17 |
| K20 | Engine Control Module | gas/regen `0x2CB` |
| K114A | Hybrid/EV Powertrain Control Module 1 | |
| K114B | Hybrid/EV Powertrain Control Module 2 | |
| K16 | Battery Energy Control Module | |
| K43 | Power Steering Control Module | LKAS `0x180` |
| K9 | Body Control Module | **ACC vs non-ACC config lives here** |
| K33 | HVAC Control Module | |
| K118 | Electric A/C Compressor Control Module | |
| K36 | Inflatable Restraint Sensing & Diagnostic Module | |
| K73 | Telematics Communication Interface Control Module | |
| K84 | Keyless Entry Control Module | |
| K132 | Pedestrian Alert Sound Control Module | |
| K157 | Video Processing Control Module | |
| K173 | Transmission Range Control Module | |
| K182 | Parking Assist Control Module | |
| K190 | Power Line Communication Module | |
| B218 | Side Object Sensor Module | blind spot |
| P16 | Instrument Cluster | |
| A11 | Radio | |
| Z1 | Immobilizer Learn | |
| Z4 | Vehicle-wide Capture of Module Identification Data | |
| **K124** | Active Safety Control Module (ASCM) | **ABSENT** — no VOACC Bolt EUV has it; openpilot impersonates it |
| **B233B** | Radar Sensor Module — Long Range | **ABSENT** on this car (ACC radar) |
| **K180** | Driver Monitoring System Module | **ABSENT** on this car (Super Cruise) |
| K179 | Automated Driving Mapping Module | likely absent (Super Cruise) — *verify* |

## On-car validation plan (joystick, brake-first)

Prereqs: ALLOW_DEBUG build flashed, fingerprints as `CHEVROLET_BOLT_NON_ACC_2ND_GEN`, "openpilot
longitudinal (alpha)" enabled, safetyParam = `HW_CAM | HW_CAM_LONG | NON_ACC | EV`,
`echo -n "1" > /data/params/d/JoystickDebugMode`.

0. **Engage, no input** (Park / wheels-up): press cruise SET, confirm engaged (`selfdriveState.enabled`,
   safety `controls_allowed`), no spurious `accFaulted`.
1. **Brake** (stationary, empty lot, foot over pedal — *do this first, it's the dangerous one*):
   joystick small negative accel. Confirm `EBCMFrictionBrakeCmd` (0x315) goes out **and** the brakes
   respond. Watch `EBCMFrictionBrakeStatus` (562) `FrictionBrakeUnavailable` — a K17 rejection shows up
   as `accFaulted`.
2. **Gas/regen** (very low speed): small positive accel. Does the car move (K20 accepts `0x2CB`)? Does the
   factory cruise fight? (The carcontroller does **not** auto-cancel stock cruise in long mode, so this is
   observable.)
3. **Full model:** only after 1 & 2 pass — drop joystick mode, try real openpilot longitudinal at low
   speed with a lead.

If commands don't reach K17/K20, suspect **K56 (SDGM)** routing. If K17 rejects `0x315`, that disproves the
"same actuator firmware" theory for braking and the fallback is re-porting the comma pedal for throttle
(braking still needs K17).
