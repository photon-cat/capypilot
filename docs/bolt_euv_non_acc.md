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

## CAN bus topology — DIFFERS from the ACC car (verify before any on-car test)

openpilot's GM camera integration is written for the **ACC Bolt EUV** layout:

- Buses: `POWERTRAIN = 0`, `OBSTACLE = 1`, `CAMERA = CHASSIS = 2`.
- Splices at the camera with a **relay** separating the camera (bus 2) from the car (bus 0); blocks the
  camera's `0x184`/LKAS and injects openpilot's own.
- **All actuation goes out bus 0** (`0x180` steer, `0x2CB` gas, `0x315` brake); camera-side `0x184`/`0x1E1`
  go out bus 2. The safety even asserts a relay (`0x180` on bus 0, `0x184` on bus 2).

**Observed wiring difference (this is the "weird" part):**

| | ACC car | Non-ACC car (this car) |
|---|---|---|
| Camera / ADAS path | routes **through the gateway (K56)** — standard 2-bus relay model | camera (B174W) sits **directly on the powertrain CAN bus** |

Implications:
- **Possibly simpler:** openpilot's commands on the powertrain bus reach K17/K20 **directly**, with no
  gateway hop — good for the brake/gas experiment.
- **But the stock bus/relay config may not match:** which bus carries `0x184` vs `0x180`, the forwarding,
  and the relay-malfunction check all assume the ACC car's gateway-separated layout. If the camera is on the
  powertrain bus here, the bus numbering the harness presents could differ — and that affects **even
  lateral** (steering injected on the wrong bus won't work / trips relay malfunction).

**Verify with a CAN dump on this car (all buses) before flashing** — confirm where each lands:

| Message | Expected (ACC model) | Confirms |
|---|---|---|
| `0x184` PSCMStatus / LKAS (from camera) | bus 2 | camera side / relay point |
| `0x180` ASCMLKASteeringCmd | bus 0 | steering target bus |
| `0x3D1` (977) ECMCruiseControl | bus 0 (pt) | cruise-engage read |
| `0x315` EBCMFrictionBrake (cmd/status) | bus 0 (pt) | **brake target bus** |
| `0x2CB` ASCMGasRegenCmd | bus 0 (pt) | gas target bus |

If the camera appears on the powertrain bus instead of a separate bus 2, the harness install point and the
`networkLocation` / `disable_forwarding` / bus assignments likely need adjusting for this car.

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
ACC vs non-ACC on the Bolt was assumed to be a **K9 (BCM) configuration** difference, not different
brake/gas actuator firmware. The module diff below (non-ACC vs a known ACC Bolt EUV) **partially refines
this**: the gateway, camera, and brake booster are byte-identical, but the EBCM (K17), which is the module
that accepts `EBCMFrictionBrakeCmd` (0x315), is flashed differently. So brake acceptance is **test-gated,
not assured** — the command *interface* may still be identical despite a different calibration, but it must
be proven on-car (Phase 1).

## Module software comparison: non-ACC vs ACC

- **Non-ACC reference:** `1G1FY6S08N4120995` — 2022 Bolt EUV LT, no ACC
- **ACC reference:** `1G1FZ6S04N4126158` — 2022 Bolt EUV Premier, ACC w/ stop-go (RPO `KSG`)

### Identical across both cars ✅
These modules carry the same Selected Software on both the non-ACC and ACC car:

- **K56 — Serial Data Gateway (gateway):**
  - P1: `13526568 / 13536793 / 13518855 / 13536797 / 13530651 / 13526562`
  - P2: `13526569 / 13536794 / 13518854 / 13536799 / 13530650`
- **B174W — Front View Camera:** `23509153 / 42790953 / 42688815`
- **K177 — Brake Booster Control Module:** `42571218 / 42754357 / 42761398`

→ Routing (K56), camera integration (B174W), and brake execution (K177) are the same on both cars.

### K17 — Electronic Brake Control Module (EBCM) — DIFFERS ❌
This is the module that gates `EBCMFrictionBrakeCmd` (0x315). All six partitions differ:

| Module Id | non-ACC `…120995` | ACC `…126158` | Match |
|---|---|---|---|
| 00 | 42793363 | 42793362 | ✗ (off by 1) |
| 01 | 42781130 | 42693738 | ✗ |
| 02 | 42693739 | 42799962 | ✗ |
| 03 | 42774925 | 42693744 | ✗ |
| 04 | 42708840 | 42693746 | ✗ |
| 05 | 42774926 | 42740374 | ✗ |

→ The EBCM is **not** the same flash. Whether the non-ACC K17 still accepts the ASCM friction-brake
command is the central on-car question. Module 00 differs by a single digit (likely calibration ID); the
rest diverge more.

> **TODO:** also pull **K20 (ECM)** software on both cars to compare the gas/regen (`0x2CB`) path — not yet
> captured. And, if K17 rejects `0x315` on-car, the ACC K17 part numbers above document what a (heavy,
> brake-module) reflash target would be — investigate feasibility/risk before ever attempting.

## ACC car ADAS build (RPO) — for reference

Selected active-safety / brake RPOs from the ACC reference `1G1FZ6S04N4126158` (the non-ACC car lacks the
ACC-specific ones — no `KSG`, no following-distance/radar):

| RPO | Meaning |
|---|---|
| `KSG` | Cruise control automatic, **adaptive, with stop/go** (the ACC) — *absent on non-ACC car* |
| `UE4` | Sensor indicator following distance — *absent on non-ACC car* |
| `UEU` | Forward collision alert |
| `UHX` | Lane keep assist (LKAS) |
| `UHY` | Low-speed collision imminent braking / **integrated brake assist** |
| `UKC` | Side active safety obstacle detection (enhanced) |
| `UKJ` | Pedestrian detection (front) |
| `UFG` | Rear cross traffic alert |
| `UV2` | 360 vision |
| `UD7` | Park assist rear |
| `J67` | Brake system power, frt & rr disc, ABS |
| `J71` | Brake parking, power operated |
| `JBJ` | Booster brake — none (no vacuum booster; electric/iBooster) |
| `EPH` | Trans range selection, electronic |
| `HPB` | Electrified propulsion, BEV, Gen 2, FWD |

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

Interpreting Phase 1:
- **K56 (SDGM) is byte-identical to the ACC car**, so gateway routing is the *same* — a "command didn't
  reach K17" failure is therefore less likely to be a gateway/firewall difference than a K17 difference.
- **K17 (EBCM) is flashed differently** from the ACC car, so it is the prime suspect if `0x315` is ignored
  or `FrictionBrakeUnavailable` asserts. That outcome would mean the non-ACC EBCM firmware doesn't accept
  the ASCM friction-brake command — i.e. braking is not available without (heavy, risky) K17 reflash.
- **K177 (booster) is identical**, so if K17 *does* accept the command, pressure execution should match the
  ACC car.

If K17 rejects `0x315`, the throttle side may still be salvageable independently (re-port the comma pedal
for gas), but adaptive braking would be blocked until the K17 question is resolved.
