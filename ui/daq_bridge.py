"""
ui/daq_bridge.py
================
Moves DAQ service events and command results from the DAQ worker thread
into the Qt main (UI) thread.

Why: Qt widgets may only be changed in the main thread. The DAQ service calls
its listeners and completes its Futures in the worker thread. A Qt signal
emitted in another thread is delivered to the receiver's thread through the
event queue (queued connection), so the slots run in the UI thread.

LabVIEW analogy:
    Like a user event that a helper loop fires and the UI event structure handles.
"""

# Import Future for type hints of command results.
from concurrent.futures import Future
# Import typing helpers.
from typing import Callable, Optional

# Import the Qt base object, signal and slot types.
from PySide6.QtCore import QObject, Signal, Slot

# Import the DAQ service type.
from core.daq_service import DaqService


class DaqBridge(QObject):
    """
    Qt-side adapter of the DAQ service.

    Signals:
        event_received(DaqEvent): Every service event, delivered in the UI thread.
    """

    # Service events (DaqEvent objects).
    event_received = Signal(object)
    # Internal: (callback, finished future) to run in the UI thread.
    _future_done = Signal(object, object)

    def __init__(self, service: Optional[DaqService], parent: Optional[QObject] = None) -> None:
        """
        Create the bridge and register it as service listener.

        Inputs:
            service (Optional[DaqService]): Service to observe (None = no hardware).
            parent (Optional[QObject]): Qt parent (keeps the bridge alive).

        Returns:
            None.
        """
        # Initialize the QObject part.
        super().__init__(parent)
        # Store the service.
        self.service = service
        # Run finished-future callbacks in this object's (UI) thread.
        self._future_done.connect(self._run_callback)
        # Register as listener when a service exists.
        if service is not None:
            # Emitting from the worker thread queues the event to the UI thread.
            service.add_listener(self.event_received.emit)

    def when_done(self, future: Future, callback: Callable[[Future], None]) -> None:
        """
        Call callback(future) in the UI thread when the future is finished.

        Never call future.result() directly in the UI thread - it would freeze
        the window while the hardware works.

        Inputs:
            future (Future): Future from a DaqService command.
            callback (Callable[[Future], None]): Function to run in the UI thread.

        Returns:
            None.
        """
        # The done-callback runs in the worker thread; forward through the signal.
        future.add_done_callback(lambda finished: self._future_done.emit(callback, finished))

    @Slot(object, object)
    def _run_callback(self, callback: Callable[[Future], None], future: Future) -> None:
        """
        Slot: run a forwarded callback in the UI thread.

        Inputs:
            callback (Callable[[Future], None]): Function to run.
            future (Future): The finished future.

        Returns:
            None.
        """
        # Call the function with the finished future.
        callback(future)
