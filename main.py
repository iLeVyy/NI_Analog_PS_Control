"""
main.py
=======
Entry point of PS Control.

Start modes:
    python main.py              GUI (power supply front panel)
    python main.py --console    console check without GUI: prints the
                                configuration, scans devices, tries to set
                                up the AO channels, closes again
    --simulate                  (both modes) force the simulation backend

Steps:
    1. core.bootstrap.startup(): paths, version, logging, settings.json,
       catalogs, themes, default template.
    2. core.bootstrap.create_daq_service(): backend + started DAQ service.
    3. Run the GUI or the console check.
    4. Close the DAQ service (writes the exit safe value).

LabVIEW analogy:
    The launcher / Main.vi that initializes and starts the modules.
"""

# Import sys for arguments and the exit code.
import sys

# Import the start-up helpers.
from core.bootstrap import AppContext, StartupError, create_daq_service, startup
# Import the scale builder for the console example.
from core.config_manager import channel_scale
# Import the logger factory.
from core.logger import get_logger


def _windowless_build() -> bool:
    """Return whether this is a packaged Windows GUI build."""
    return sys.platform == "win32" and ("__compiled__" in globals() or getattr(sys, "frozen", False))


def _report_startup_error(message: str) -> None:
    """Report a startup failure without depending on a console or Qt."""
    if _windowless_build():
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "PS Control - startup error", 0x10)
    else:
        print(f"Cannot start: {message}")


def print_channel(name: str, channel) -> None:
    """
    Print one channel summary line to the console.

    Inputs:
        name (str): Display name ("Voltage" or "Current").
        channel (ChannelConfig): Channel configuration.

    Returns:
        None.
    """
    # Show the assigned channel, or a hint when none is assigned.
    assigned = channel.physical_channel or "(not assigned)"
    # Print range, span and calculated scaling in one line.
    print(f"  {name:<8} ch={assigned:<18} PS {channel.ps_min:g}..{channel.ps_max:g} {channel.unit}"
          f"  AO {channel.ao_min:g}..{channel.ao_max:g} V"
          f"  slope={channel.slope:.6g} offset={channel.offset:.6g}")


def run_console(ctx: AppContext, force_simulation: bool) -> int:
    """
    Console check: print the configuration, scan, try to configure, close.

    Inputs:
        ctx (AppContext): Start-up context.
        force_simulation (bool): True = simulation backend.

    Returns:
        int: Exit code (0).
    """
    # Short name for the load result.
    result = ctx.load_result
    # Print the summary header.
    print(f"\nPS Control {ctx.version} - console check")
    # Print the root folder.
    print(f"  Root       : {ctx.paths.root}")
    # Print the load status.
    print(f"  Config     : {result.status} ({ctx.paths.settings_file})")
    # Print the voltage channel.
    print_channel("Voltage", result.config.voltage_channel)
    # Print the current channel.
    print_channel("Current", result.config.current_channel)
    # Scale of the voltage channel for an example.
    scale = channel_scale(result.config.voltage_channel)
    # Only print the example for a valid scale.
    if scale.is_valid:
        # Middle of the PS range.
        mid = (scale.ps_min + scale.ps_max) / 2
        # Print the conversion example.
        print(f"  Example    : {mid:g} V set -> {scale.to_ao(mid):g} V on AO")
    # Print the catalog names.
    print(f"  Catalogs   : {', '.join(ctx.catalogs)}")
    # Print the theme ids.
    print(f"  Themes     : {', '.join(ctx.themes)}")
    # Print every validation error.
    for message in result.errors:
        # Error line.
        print(f"  ERROR      : {message}")
    # Print every validation warning.
    for message in result.warnings:
        # Warning line.
        print(f"  WARNING    : {message}")
    # Create the DAQ service.
    service, driver = create_daq_service(ctx, force_simulation)
    # No backend usable.
    if service is None:
        # Print the reason.
        print(f"  DAQ        : {driver.message}")
        # Done.
        return 0
    # Print the backend and driver.
    print(f"  DAQ backend: {service.state.backend_id} - {driver.message}")
    # Timeout for one command.
    timeout = result.config.daq.write_timeout_s
    # Scan and configure, then always close.
    try:
        # Only with a usable driver.
        if driver.available:
            # Scan devices.
            for device in service.scan().result(timeout):
                # Device line.
                print(f"  Device     : {device.name:<12} {device.product_type:<22} "
                      f"AO: {', '.join(device.ao_channels)}  ranges: {device.ao_ranges}")
            # Try to configure.
            ok, errors, _warnings = service.configure(result.config).result(timeout * 5)
            # Print the result.
            print(f"  Configure  : {'OK - output OFF, safe value applied' if ok else 'not configured'}")
            # Print the reasons.
            for message in errors:
                # Reason line.
                print(f"    - {message}")
    # Always release the hardware.
    finally:
        # Exit safe value and close tasks.
        service.close()
    # Success.
    return 0


def run_gui_mode(ctx: AppContext, force_simulation: bool) -> int:
    """
    Start the GUI; fall back to the console check when PySide6 is missing.

    Inputs:
        ctx (AppContext): Start-up context.
        force_simulation (bool): True = simulation backend.

    Returns:
        int: Exit code.
    """
    # Logger for this step.
    log = get_logger("main")
    # Import the GUI only here, so the console mode works without PySide6.
    try:
        # GUI entry function.
        from ui.app import run_gui
    # PySide6 not installed.
    except ImportError as exc:
        if _windowless_build():
            raise RuntimeError(f"GUI is not available: {exc}") from exc
        # Log and explain.
        log.error("GUI not available (%s) - running the console check instead", exc)
        # Console fallback.
        return run_console(ctx, force_simulation)
    # Create the DAQ service.
    service, _driver = create_daq_service(ctx, force_simulation)
    # Run the GUI and always close the service afterwards.
    try:
        # Event loop.
        return run_gui(ctx, service)
    # Release the hardware.
    finally:
        # Close when a service exists.
        if service is not None:
            # Exit safe value and close tasks.
            service.close()


def main() -> int:
    """
    Parse the arguments and run the selected mode.

    Returns:
        int: Exit code (0 = OK, 1 = start-up error).
    """
    # Command-line arguments without the program name.
    args = sys.argv[1:]
    # Simulation forced?
    force_simulation = "--simulate" in args
    # Run the common start-up.
    try:
        # Context with config and catalogs.
        ctx = startup()
    # Broken installation.
    except StartupError as exc:
        # Show the reason even when the executable has no console.
        _report_startup_error(str(exc))
        # Error exit code.
        return 1
    # Console mode.
    if "--console" in args:
        # Run the console check.
        exit_code = run_console(ctx, force_simulation)
    # GUI mode (default).
    else:
        # Run the GUI.
        exit_code = run_gui_mode(ctx, force_simulation)
    # Log the end.
    get_logger("main").info("PS Control finished (exit code %d)", exit_code)
    # Return the exit code.
    return exit_code


# Run main() only when this file is started directly (not when imported).
if __name__ == "__main__":
    if _windowless_build():
        try:
            exit_code = main()
        except Exception as exc:
            get_logger("main").exception("Application failed: %s", exc)
            _report_startup_error(f"PS Control could not start or continue:\n{exc}\n\n"
                                  "See the application log for details.")
            exit_code = 1
        sys.exit(exit_code)
    sys.exit(main())
