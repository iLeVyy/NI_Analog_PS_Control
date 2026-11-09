"""
ui/app.py
=========
Starts the graphical user interface.

Since v0.1.3 (Phase 4) the power supply front panel is the main window; it
opens the Settings window with its Settings button (and automatically on
the first start).
"""

# Import sys for the Qt command-line arguments.
import sys
# Import typing helpers.
from typing import Optional

# Import the single-shot timer to run start-up actions after the window is shown.
from PySide6.QtCore import QTimer
# Import the Qt application class.
from PySide6.QtWidgets import QApplication

# Import the start-up context.
from core.bootstrap import AppContext
# Import the "settings.json was created" status constant.
from core.config_manager import STATUS_CREATED
# Import the DAQ service type.
from core.daq_service import DaqService
# Import the logger factory.
from core.logger import get_logger
# Import the front panel.
from ui.main_panel import MainPanel


def run_gui(ctx: AppContext, service: Optional[DaqService]) -> int:
    """
    Create the Qt application, show the main window and run the event loop.

    Inputs:
        ctx (AppContext): Start-up context.
        service (Optional[DaqService]): Started DAQ service (None = no backend).

    Returns:
        int: Exit code of the Qt event loop.
    """
    # Logger for the GUI start.
    log = get_logger("app")
    # Reuse an existing Qt application (tests) or create one.
    app = QApplication.instance() or QApplication(sys.argv)
    # Application name (used by Qt for settings / dialogs).
    app.setApplicationName("PS Control")
    # First start = settings.json was just created.
    first_start = ctx.load_result.status == STATUS_CREATED
    # Create the front panel (main window).
    window = MainPanel(ctx, service, first_start=first_start)
    # Show it.
    window.show()
    # Start-up actions once the event loop runs (set up channels, maybe open Settings).
    QTimer.singleShot(0, window.start)
    # Log the start of the event loop.
    log.info("GUI started")
    # Run until the window is closed.
    return app.exec()
