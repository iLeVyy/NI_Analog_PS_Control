# Project Plan – PS Control (NI DAQmx Analog-Output Power Supply Controller)

| Item | Value |
|---|---|
| Document | Project_Plan.md |
| Project name | PS Control (working title) |
| Version | 1.4 (Approved) |
| Author | Erik Evangelio – Testing Technology |
| Status | Approved – Phases 1–5 delivered (app v0.1.0 – v0.1.4) |
| Language / Stack | Python 3, PySide6 (Qt), nidaqmx, Nuitka |

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Requirements](#2-requirements)
3. [Technology Stack](#3-technology-stack)
4. [Architecture](#4-architecture)
5. [Folder Structure](#5-folder-structure)
6. [Main Panel (Power Supply UI)](#6-main-panel-power-supply-ui)
7. [Settings Window](#7-settings-window)
8. [Scaling Concept](#8-scaling-concept)
9. [Configuration File](#9-configuration-file)
10. [Sequence Window](#10-sequence-window)
11. [Sequence File Format](#11-sequence-file-format)
12. [DAQ Service Behavior](#12-daq-service-behavior)
13. [Safety and Error Handling](#13-safety-and-error-handling)
14. [Logging](#14-logging)
15. [Build and Deployment (Nuitka)](#15-build-and-deployment-nuitka)
16. [Testing Strategy](#16-testing-strategy)
17. [Implementation Phases and Milestones](#17-implementation-phases-and-milestones)
18. [Deliverables](#18-deliverables)
19. [Risks and Mitigations](#19-risks-and-mitigations)
20. [Decisions](#20-decisions)
21. [Future Enhancements](#21-future-enhancements)
22. [Development Rules](#22-development-rules)
23. [Revision History](#23-revision-history)

---

## 1. Purpose and Scope

### 1.1 Purpose
Provide a standalone Windows desktop application that controls a programmable power supply through its analog control inputs, using NI analog-output (AO) hardware driven by the NI-DAQmx API from Python.

### 1.2 In Scope
- Bench-power-supply-style operator UI for voltage and current setpoints.
- Settings window for NI device scanning, AO channel assignment, range configuration, and automatic linear scaling.
- Persistent configuration in JSON under a relative `config/` folder.
- Time-based sequence editor and runner (software-timed, see D-01).
- Standalone build with Nuitka, user manual, and handoff document.

### 1.3 Out of Scope (v1.0)
- Remote control interfaces (SCPI, TCP, CAN).
- Measurement readback of actual voltage/current (decided D-04; planned as future enhancement FE-02, see section 21).
- Multi-power-supply control (more than one voltage/current pair).
- Hardware-timed sequence playback (planned as future enhancement, see section 21).

---

## 2. Requirements

### 2.1 Functional Requirements

| ID | Requirement |
|---|---|
| FR-01 | The UI provides a power-supply-like front panel with voltage and current setpoint controls (knob, numeric entry, step buttons). |
| FR-02 | The UI provides an Output ON/OFF control. AO channels output setpoint values only while Output is ON. |
| FR-03 | A Settings button opens the Settings window. |
| FR-04 | The Settings window scans all available NI devices and lists AO-capable physical channels. |
| FR-05 | The user assigns one AO channel to voltage control and one AO channel to current control. |
| FR-06 | The available AO output ranges are read from the device using DAQmx device properties. |
| FR-07 | The user enters the power supply range for voltage and current (e.g., 0–20 V, 0–100 A). |
| FR-08 | The tool calculates slope and offset automatically from the power supply range and the selected AO range. |
| FR-09 | Settings are saved as JSON in `<app root>/config/settings.json` and loaded automatically at startup. |
| FR-10 | A Sequence window allows creating, editing, saving, loading, and running time-based setpoint sequences. |
| FR-11 | Sequence step durations support ms, s, and min units. |
| FR-12 | Sequences support a Stop action that ends execution. |
| FR-13 | Manual front-panel control is locked while a sequence is running. |
| FR-14 | Sequences are executed with software timing using an absolute schedule; the minimum step duration is 50 ms. |
| FR-15 | The actual execution timestamp of each sequence step is logged for comparison against the planned schedule. |
| FR-16 | Sequence steps use "set, then hold" semantics. Each step's action is **Set** (jump) or **Ramp** (linear transition from the previous value over the step duration), plus a final **Stop**. |
| FR-17 | Each sequence step has a mandatory voltage value and an optional current value. An empty current cell means "current limit unchanged". |
| FR-18 | All user-entered values (front panel and sequences) are in **power supply engineering units** (V, A) within the power supply range. The tool converts them to AO voltage using the configured scaling; the user never enters AO values. |
| FR-19 | Each sequence defines its end state for a normal end (Stop step): **Output OFF + safe value** (default) or **Hold last value**. |
| FR-20 | A user abort (Stop button) always applies Output OFF + safe value, regardless of the sequence end state. An error always applies the safe state. |
| FR-21 | When a sequence is set to "Hold last value", the Sequence window shows a visible warning that power remains on after the sequence ends. |
| FR-22 | For each control channel (voltage, current), the user selects the device AO range (from DAQmx) and the **used AO span** (AO min/max). Presets: 0–5 V, 0–10 V (default), Custom. |
| FR-23 | For each channel, the user enters both **PS min** and **PS max** (PS min defaults to 0). Front-panel controls and sequence values are limited to PS min…PS max. |
| FR-24 | Front-panel setpoint changes jump by default. An optional **slew-rate limit** (separate V/s and A/s, off by default; default rates **1 V/s** and **5 A/s**) makes front-panel changes ramp in ≈ 50 ms increments. |
| FR-25 | Typed values are applied only on Enter or focus loss; Output ON starts from the safe value toward the setpoint (jump or rate-limited ramp). |
| FR-26 | Each sequence has a **repeat count**: 1 = run once (default), N = run N times, 0 = run until stopped. |

### 2.2 Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | UI remains responsive at all times (hardware access and sequence execution run outside the UI thread). |
| NFR-02 | All file paths are relative to the application root folder. |
| NFR-03 | Application is deployable as a Nuitka standalone build without a Python installation on the target PC. |
| NFR-04 | Dark industrial theme (dark blue-gray, blue accent) with optional light theme. |
| NFR-05 | Layout usable on lower-resolution screens (resizable windows, scroll areas where needed, centered startup). |
| NFR-06 | Outputs default to a safe state on startup, exit, and error. |
| NFR-07 | Code is modular with a clear separation of UI, hardware, configuration, and sequencing. |
| NFR-08 | Sequence step timing accuracy: typically ±5–15 ms per step on Windows, with no cumulative drift over the sequence. |
| NFR-09 | The sequence engine is designed so a hardware-timed player can be added later without changing the editor, file format, or engine interface. |

---

## 3. Technology Stack

| Area | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ (version to be pinned) | Supported by nidaqmx, PySide6, and Nuitka. |
| UI framework | PySide6 (Qt 6) | Same as DataLab Next; official Qt for Python. |
| UI design | Qt Designer `.ui` files, loaded at run time with `QUiLoader` | Visual "front panel" editing, similar to LabVIEW; layout changes need no code change or conversion step. Required widget names are listed in a spec module per window and checked by tests. |
| Controls | `QDial`, `QDoubleSpinBox`, custom 7-segment display widget, `QTableWidget` | Bench-supply appearance and sequence table. |
| Hardware API | `nidaqmx` (NI official Python package) | Same NI-DAQmx driver as LabVIEW. |
| Configuration | JSON | Human-readable, versionable. |
| Testing | pytest | Unit tests for scaling, config, and sequence logic. |
| Packaging | Nuitka (standalone mode) | Native compiled EXE, faster startup, better code protection. |
| Version control | Git | Standard workflow. |

---

## 4. Architecture

### 4.1 Principles
- **Single responsibility per module** (DQMH-style).
- **Only the DAQ service talks to DAQmx.** UI never calls the driver directly.
- **Communication through Qt signals/slots** (equivalent to DQMH requests and broadcasts).
- **Long-running work in worker threads** (equivalent to parallel helper loops).

### 4.2 Module Overview

| Module | Responsibility | LabVIEW / DQMH Analogy |
|---|---|---|
| `main.py` | Entry point: resolve app root, load config, create services and windows | Launcher / Main.vi |
| `ui/main_panel` | Power supply front panel (main window) | Main UI module |
| `core/front_panel` | Qt-free front-panel logic: ranges, step sizes, knob mapping, display format, slew ramp | Front-panel sub-VIs |
| `ui/settings_window` | Device scan, channel assignment, ranges, scaling preview | Settings dialog |
| `core/settings_editor` | Qt-free logic of the Settings window (working copy, presets, preview, validation, dirty check) | Dialog data cluster + checks |
| `ui/daq_bridge` | Delivers DAQ events / command results to the UI thread | User event from helper loop |
| `core/bootstrap` | Common start-up for GUI and console mode | Launcher init |
| `ui/sequence_window` | Sequence editor and run monitor | Sequence module UI |
| `ui/widgets` | Custom widgets (7-segment display, knob with fine/coarse) | Custom controls (.ctl) |
| `core/daq_service` | Device scan, AO task creation, write, safe state, close; worker thread + command queue; events to listeners | DQMH module (requests / broadcasts) |
| `core/daq_backend` | Backend interface (`DaqBackend`), device/driver data types, backend factory driven by `config/presets/daq_backends.json` | HAL parent class |
| `core/nidaqmx_backend` | Real NI-DAQmx backend (lazy `nidaqmx` import) | DAQmx HAL child class |
| `core/scaling` | Slope/offset calculation, conversion, clamping | Linear custom scale |
| `core/config_manager` | Load, validate, default, save JSON | Config class / FGV |
| `core/sequence_engine` | Validate sequence, build execution schedule, AO preview, `SequenceRunner` (thread, pause/resume/stop, end states, timing log, status snapshot) | Sequencer module |
| `core/sequence_model` | Sequence data model and JSON file format | Typedef + flatten/unflatten |
| `core/sequence_editor` | Qt-free editing logic of the Sequence window | Editor data cluster |
| `core/sequence_players` | `Clock`, `RunControl`, `SequencePlayer` interface, `SoftwarePlayer` (v1.0); `HardwarePlayer` reserved for FE-01 | Parent class + child classes (dynamic dispatch) |
| `core/models` | Data classes for channel config, PS config, sequence steps | Typedefs / class private data |
| `core/app_paths` | Resolve app root and relative folders (dev and Nuitka build) | Application directory VI |
| `core/logger` | Application logging | Error/log module |
| `core/simulation` | Simulated DAQ backend; devices read from `config/simulation/devices.json` | Simulated HAL child class |

### 4.3 Communication Flow

```
Main Panel ──(set voltage/current, output on/off)──► DAQ Service ──► NI AO channels
     ▲                                                   │
     └──────────────(status, errors, AO value)───────────┘

Settings Window ──(scan request)──► DAQ Service ──(device list)──► Settings Window
Settings Window ──(save)──► Config Manager ──(config changed)──► DAQ Service (reconfigure)

Sequence Window ──(start/pause/stop)──► Sequence Engine ──(schedule)──► Player ──(setpoints)──► DAQ Service
Player ──(step index, progress, finished)──► Sequence Window / Main Panel
```

### 4.4 Threading Model

| Thread | Work |
|---|---|
| UI (main) thread | All widgets, user input, display updates |
| DAQ worker thread | DAQmx task handling and writes (serialized command queue) |
| Sequence worker thread | Software player: step timing; sends setpoints to the DAQ worker (waits for each write result) |
| UI monitor | Sequence window polls the runner status every `status_poll_ms` (QTimer) |

---

## 5. Folder Structure

```
PS_Control/
├─ main.py
├─ version.txt                 (single source of the app version)
├─ requirements.txt
├─ setup_venv.bat / run_app.bat / run_tests.bat
├─ HANDOFF.md                  (developer handoff, updated every release)
├─ PROJECT_OVERVIEW.txt        (user guide incl. button reference)
├─ ui/                         (.ui Designer files + window classes, Phase 3+)
├─ core/                       (non-UI logic)
├─ config/
│  ├─ settings.json            (created automatically on first start)
│  ├─ defaults/settings.default.json   (default values - data, not code)
│  ├─ presets/*.json           (config-driven catalogs: AO spans, step sizes,
│  │                            duration units, end states, sequence actions,
│  │                            scaling preview points, DAQ backends)
│  └─ simulation/devices.json  (simulated AO devices)
├─ sequences/                  (user sequence files, *.json)
├─ resources/
│  ├─ icons/
│  └─ themes/<id>/theme.json + style.qss   (one folder per theme)
├─ logs/
├─ tests/
├─ release_notes/RELEASE_NOTES_v<x.y.z>.txt
├─ docs/Project_Plan.md
├─ build_nuitka.bat             (builds the standalone package)
└─ build/dist/PS_Control/       (generated package; contains PS_Control.exe)
```

---

## 6. Main Panel (Power Supply UI)

### 6.1 Layout

| Area | Elements |
|---|---|
| Header | App name, version, connection indicator |
| Voltage section (left) | Large 7-segment setpoint display (V), rotary knob, numeric entry, ▲/▼ step buttons, step-size selector (e.g., 0.01 / 0.1 / 1 V), max range label |
| Current section (right) | Same as voltage section, in A |
| Output section | Large Output ON/OFF button with LED indicator |
| Toolbar | ⚙ Settings, Sequence (enabled in Phase 5), Exit |
| Status bar | Device/channel names, actual AO voltage sent (V and I channel), sequence state, last message (warning / error colored) |
| Sent value | Per section: PS value written last (differs from the setpoint during a ramp) |
| Reserved area (FE-02) | Layout space reserved below each setpoint display for a future "Measured" value; hidden in v1.0 |

### 6.2 Behavior
- Knob: coarse adjustment by rotation, fine adjustment via step buttons or modifier key.
- Setpoints are clamped to the configured power supply range (PS min…PS max); knob and numeric entry limits follow PS min/max.
- Setpoint changes are written immediately when Output is ON; when OFF, they are stored and applied when Output is switched ON.
- **Jump / slew-rate limit (Decision D-07):**
  - Default: changes jump directly to the new value.
  - With "Limit slew rate" enabled: changes ramp at the configured rate (defaults: **1 V/s** voltage, **5 A/s** current) in ≈ 50 ms increments.
  - Typed values are applied only on **Enter** or focus loss, not on every keystroke.
  - **Output ON** starts from the safe value and moves to the setpoint (jump, or ramp if the limit is enabled).
  - During a ramp, the display shows the target value; the status bar shows the actual value being sent.
  - A new setpoint during a running ramp retargets the ramp from the current actual value.
  - Output OFF during a ramp cancels it and applies the safe value immediately (no ramp-down).
- Sequences ignore the front-panel slew-rate limit and use their own Set (jump) / Ramp steps.
- Controls are disabled when configuration is invalid or no device is available.
- Controls are locked while a sequence is running; the displays follow the sequence values.
- Knob: one position = one step; Page Up/Down = 1/20 of the range; notches hidden for very fine knobs (> 400 positions).
- Start-up: AO channels are set up automatically; first start opens Settings; missing device offers Settings (9.3).
- Exit asks for confirmation while the output is ON; theme, step sizes and window geometry are saved on exit.

---

## 7. Settings Window

### 7.1 Sections

| Section | Elements |
|---|---|
| Device scan | Scan button, device list (name, product type, serial), AO channel list per device |
| Voltage control channel | AO channel selector, device AO range (from DAQmx, read-only/selectable), used AO span preset (0–5 V / **0–10 V default** / Custom) with AO min/max fields, PS min (default 0) / PS max (V) |
| Current control channel | Same as voltage, independent span setting, PS min/max (A) |
| Scaling preview (read-only) | Slope, offset, example table (0 %, 25 %, 50 %, 75 %, 100 %) |
| Safety | Safe value at startup/exit; "Limit slew rate" checkbox (off by default) with voltage rate [V/s] (default 1 V/s) and current rate [A/s] (default 5 A/s); rates must be > 0 |
| Test | ~~"Write test value" button per channel (optional)~~ – closed in v0.1.3: Output ON on the front panel provides the same check |
| Buttons | Save, Cancel, Restore Defaults |
| Backend | Backend selector (Auto / NI-DAQmx / Simulation from `daq_backends.json`; applies at next start) and SIMULATION indicator |
| Messages | Live validation below the form (errors / warnings / OK, colors from the theme) and the result of the last action |

### 7.2 Device Scan
- Read the DAQmx system device list.
- For each device, read product type, serial number, AO physical channels, and supported AO voltage ranges.
- Present only devices with AO channels.
- Mark the currently configured channels; warn if a saved channel is no longer present.
- Reserved for future (section 21): read whether the device supports hardware-timed AO (sample clock) so the hybrid timing option can be offered only on compatible hardware.

### 7.3 Validation on Save
- Voltage and current channels are assigned and not identical.
- PS max > PS min for both channels (PS min defaults to 0; non-zero and offset mappings allowed).
- Info note in the scaling preview when PS min ≠ 0, showing the resulting offset.
- Selected device AO range is supported by the device.
- Used AO span lies within the device AO range.
- AO max > AO min.
- Warning (not blocking) if the used span includes negative voltages.
- Both channels must be assigned to save. Hardware is checked against the last scan (device present, channel on device, span inside a device range).
- On success: write JSON, notify DAQ service to recreate AO tasks (asynchronous; result shown in the message area). The window stays open.
- Cancel, Esc and closing the window ask before discarding unsaved changes.

---

## 8. Scaling Concept

### 8.1 Formulas

$$\text{slope} = \frac{AO_{max} - AO_{min}}{PS_{max} - PS_{min}}$$

$$\text{offset} = AO_{min} - \text{slope} \cdot PS_{min}$$

$$AO_{out} = \text{clamp}(\text{slope} \cdot PS_{set} + \text{offset},\ AO_{min},\ AO_{max})$$

Inverse (for display of actual AO value as PS value):

$$PS = \frac{AO - \text{offset}}{\text{slope}}$$

### 8.2 Examples

| Channel | PS range | AO range | Slope | Offset | Setpoint | AO out |
|---|---|---|---|---|---|---|
| Voltage | 0–20 V | 0–10 V | 0.5 | 0 | 10 V | 5 V |
| Current | 0–100 A | 0–10 V | 0.1 | 0 | 50 A | 5 V |
| Voltage (non-zero min) | 2–20 V | 0–10 V | 0.5556 | −1.1111 | 2 V / 11 V / 20 V | 0 V / 5 V / 10 V |

### 8.3 Rules
- The user always works in power supply units (V, A) on the front panel and in sequences. AO voltage is an internal value only, visible in the status bar and the optional sequence preview.
- Conversion PS → AO is done in exactly one place (`core/scaling`, used by the DAQ service).
- Slope and offset are recalculated whenever ranges change and on every startup (stored values are informational).
- **Used AO span (Decision D-05):** the scaling always uses the user-defined span (default **0–10 V**), never the full device range automatically. Example: NI 9263 device range ±10 V, used span 0–10 V → PS 0 V = AO 0 V.
- The DAQmx task min/max is set to the used span, so the driver rejects out-of-range values as an additional safety layer on top of clamping.
- Voltage and current channels have independent spans.

---

## 9. Configuration File

**Location:** `<app root>/config/settings.json`

### 9.1 Content

| Group | Fields |
|---|---|
| meta | config_version, app_version, last_saved |
| voltage_channel | device, physical_channel, device_ao_min, device_ao_max, ao_span_preset, ao_min, ao_max, ps_min, ps_max, unit, slope, offset |
| current_channel | same as voltage_channel |
| safety | safe_value_startup, safe_value_exit, slew_limit_enabled (default false), slew_rate_voltage [V/s] (default 1.0), slew_rate_current [A/s] (default 5.0), slew_interval_ms (default 50, min 10) |
| sequence | min_step_duration_ms (default 50), timing_mode (`"software"` in v1.0), ramp_interval_ms (50), spin_wait_ms (2), new_step_duration (1.0), status_poll_ms (100) |
| daq | backend (`auto` / `nidaqmx` / `simulation`, default `auto`), min_driver_version (`""` = no check), write_timeout_s (default 2.0) |
| logging | retention_days (default 30), level (default INFO) |
| ui | theme, voltage_step, current_step, window_geometry, last_sequence_file |

### 9.2 Illustrative Structure

```json
{
  "meta": { "config_version": 1, "app_version": "1.0.0", "last_saved": "2026-11-05T12:00:00" },
  "voltage_channel": {
    "device": "cDAQ1Mod1", "physical_channel": "cDAQ1Mod1/ao0",
    "ao_min": 0.0, "ao_max": 10.0, "ps_min": 0.0, "ps_max": 20.0,
    "unit": "V", "slope": 0.5, "offset": 0.0
  },
  "current_channel": {
    "device": "cDAQ1Mod1", "physical_channel": "cDAQ1Mod1/ao1",
    "ao_min": 0.0, "ao_max": 10.0, "ps_min": 0.0, "ps_max": 100.0,
    "unit": "A", "slope": 0.1, "offset": 0.0
  },
  "safety": { "safe_value_startup": 0.0, "safe_value_exit": 0.0, "slew_limit_enabled": false, "slew_rate_voltage": 1.0, "slew_rate_current": 5.0 },
  "sequence": { "min_step_duration_ms": 50, "timing_mode": "software" },
  "ui": { "theme": "dark", "voltage_step": 0.1, "current_step": 0.1, "window_geometry": null, "last_sequence_file": "" }
}
```

### 9.3 Startup Logic

| Condition | Action |
|---|---|
| File missing | Create defaults, open Settings window |
| File invalid / unreadable | Back up as `settings.json.bak`, load defaults, warn user |
| Configured device not present | Warn, disable Output, offer to open Settings |
| Older config_version | Migrate fields, save new version |

---

## 10. Sequence Window

### 10.1 Layout

| Area | Elements |
|---|---|
| Toolbar | New, Load, Save, Save As; file name with `*` for unsaved changes; last file reopened (`ui.last_sequence_file`) |
| Options | Name, description, end state (with warning), repeat count, "Show AO preview" |
| Table | Step #, Duration, Unit (ms/s/min), Voltage, Current (optional), Action (Set / Ramp / Stop) |
| Row controls | Add, Insert, Delete, Duplicate, Move Up, Move Down |
| Run controls | Start, Pause/Resume, Stop (abort) |
| End state | Selector: Output OFF + safe value (default) / Hold last value; orange warning label when Hold last value is selected |
| Repeat | "Repeat count" field (1 = once, default; N = N times; 0 = until stopped) |
| Run monitor | Current step highlight, cycle counter (e.g., "Cycle 12 / 500" or "Cycle 12 / ∞"), step elapsed/remaining, total elapsed/remaining (elapsed only when repeat = 0), progress bar |

### 10.2 Step Semantics (Decision D-02)

| Action | Behavior |
|---|---|
| **Set** | Apply the setpoint at the start of the step, then hold it for the step duration. |
| **Ramp** | Move linearly from the previous value to the target over the step duration. Software ramp is applied in increments of approx. 50 ms (aligned with the minimum step time). Ramp start value of the first step = value active at sequence start. |
| **Stop** | End the sequence normally and apply the end state defined in the sequence (see 10.5). |

**Current column (optional):** voltage is mandatory in every Set/Ramp step; current is optional. An empty current cell keeps the current limit unchanged (front-panel value or last value set by the sequence). If a Ramp step has a current value, voltage and current ramp simultaneously.

**Engineering units:** all values in the table are entered in power supply units (V, A) – never in AO volts. Column headers show the unit and the valid range from settings, e.g. `Voltage [V] (0–20)` and `Current [A] (0–100)`.

Example from requirement (PS range 0–20 V / 0–100 A, AO range 0–10 V):

| Step | Duration | Voltage [V] | Current [A] | Action | AO sent (V ch.) | AO sent (I ch.) |
|---|---|---|---|---|---|---|
| 1 | 10 s | 10 | 50 | Set | 5.00 V | 5.00 V |
| 2 | 20 s | 15 | (unchanged) | Ramp | 5.00 → 7.50 V | 5.00 V |
| 3 | 100 ms | 5 | (unchanged) | Set | 2.50 V | 5.00 V |
| 4 | – | – | – | Stop | end state | end state |

The "AO sent" columns are shown only as an optional read-only preview (toggle "Show AO preview") for diagnostics. Decimal comma is accepted in cells; invalid cells are colored (error red / warning orange).

### 10.2.1 Scaling in the Sequence Path
- The sequence engine and players work exclusively in power supply units.
- Conversion to AO voltage happens in one place only: `core/scaling`, called by the DAQ service immediately before the write (same path as the front panel).
- Each sequence value is validated against the PS range (not the AO range) and the AO result is additionally clamped to the AO span as a final safety layer.
- If the settings (PS range or AO range) change after a sequence was created, the sequence is re-validated on load and before run; out-of-range values are highlighted and the run is blocked.

### 10.3 Validation Before Run
- All setpoints within the **power supply range** (V and A) from settings – not the AO range.
- Voltage present in every Set/Ramp step; current either empty or within range.
- A Ramp step must have a duration ≥ 50 ms.
- Durations numeric and ≥ **50 ms** (minimum step duration, configurable in `settings.json`).
- Steps below 50 ms are rejected with a clear message in the table (cell highlighted).
- Output configuration valid and device present.
- Sequence ends with Stop (or Stop is implied at end).

### 10.4 Execution – Timing Concept (Decision D-01)

**Mode in v1.0: Software timing.**

| Topic | Definition |
|---|---|
| Timing source | High-resolution monotonic clock in the sequence worker thread |
| Scheduling | Absolute schedule: each step's target time = sequence start time + cumulative durations. Errors do not accumulate (no drift). |
| Expected accuracy | Typically ±5–15 ms per step on Windows |
| Minimum step duration | 50 ms |
| Wait strategy | Coarse sleep until shortly before the target time, then short fine wait for the final milliseconds |
| Pause | Pause stores remaining time of the current step; Resume rebuilds the schedule from the resume time |
| Timing log | Planned vs. actual timestamp and deviation (ms) logged per step; summary (max/avg deviation) at sequence end |
| Player design | Engine builds the schedule and hands it to a player object. v1.0 implements `SoftwarePlayer` only. A `HardwarePlayer` can be added later without changing the editor, file format, or engine (see section 21). |

Other execution rules:
- Start: the first Set row's values are stored, then the output is switched ON automatically (a first Ramp row starts from the stored front-panel setpoints). Confirmed in v0.1.4.
- Wait strategy implemented as coarse interruptible sleeps (max. 50 ms) plus active waiting for the last `spin_wait_ms`; Windows timer resolution set to 1 ms during a run.
- Output switched OFF outside the sequence (front panel, error) ends the run with an error state.
- End of sequence, abort, and error handling: see 10.5.

---

### 10.4.1 Repeat Handling (Decision D-08)
- The whole sequence (all steps before Stop) is repeated according to the repeat count.
- The absolute schedule continues seamlessly across cycles (no drift between cycles).
- A Ramp step at the start of a cycle ramps from the last value of the previous cycle.
- The configured end state (10.5) is applied only after the **last** cycle; abort and error act immediately.
- Pause/Resume works within any cycle; the cycle counter is preserved.
- The timing log records the cycle number with each step.
- Partial (block) repetition is not part of v1.0 – see future enhancement FE-03 (section 21.3).

### 10.5 End-State Handling (Decision D-03)

| How the sequence ends | Reaction | Configurable |
|---|---|---|
| **Normal end** (Stop step or last step completed) | Apply the sequence end state: **Output OFF + safe value** (default) or **Hold last value** | Yes – per sequence |
| **User abort** (Stop button, window close, application exit) | Always **Output OFF + safe value** | No |
| **Error** (DAQmx error, device removed, validation failure during run) | Always **safe state**: safe value, Output OFF, error shown and logged | No |

**Hold last value behavior:**
- The last voltage and current setpoints stay applied; Output stays ON.
- Front-panel controls unlock and show the held values, so the operator can continue manually.
- Status bar shows "Sequence finished – output held".

**Output OFF + safe value behavior:**
- Both AO channels are written to the safe value; Output button switches OFF.
- Front-panel setpoints keep the last sequence values (not applied) so the operator can see where the sequence ended.

**Operator awareness:**
- Orange warning label in the Sequence window when "Hold last value" is selected.
- The end state applied is logged with the reason (normal end / abort / error).

## 11. Sequence File Format

**Location:** `<app root>/sequences/*.json`

| Field | Description |
|---|---|
| name | Sequence name |
| description | Optional text |
| created / modified | Timestamps |
| timing_mode | `"software"` (v1.0); reserved value `"hardware"` for future hybrid mode |
| end_state | `"output_off"` (default) or `"hold_last"` – applies to normal end only |
| repeat_count | Integer ≥ 0; 1 = run once (default), N = N times, 0 = until stopped |
| units | Fixed `"V"` / `"A"` – values are stored in power supply units, never AO volts |
| ps_range_at_save | PS voltage/current range at save time (informational, used to warn if settings changed) |
| steps[] | duration, unit (ms/s/min), voltage (mandatory for Set/Ramp), current (optional, `null` = unchanged), action (`set` / `ramp` / `stop`) |

---

## 12. DAQ Service Behavior

| Function | Description |
|---|---|
| Scan | Return list of devices, AO channels, and supported AO ranges |
| Configure | Create one AO voltage task per channel (or one task with two channels); task min/max = used AO span from config |
| Write | Accepts values in power supply units only (V, A). Converts PS setpoint → AO value via `core/scaling`, clamps to AO span, writes on-demand (software-timed). Single conversion point for front panel and sequences. |
| Safe state | Write safe value to both channels |
| Close | Write safe value, stop and clear tasks |
| Driver check | At startup verify NI-DAQmx driver is installed and report version |
| Simulation | Alternative backend with identical interface for testing without hardware (NI MAX simulated devices also supported) |
| Backend selection | `daq.backend` in settings.json; `auto` tries the `auto_order` of `config/presets/daq_backends.json` (nidaqmx, then simulation). Command-line `--simulate` forces the simulation. The UI must show clearly when the simulation is active (Phase 3/4). |

---

## 13. Safety and Error Handling

| Event | Reaction |
|---|---|
| Application start | AO channels set to safe value, Output OFF |
| Output OFF | AO channels set to safe value |
| Sequence normal end | Apply sequence end state (Output OFF + safe value, or Hold last value) – see 10.5 |
| Sequence user abort | Always Output OFF + safe value |
| Error during sequence | Always safe state, Output OFF |
| DAQmx error | Log, show in status bar, apply safe state (if possible), Output OFF |
| Device removed | Detect on next write, disable Output, show warning |
| Application exit / window close | Stop sequence, safe state, close tasks |
| Out-of-range input | Clamp and indicate in UI |
| Output OFF during front-panel ramp | Cancel ramp, apply safe value immediately |

---

## 14. Logging

- File: `<app root>/logs/ps_control_YYYYMMDD.log`.
- Content: startup info (version, driver version), config load/save, device scan results, Output ON/OFF, setpoint changes, sequence start/stop, errors.
- Sequence timing: cycle number, planned vs. actual time and deviation per step, plus max/avg deviation summary.
- Rotation by day; retention configurable (default 30 days).

---

## 15. Build and Deployment (Nuitka)

### 15.1 Build Configuration

| Topic | Decision |
|---|---|
| Mode | Standalone (folder) – recommended over onefile |
| Plugin | PySide6 plugin enabled |
| Included data | `ui/`, `resources/`, default `config/` template |
| Console | Disabled for release, enabled for debug build |
| Icon / version info | Windows icon (pending); `OriginalFilename` is `PS_Control.exe` and `FileVersion` comes from `version.txt` |
| Compiler | MSVC (Visual Studio Build Tools) preferred; MinGW64 as alternative |
| Script | Root-level `build_nuitka.bat` |
| Output | `build/dist/PS_Control/PS_Control.exe` and its standalone runtime files |

### 15.2 Why Standalone Instead of Onefile
- Onefile extracts to a temporary folder at every start → relative paths and writable `config/` become complicated, startup is slower.
- Standalone keeps `config/`, `sequences/`, and `logs/` writable next to the EXE.

### 15.3 Path Handling
- A single helper (`core/app_paths`) resolves the app root from the EXE location in the build and from the script location in development.
- Default config is shipped as a template; the real `settings.json` is created on first start so rebuilds never overwrite user settings.

### 15.4 Target PC Prerequisites
- NI-DAQmx driver installed (not bundled by Nuitka). Minimum version to be defined and checked at startup.
- No Python installation required.

### 15.5 Build Output Layout
- Run `build_nuitka.bat` from the project root or by double-clicking it.
- The script stages Nuitka output under `build/dist/staging`, copies the runtime configuration templates, UI forms, sequences, resources, and version file, then publishes the completed standalone directory as `build/dist/PS_Control`.
- The executable is `build/dist/PS_Control/PS_Control.exe`; `config/settings.json` and logs are created at runtime inside the package as needed.
- Windows file properties show `OriginalFilename` as `PS_Control.exe` and the file version from `version.txt` in four-part numeric format.
- The complete `build/dist/PS_Control` folder is the distributable unit. Generated build output is ignored by Git.

---

## 16. Testing Strategy

| Level | Content |
|---|---|
| Unit tests (pytest) | Scaling (forward/inverse, clamping, non-zero min, span presets 0–5 V / 0–10 V / custom), span validation against device range, config load/save/migration/defaults, sequence validation (incl. 50 ms minimum, PS-range checks, optional current), ramp interpolation, PS → AO conversion for sequence values, repeat count (1 / N / 0), schedule continuity across cycles, re-validation after range change, end-state selection per end reason (normal / abort / error), front-panel slew-rate ramp (rate, retarget, cancel on Output OFF), duration unit conversion, absolute schedule calculation |
| Integration (simulation) | DAQ service with simulated backend; full UI flow without hardware |
| UI file check | `.ui` files parsed as XML and compared with the widget spec modules (no Qt needed) |
| Qt window tests | Real windows on the offscreen Qt platform with the simulation backend; skipped when PySide6 is missing |
| Integration (NI MAX simulated device) | Real DAQmx calls against a simulated AO module |
| Hardware test | AO output measured with multimeter at 0 / 25 / 50 / 75 / 100 % of range |
| Timing test | Run reference sequences (50 ms, 100 ms, 1 s, 10 s steps); evaluate timing log: per-step deviation within ±15 ms typical, no cumulative drift |
| Build test | Nuitka standalone build launched on a clean PC with only NI-DAQmx installed |

---

## 17. Implementation Phases and Milestones

| Phase | Content | Exit Criteria |
|---|---|---|
| 1 – Foundation | Project skeleton, app_paths, logger, models, config manager, scaling + unit tests | Tests pass, config created on first start – **done** |
| 2 – DAQ Service | Driver check, scan, configure, write, safe state, simulation backend | Values written to simulated device – **done** |
| 3 – Settings Window | Scan UI, channel assignment, ranges, scaling preview, save | Config saved and reloaded correctly – **done** |
| 4 – Main Panel | Power supply UI, knobs, displays, Output ON/OFF, status bar | Manual control works end-to-end – **done** |
| 5 – Sequence | Editor, file load/save, engine, player abstraction, `SoftwarePlayer`, timing log, run monitor, lock of manual control | Example sequence runs; timing log within NFR-08 – **done (v0.1.4)** |
| 6 – Polish & Release | Themes, layout tuning, Nuitka build script, user manual, handoff TXT | Release build runs on clean target PC |

---

## 18. Deliverables

- Source code repository (structured as in section 5).
- `requirements.txt` with pinned versions.
- `build_nuitka.bat`.
- Release build (Nuitka standalone folder, zipped).
- User manual (operation, settings, sequences, timing limits, troubleshooting).
- Handoff TXT (architecture, decisions, open points, future enhancements, how to continue).
- Release notes.

---

## 19. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Software timing jitter on Windows (±5–15 ms) | Short steps less accurate | 50 ms minimum step, absolute scheduling (no drift), timing log for verification; hybrid hardware timing planned as future enhancement |
| NI-DAQmx driver missing / version mismatch | App cannot control hardware | Startup driver check with clear message; documented minimum version |
| Wrong scaling / range configuration | Overvoltage / overcurrent on DUT | Clamping, scaling preview, validation, safe state defaults |
| Nuitka build complexity (compiler, PySide6) | Delayed release | Set up build early (end of phase 1 smoke build) |
| Device renamed in NI MAX | Saved config invalid | Detect at startup, prompt to re-assign |
| UI freeze during hardware calls | Poor usability | Dedicated worker threads |

---

## 20. Decisions

### 20.1 Decisions

| ID | Question | Decision |
|---|---|---|
| D-01 (Q1) | Sequence timing accuracy | **Option A – Software timing** with absolute schedule and **50 ms minimum step duration**. Hybrid (software + hardware-timed) mode is kept as a future enhancement, to be evaluated when compatible NI hardware is used (section 21). |
| D-02 (Q2) | Step semantics and current | **A3 + B2** – "Set, then hold" with optional **Ramp** action; voltage mandatory, **current optional** (empty = unchanged). All values entered in **power supply units** (PS range); the tool applies the PS → AO scaling automatically (section 10.2.1). |
| D-03 (Q3) | End state after sequence | **Option C – configurable per sequence** (Output OFF + safe value as default, or Hold last value) for normal end. User abort always forces Output OFF + safe value; errors always force safe state. Warning shown when Hold last value is selected (section 10.5). |
| D-04 (Q4) | Measurement readback | **Option A – No readback in v1.0** (control only, AO hardware only). **Option B (optional AI readback)** is documented as future enhancement FE-02 (section 21.2); v1.0 reserves UI space and keeps the config extensible. |
| D-05 (Q5) | AO span | **Option B – user-selectable used AO span** per channel, presets 0–5 V / **0–10 V (default)** / Custom; validated against the DAQmx device range; DAQmx task limits set to the span (sections 7, 8.3). |
| D-06 (Q6) | PS minimum | **Option B – PS min and PS max editable** per channel, **PS min default 0**. Non-zero minimum handled by the existing slope/offset formulas; limits applied to front panel and sequences (sections 6.2, 7, 8.2). |
| D-07 (Q7) | Manual setpoint changes | **Option C – jump by default, optional slew-rate limit** (separate V/s and A/s, off by default; default rates 1 V/s and 5 A/s). Values applied on Enter/focus loss; Output ON starts from safe value; sequences use their own Set/Ramp steps (section 6.2). |
| D-08 (Q8) | Sequence repetition | **Option B – repeat whole sequence** with repeat count (1 = once default, N = N times, 0 = until stopped). Block repetition (Option C) documented as future enhancement FE-03 (sections 10.4.1, 21.3). |

All open questions (Q1–Q8) are decided. New questions arising during implementation will be added here.

---

## 21. Future Enhancements

### 21.1 FE-01 – Hybrid Sequence Timing (Software + Hardware-Timed)

| Topic | Description |
|---|---|
| Goal | Exact step timing (device clock, µs range) for short steps and smooth ramps |
| Concept | Per-sequence option "Precise timing (hardware)". The engine converts the schedule into a sample buffer (e.g., 1 kS/s) and a `HardwarePlayer` outputs it via a hardware-timed AO task. Software timing remains the default. |
| Precondition | Selected AO device supports hardware-timed (sample-clocked) analog output. Compatibility is detected during device scan; the option is shown only for compatible hardware. |
| Prepared in v1.0 | Player abstraction in `core/sequence_players`; `timing_mode` field in config and sequence files; scan structure ready for a capability flag |
| Open topics | Pause behavior with hardware buffer, buffer size/streaming for long sequences, run-monitor position estimation, regeneration settings |
| Trigger for evaluation | When compatible NI hardware is used and short-step accuracy below the software limits is required |

### 21.2 FE-02 – Measurement Readback (Optional AI Channels)

| Topic | Description |
|---|---|
| Goal | Show actual output voltage and current next to the setpoints, read from the power supply monitor outputs |
| Concept | In Settings, the user optionally assigns one AI channel for measured voltage and one for measured current. Unassigned = readback disabled. |
| Scaling | Same slope/offset method as the AO, inverse direction (AI volts → PS units), configured with the PS monitor range (e.g., 0–10 V = 0–20 V) |
| UI | "Measured" values in the reserved area of the front panel; optional indicator for constant-current (CC) mode when measured current reaches the limit |
| Hardware | NI AI module (e.g., NI 9215, NI 9205); detected during device scan |
| Prepared in v1.0 | Reserved front-panel layout area; config structure extensible with optional `voltage_readback` / `current_readback` groups (config_version migration); DAQ service interface allows adding an AI task |
| Possible extension | Logging of measured values to CSV during sequences (former Option C) |
| Trigger for evaluation | When the power supply in use provides monitor outputs and AI hardware is available in the setup |

### 21.3 FE-03 – Block Repetition (Repeat Part of a Sequence)

| Topic | Description |
|---|---|
| Goal | Repeat only a range of steps, e.g., warm-up once, then cycle steps 2–5 × 100 |
| Concept | New step action "Go to step X × N" (loop block); nested loops not planned initially |
| UI | Loop step in the table with target step and count; loop block visually indicated; run monitor shows block cycle and overall cycle |
| Validation | Target step must precede the loop step; count ≥ 1; no overlapping blocks |
| Prepared in v1.0 | Engine builds a flat schedule from the step list, so block expansion can be added in the schedule builder without changing the players; `action` field extensible with a `loop` value |
| Trigger for evaluation | When test profiles require a one-time preconditioning phase followed by repeated cycles |

---

## 22. Development Rules

These rules apply to every phase and every later modification.

### 22.1 Config-Driven Features
- Lists of similar things and user-changeable values live in data files read at run time – never hard-coded.
- PS Control catalogs: `config/presets/*.json` (AO span presets, step sizes, duration units, end states, sequence actions, scaling preview points); themes as folders `resources/themes/<id>/theme.json + style.qss`; default values in `config/defaults/settings.default.json`.
- Adding a preset, unit or theme = adding a JSON item or folder; no code change. New catalog files in `config/presets/` are loaded automatically.

### 22.2 Detailed Code Comments
- Every module, class and function has a docstring (purpose, inputs, return value).
- Every statement has a plain-English comment on the line before it. Applies to Python, tests and batch scripts (REM).

### 22.3 Release-Notes Workflow
After each modification is made and verified:
1. Raise the version in `version.txt` by one patch step (e.g. 0.1.0 → 0.1.1).
2. Write `release_notes/RELEASE_NOTES_v<version>.txt` listing only this modification's changes.
3. Add the new features to `HANDOFF.md`.
4. Update `PROJECT_OVERVIEW.txt`, including its button reference (every added, changed or removed button described or deleted).
5. Deliver the whole project as a ZIP.

## 23. Revision History

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-11-05 | E. Evangelio | Initial base plan (Nuitka packaging) |
| 0.2 | 2026-11-05 | E. Evangelio | Q1 decided (D-01): software timing, 50 ms minimum step; FR-14/15, NFR-08/09 added; player abstraction; timing log; new section 21 Future Enhancements (FE-01 hybrid timing) |
| 0.3 | 2026-11-05 | E. Evangelio | Q2 decided (D-02): Set/Ramp/Stop semantics, optional current, all values in PS units with automatic PS → AO scaling; FR-16–18; sections 8.3, 10.2, 10.2.1, 10.3, 11, 12, 16 updated |
| 0.4 | 2026-11-05 | E. Evangelio | Q3 decided (D-03): configurable end state per sequence (default Output OFF), abort/error always safe; FR-19–21; new section 10.5; sections 10.1, 11, 13, 16 updated |
| 0.5 | 2026-11-05 | E. Evangelio | Q4 decided (D-04): no readback in v1.0; optional AI readback added as future enhancement FE-02 (section 21.2); reserved front-panel area; scope section updated |
| 0.6 | 2026-11-05 | E. Evangelio | Q5 decided (D-05): user-selectable AO span per channel, default 0–10 V, presets and validation; FR-22; sections 7.1, 7.3, 8.3, 12, 16 updated |
| 0.7 | 2026-11-05 | E. Evangelio | Q6 decided (D-06): editable PS min/max, PS min default 0; FR-23; non-zero-min example; sections 6.2, 7.1, 7.3, 8.2 updated |
| 0.8 | 2026-11-05 | E. Evangelio | Q7 decided (D-07): jump by default, optional front-panel slew-rate limit (V/s, A/s); FR-24/25; sections 6.2, 7.1, 9, 13, 16 updated |
| 0.9 | 2026-11-05 | E. Evangelio | Q8 decided (D-08): whole-sequence repeat count (1 default, 0 = until stopped); FR-26; new section 10.4.1; future enhancement FE-03 block repetition (21.3); open-questions list closed; sections 10.1, 11, 14, 16, 20 updated |
| 0.10 | 2026-11-05 | E. Evangelio | Default slew rates confirmed: 1 V/s (voltage), 5 A/s (current); FR-24, sections 6.2, 7.1, 9.1 and D-07 updated |
| 1.0 | 2026-11-05 | E. Evangelio | Plan approved. Section 22 Development Rules added (config-driven features, detailed comments, release-notes workflow); folder structure updated (defaults, presets, theme folders, HANDOFF.md, PROJECT_OVERVIEW.txt, release_notes); `logging` group added to settings.json (retention_days, level) |
| 1.1 | 2026-11-05 | E. Evangelio | Phase 2 implemented: module table (daq_backend, nidaqmx_backend), `daq` settings group, backend selection, simulation device file, folder structure |
| 1.2 | 2026-11-05 | E. Evangelio | Phase 3 implemented: Settings window details (backend selector, messages, save/cancel behavior), run-time `.ui` loading, new modules (settings_editor, daq_bridge, bootstrap), UI test levels; test write deferred to Phase 4 |
| 1.3 | 2026-11-05 | E. Evangelio | Phase 4 implemented: front-panel details (Sent value, knob, start-up, exit), `core/front_panel` module, `safety.slew_interval_ms`, theme display/LED colors; Settings test-write item closed (covered by Output ON) |
| 1.4 | 2026-11-05 | E. Evangelio | Phase 5 implemented: sequence modules, start behavior confirmed (output ON automatically), wait strategy, new `sequence` settings, Sequence window toolbar/options details, example sequence file |
