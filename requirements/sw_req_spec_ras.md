# Software Requirement Specification (SRS)

## Rear Axle Steering Control Algorithm

**Requirement ID:** RAS-SW-REQ-0010

---

## 1. Introduction & Scope

The Electronic Chassis Control Unit shall implement a real-time software module commanding the rear axle steering actuator to a desired angle within ±5.0° mechanical limits. It must operate in all vehicle states—low-speed maneuvers (v < 60 km/h), high-speed lane changes (v > 65 km/h), parking, reverse, and emergency maneuvers—using both rule-based open-loop and yaw-rate-feedback closed-loop control to optimize handling, stability, and safety.

---

## 2. Description & Rationale

**2.1 Description**

* Real-time computation of δᵣ (rear-wheel angle) based on front-wheel steering δᶠ, vehicle speed v, yaw rate r, lateral accel aᵧ, driver\_mode, and gear\_state.
* Output δᵣ\_cmd broadcast on CAN ID 0x4B0 (0.01°/bit) every 5 ms.

**2.2 Rationale**

* **Low-speed maneuverability:** Counter-phase steering reduces turning radius.
* **High-speed stability:** Same-phase steering enhances yaw damping and lane-change control.
* **Safety:** Improves obstacle-avoidance path and transient response.
* **Driver confidence:** Provides predictable, seamless transitions without discontinuities.

---

## 3. System Architecture & Data Flow

```text
┌─────────────┐   front_wheel_angle │  
│ Sensors &   │ + vehicle_speed    ▼  
│ Vehicle     ├─────────────────▶ ┌──────────────┐ ──▶ δᵣ_cmd ──▶ Steering Actuator  
│ State       │   yaw_rate         │ RAS Control │    CAN (5 ms)   Driver  
└─────────────┘   lateral_accel    │ Algorithm   │                  │  
         ▲                         └──────────────┘                 │  
         │ driver_mode, gear_state                                └─┘  
```

**Inputs:**

* δᶠ (front steer), v (vehicle speed), r (yaw rate), aᵧ (lateral accel), driver\_mode, gear\_state

**Control Core:**

1. Phase Law (rule-based)
2. PI Yaw Trim (closed-loop)
3. Hysteresis Compensation
4. Slew-Rate & Limit Enforcement

**Outputs:**

* δᵣ\_cmd on CAN ID 0x4B0 every 5 ms

---

## 4. Functional Requirements

### 4.1 Open-Loop Phase-Based Steering

**4.1.1 Opposite-Phase Region (v < 60 km/h)**

* δᵣₒₚₚ = –K₁·δᶠ
* K₁ tapers linearly from K₁ₘₐₓ at 0 km/h to K₁ₘᵢₙ at 60 km/h (configurable via UDS).

**4.1.2 Transition Blend (55 ≤ v ≤ 65 km/h)**

* Weight:
  $w(v)=\frac{1}{1+e^{-α\,(v–v₀)}},\;α=0.23,\;v₀=60$
* δᵣ\_phase = (1–w)·δᵣₒₚₚ + w·δᵣₛₐₘₑ

**4.1.3 Same-Phase Region (v > 65 km/h)**

* δᵣₛₐₘₑ = +K₂·δᶠ
* K₂ scheduled vs. v to limit sensitivity at very high speeds.

---

### 4.2 Closed-Loop Yaw-Rate Feedback

**4.2.1 Yaw-Rate Error**

* r\_error = r\_meas – r\_model(v,δᶠ)
* r\_model = (v / l)·tan(δᶠ)·cos(λ) (bicycle model; λ = roll-center offset)

**4.2.2 PI Controller**

* Δδᵣ = Kp(v)·r\_error + Ki(v)·∫r\_error dt
* Kp, Ki gain-scheduled vs. v and driver\_mode; update ≥ 200 Hz; anti-windup via back-calculation.

---

### 4.3 Actuator Conditioning

**4.3.1 Slew-Rate Limit**

* |dδᵣ/dt| ≤ 30°/s enforced before CAN broadcast.

**4.3.2 Hysteresis Compensation**

* δ\_offset = H(δ\_cmd\_last, δ\_meas) (≤ 0.3°)
* δᵣ\_cmd\_adj = δᵣ\_cmd + δ\_offset

---

### 4.4 Accuracy & Fail-Safe

**4.4.1 Steady-State Tolerance**

* |δᵣ\_target – δᵣ\_meas| ≤ 0.2° for v ∈ \[5, 180] km/h on flat road.

**4.4.2 Input Invalidity**

* On invalid δᶠ, v, r, aᵧ, or gear\_state:

  * Command δᵣ = 0° within 100 ms
  * Set diagnostic flag RAS\_FAIL\_SAFE\_ACTIVE

---

## 5. Interface Requirements

| ID  | Specification                                                                                                                                      |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5.1 | Broadcast δᵣ\_cmd on CAN ID `0x4B0` every 5 ms, scaled 0.01°/bit, offset 0°.                                                                       |
| 5.2 | Required inputs: `front_wheel_angle` (≤ 0.05°), `vehicle_speed` (≤ 0.1 km/h), `yaw_rate` (≤ 0.2°/s), `lateral_accel`, `driver_mode`, `gear_state`. |
| 5.3 | Calibration via UDS `0x2F` (WriteDataByIdentifier), DataIDs `0xF210–0xF21F`.                                                                       |
| 5.4 | Diagnostic routine via UDS `0x31` sub-function `0x0203` to slew actuator ±3° and verify feedback.                                                  |

---

## 6. Diagnostics

| ID  | Specification                                                                                                  |                     |                                                                                   |
| --- | -------------------------------------------------------------------------------------------------------------- | ------------------- | --------------------------------------------------------------------------------- |
| 6.1 | If                                                                                                             | δ\_target – δ\_meas | > 0.5° for > 50 ms, store DTC `C1A27` and enter degraded mode (δ limited to ±1°). |
| 6.2 | Supervise control task with 100 ms watchdog; timeout raises `RAS_TIMEOUT_ERROR` and recenters actuator.        |                     |                                                                                   |
| 6.3 | On power loss or ECU reset, actuator must return to 0° (mechanical centre) via spring preload within ≤ 300 ms. |                     |                                                                                   |
| 6.4 | Support freeze-frame capture (timestamp, v, angles, r) at fault for post-analysis.                             |                     |                                                                                   |
