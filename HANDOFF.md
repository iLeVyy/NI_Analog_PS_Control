# HANDOFF – PS Control

Developer handoff for continuing the project (people or AI sessions).
Read this file and `docs/Project_Plan.md` (v1.0, approved) first.

| Item | Value |
|---|---|
| Current version | 0.1.7 (see `version.txt`) |
| Current phase | Phase 5 – Sequence window + engine: **done** |
| Next phase | Phase 6 – Polish & Release (themes, Nuitka build, user manual) |
| Project folder (user) | *to be filled in by the user* |
| Python | 3.11+ (tested with 3.12 in the creation environment) |
| NI-DAQmx driver | required on target PC from Phase 2 – version to be recorded |

## 1. How to continue
1. Upload the latest project ZIP into the new session.
2. Point the assistant to this file and `docs/Project_Plan.md`.
3. Follow the Development Rules (Project_Plan.md section 22) for every change:
   config-driven features, detailed comments, release-notes workflow.

## 2. Setup and run (Windows)
1. `setup_venv.bat` – creates `.venv` and installs `requirements.txt`.
2. `run_tests.bat` – runs all tests (without PySide6 the Qt window tests are skipped).
3. `run_app.bat` – starts the GUI (front panel; Settings via its button).
4. `run_console.bat` – console check (scan + configure + close), same as `python main.py --console`.
5. `--simulate` (GUI or console) forces the simulation backend.

## 3. Architecture status

| Module | Status | Notes |
|---|---|---|
| `core/app_paths.py` | Done | Root detection: Nuitka (`__compiled__`) → EXE folder; frozen → EXE folder; dev → folder of `main.py` |
| `core/logger.py` | Done | Daily file, retention from config |
| `core/activity_trace.py` | Done | Thread-safe, in-memory trace, latest 50 bounded entries; independent of persistent log threshold |
| `core/models.py` | Done | Data classes; defaults are NOT in code, they come from the template |
| `core/catalogs.py` | Done | `load_all_catalogs()` loads every `config/presets/*.json`; `discover_themes()` scans theme folders |
| `core/config_manager.py` | Done | Load/create/repair/migrate/save; `MIGRATIONS` dict ready for future structure versions |
| `core/scaling.py` | Done | Single PS → AO conversion point |
| `core/daq_service.py` | Done | Worker thread + command queue; public methods return `Future`; listeners are called in the worker thread |
| `core/daq_backend.py` | Done | `DaqBackend` interface, `create_backend()` driven by `daq_backends.json`, `check_min_driver_version()` |
| `core/nidaqmx_backend.py` | Done (fake-driver tested) | Lazy `nidaqmx` import; verify on hardware |
| `core/simulation.py` | Done | Devices from `config/simulation/devices.json`; test helpers `fail_next_writes`, `remove_device()`, `last_values`, `history` |
| `core/bootstrap.py` | Done | `startup()` → `AppContext`; `create_daq_service()` |
| `core/settings_editor.py` | Done | All Settings rules without Qt; working copy, dirty check, restore defaults |
| `ui/settings_window.ui` | Done | Qt Designer form, loaded at run time (QUiLoader) |
| `ui/settings_widgets.py` | Done | Required widget names/classes; checked by `tests/test_ui_files.py` without Qt |
| `ui/ui_loader.py` | Done | `load_ui()`, `find_widgets()` (fails with a list of missing widgets) |
| `ui/daq_bridge.py` | Done | Worker thread → UI thread (`event_received`, `when_done(future, cb)`) |
| `ui/settings_window.py` | Done | Opened modal (non-blocking `show()`) from the front panel |
| `ui/app.py` | Done | `run_gui()` → `MainPanel`, `start()` via `QTimer.singleShot(0)` |
| `core/front_panel.py` | Done | `ChannelControl` (range, steps, knob mapping, display), `SlewRamp` |
| `ui/main_panel.ui` / `ui/main_panel_widgets.py` | Done | Front-panel form + widget spec |
| `ui/main_panel.py` | Done | Main window; ramps via QTimer; output state set optimistically on request, corrected by `EVT_OUTPUT` / command result |
| `ui/log_window.py` | Done | Nonmodal, read-only, polled view of the in-memory activity trace |
| `core/sequence_model.py` | Done | `Sequence`, `SequenceStep`, `load_sequence()`, `save_sequence()` (format_version 1) |
| `core/sequence_engine.py` | Done | `validate_sequence()`, `build_plan()`, `preview_values()`, `SequenceRunner` (thread, pause/resume/stop, end states, timing log, `status()` snapshot) |
| `core/sequence_players.py` | Done | `Clock`, `RunControl`, `SequencePlayer` interface, `SoftwarePlayer`; `PLAYERS` table by timing mode (FE-01 adds "hardware") |
| `core/sequence_editor.py` | Done | Qt-free editing: rows, cells, options, files, headers, range warning |
| `ui/sequence_window.ui` / `ui/sequence_widgets.py` | Done | Form + widget spec + table column spec |
| `ui/sequence_window.py` | Done | Non-modal; polls `runner.status()` with a QTimer; signals `running_changed`, `values_changed` |
| `tests/fake_clock.py` | Done | Deterministic clock with scheduled actions for timing tests |
| `core/sequence_engine.py`, `core/sequence_players.py` | Phase 5 | Software player only (D-01) |

## 4. Features (cumulative)

### v0.1.0 – Phase 1 Foundation
- Relative path handling for development and Nuitka builds.
- Daily logging with configurable level and retention.
- `settings.json` lifecycle: created on first start, invalid files backed up to `.bak`, missing keys completed from `config/defaults/settings.default.json`, migration framework, atomic save.
- Validation: PS range, AO span vs. device range (D-05), negative span warning, duplicate channel, slew rates > 0, minimum step > 0, unassigned channel warnings.
- Linear scaling with clamping and non-zero PS min (D-06); preview table.
- Config-driven catalogs: AO span presets, step sizes, duration units, end states, sequence actions, scaling preview points; theme folders.

### v0.1.1 – Phase 2 DAQ Service
- DAQ service with worker thread, command queue and events (scan, configured, output, written, safe_state, error, closed).
- Configure checks settings and hardware (device present, channel on device, span inside a device range) and opens one on-demand AO task per channel (task limits = used span).
- Setpoints in PS units, clamped to the PS range, converted only via `core/scaling`.
- Output ON writes setpoints; Output OFF, errors and configure write the safe value; close writes the exit value.
- Backends: NI-DAQmx (real hardware) and simulation; selection `daq.backend` = auto / nidaqmx / simulation; `--simulate` option.
- Minimum driver version check (`daq.min_driver_version`).

### v0.1.2 – Phase 3 Settings Window
- Settings window (main window for now): DAQ hardware (backend selector, SIMULATION indicator, scan, device table), voltage/current channel groups (channel, device range, span preset, AO min/max for Custom, PS min/max, slope/offset, preview table), safety (safe values, slew limit + rates), live validation, Restore Defaults / Cancel / Save.
- Save validates (both channels required, hardware vs. last scan), writes `settings.json`, configures the DAQ service asynchronously, shows the result.
- Unsaved-changes question on Cancel / close / Esc.
- Qt Designer layout loaded at run time; widget names checked in tests.
- GUI is the default start mode; `--console` for the console check.

### v0.1.3 – Phase 4 Front Panel
- Front panel is the main window: voltage / current sections (7-segment display, "Sent" value, range, knob, entry, step buttons, step size from `step_sizes.json`), Output button with LED, toolbar (Settings, Sequence, Log), status bar (channels, AO volts, messages), hidden Measured area (FE-02).
- Output OFF stores, Output ON writes; jump by default, slew-rate ramps when enabled (Output ON from safe value, retarget from actual, OFF cancels).
- Start-up: configure automatically; first start opens Settings; missing device offers Settings.
- Settings → Save refreshes the panel; the configured theme is loaded at startup; step sizes and window geometry are saved on exit; confirmation when closing with output ON.
- New config `safety.slew_interval_ms` (50 ms); theme colors `display_background`, `display_text`, `led_on`, `led_off`.

### v0.1.4 – Phase 5 Sequence Window + Engine
- Sequence window: New / Load / Save / Save As (JSON in `sequences/`, last file reopened), name, description, end state with warning, repeat (0 = until stopped), AO preview, step table (duration, unit, voltage, optional current, Set / Ramp / Stop), row tools, live validation with cell colors, run controls and monitor.
- Engine: validation (50 ms min, PS ranges, units, actions), absolute-schedule software player, ramps in 50 ms steps, repeat, pause/resume, end states (normal / stop / error), timing log, Windows 1 ms timer during runs.
- Front panel locked during runs, displays follow the sequence, Output OFF stops the sequence, Settings disabled during runs.
- New settings: `sequence.ramp_interval_ms`, `spin_wait_ms`, `new_step_duration`, `status_poll_ms`.
- Example file `sequences/Example_from_request.json`.

### v0.1.7 – Activity Log, manual setpoints, and windowless build
- Log opens a nonmodal, in-memory session trace. The most recent 50 bounded
  entries are retained; the trace itself is not written to disk. Existing
  daily file logging remains in place.
- The last manually chosen setpoints are saved on normal close independently
  of sequence values. Startup restores valid values with output OFF; invalid
  or out-of-range values use the channel minimum.
- The main window's close control replaces the former Exit button. The EXE
  runs without a console, displays startup errors in a dialog, and has a
  circular power-supply icon. Source-mode console checks remain available.

## 5. Config-driven data – where to change things

| What | File / folder |
|---|---|
| Default settings | `config/defaults/settings.default.json` |
| AO span presets | `config/presets/ao_spans.json` |
| Front-panel step sizes | `config/presets/step_sizes.json` |
| Duration units | `config/presets/duration_units.json` |
| Sequence end states | `config/presets/end_states.json` |
| Sequence actions | `config/presets/sequence_actions.json` |
| Scaling preview points | `config/presets/scaling_preview.json` |
| DAQ backends and auto order | `config/presets/daq_backends.json` |
| Simulated devices | `config/simulation/devices.json` |
| DAQ settings (backend, min driver, timeout) | `settings.json` → `daq` |
| Settings window layout | `ui/settings_window.ui` (Qt Designer; keep the object names listed in `ui/settings_widgets.py`) |
| Message colors | `resources/themes/<id>/theme.json` → `colors` (error, warning, ok) |
| Display / LED colors | `theme.json` → `display_background`, `display_text`, `led_on`, `led_off` |
| Ramp step interval | `settings.json` → `safety.slew_interval_ms` (not shown in Settings) |
| Front-panel layout | `ui/main_panel.ui` (keep the names in `ui/main_panel_widgets.py`) |
| Sequence window layout | `ui/sequence_window.ui` (names in `ui/sequence_widgets.py`) |
| Sequence timing values | `settings.json` → `sequence` (min step, ramp interval, spin wait, new step duration, monitor refresh) |
| Duration units / actions / end states | `config/presets/duration_units.json`, `sequence_actions.json`, `end_states.json` |
| Example sequences | `sequences/*.json` |
| Themes | `resources/themes/<id>/theme.json` + `style.qss` |

New catalog files in `config/presets/` are loaded automatically.

## 6. Decisions (summary)
D-01 software timing, 50 ms min · D-02 Set/Ramp/Stop, current optional, PS units · D-03 end state per sequence, abort/error always safe · D-04 no readback (FE-02 later) · D-05 user span, default 0–10 V · D-06 PS min/max editable, min 0 · D-07 jump + optional slew limit 1 V/s, 5 A/s · D-08 whole-sequence repeat (FE-03 block repeat later).

## 7. Open points
- Pin package versions after first install on the build PC.
- The v0.1.7 standalone build and an isolated simulated GUI startup/shutdown
  were verified on the build PC; verify the package on a clean target PC before release.
- Test the NI-DAQmx backend on real hardware (scan, configure, write, unplug while ON) and record the driver version.
- Decide whether test benches use `daq.backend = "nidaqmx"` instead of `auto` (prevents an unnoticed simulation fallback).
- User project folder path to be added in the table above.

## 8. Notes for Phase 6 (Polish & Release)
- Fill `resources/themes/<id>/style.qss` (dark industrial: dark blue-gray, light linear buttons with blue accent, blue primary Output button); `MainPanel.apply_theme()` already loads the file of the active theme for the whole application.
- Run the root-level `build_nuitka.bat`; it publishes `build/dist/PS_Control/PS_Control.exe` with `ui/*.ui`, `config/defaults`, `config/presets`, `config/simulation`, `resources`, `sequences`, and `version.txt` alongside it. Confirm `config/settings.json` is created on first start.
- The EXE version resource uses `OriginalFilename = PS_Control.exe` and the four-part `FileVersion` from `version.txt`.
- The EXE uses `--windows-console-mode=disable` and `resources/icons/ps_control.ico`; source-mode console checks remain available.
- Write the user manual (from `PROJECT_OVERVIEW.txt` + screenshots in `docs/screenshots`).
- Hardware acceptance test on a real AO module (scan, Settings save, Output ON/OFF, ramps, sequence with timing log, unplug during a run).
- Pin package versions in `requirements.txt`.

## 9. Phase 6 scope (next)
Theme stylesheets, layout tuning on Windows / small screens, user manual, final handoff, clean-target validation and hardware acceptance test checklist.

## 10. Development environment note
The Qt tests ran offscreen on Linux (PySide6 6.12). On Windows they run normally with `run_tests.bat`; no display setting is needed (the tests set `QT_QPA_PLATFORM=offscreen` themselves).
